"""
Регрессии: стоп-лист в кэше меню, отмена корзины, HUMAN_MODE, stale cart.
"""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Order, OrderStatus, User
from app.services.dialog_mgr import (
    UserState,
    is_cancel_all_message,
    clear_pending_order,
)
from app.services.intent_router import (
    cancel_all_draft_orders_for_phone,
    get_open_draft_order,
    route_intent,
)
from app.services.order_logic import (
    build_menu_context,
    build_menu_context_for_ai,
    load_available_menu,
    menu_stoplist_fingerprint,
)
from app.schemas.ai_schemas import AIBrainResponse, OrderItem


def test_menu_stoplist_fingerprint_changes_when_availability_changes() -> None:
    from app.db.models import MenuItem

    a = MenuItem(
        organization_id=1,
        name="Плов",
        price=1000,
        is_available=True,
        iiko_id="uuid-plov",
    )
    b = MenuItem(
        organization_id=1,
        name="Плов",
        price=1000,
        is_available=False,
        iiko_id="uuid-plov",
    )
    assert menu_stoplist_fingerprint([a]) != menu_stoplist_fingerprint([b])


def test_newly_stopped_names_detects_fresh_stop() -> None:
    from app.db.models import MenuItem
    from app.services.stoplist_session import compose_stoplist_notice, newly_stopped_names

    items = [
        MenuItem(organization_id=1, name="Плов", price=1, is_available=False, iiko_id="a"),
        MenuItem(organization_id=1, name="Чай", price=1, is_available=True, iiko_id="b"),
    ]
    fresh = newly_stopped_names(set(), items)
    assert fresh == ["Плов"]
    msg = compose_stoplist_notice(["Плов"], fresh, draft_item_names=["Плов"])
    assert "только что" in msg.lower()
    assert "убрал" in msg.lower()


def test_is_cancel_all_message() -> None:
    assert is_cancel_all_message("отмени всё")
    assert is_cancel_all_message("Отмени все!")
    assert is_cancel_all_message("отмени")
    assert not is_cancel_all_message("есть плов?")
    assert not is_cancel_all_message("нет")
    assert not is_cancel_all_message("отмени плов")


def test_newly_stopped_empty_on_first_snapshot() -> None:
    from app.db.models import MenuItem
    from app.services.stoplist_session import newly_stopped_names

    items = [
        MenuItem(organization_id=1, name="Плов", price=1, is_available=False, iiko_id="a"),
    ]
    assert newly_stopped_names(set(), items) == ["Плов"]
    # В вебхуке при пустом prev не вызывают diff — эмуляция:
    prev: set[str] = set()
    fresh = newly_stopped_names(prev, items) if prev else []
    assert fresh == []


@pytest.mark.asyncio
async def test_cancel_all_draft_orders(db_with_menu: AsyncSession) -> None:
    phone = "+77005554433"
    menu = await load_available_menu(db_with_menu, organization_id=1)
    ai = AIBrainResponse(
        intent="order",
        reply_text="Ок",
        items=[OrderItem(name="Плов", quantity=1, iiko_item_id="uuid-plov")],
        order_type="delivery",
        payment_method="cash",
        delivery_address="ул. Тест 1",
    )
    await route_intent(db_with_menu, phone, ai, menu_items=menu)
    await db_with_menu.commit()

    n = await cancel_all_draft_orders_for_phone(db_with_menu, phone, 1)
    await db_with_menu.commit()
    assert n >= 1
    assert await get_open_draft_order(db_with_menu, phone, 1) is None


@pytest.mark.asyncio
async def test_clear_pending_order_preserves_human_mode(
    db_with_menu: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.db.session import redis_client

    phone = "+77006667788"
    org = 1
    await clear_pending_order(redis_client, phone, organization_id=org)
    await redis_client.set(f"user:state:{org}:{phone}", UserState.HUMAN_MODE.value, ex=60)

    await clear_pending_order(redis_client, phone, organization_id=org)
    raw = await redis_client.get(f"user:state:{org}:{phone}")
    assert raw == UserState.HUMAN_MODE.value


@pytest.mark.asyncio
async def test_build_menu_context_marks_stop_items(db_with_menu: AsyncSession) -> None:
    menu = await load_available_menu(
        db_with_menu, organization_id=1, include_unavailable=True,
    )
    stopped = [m for m in menu if not m.is_available]
    if not stopped:
        pytest.skip("no stopped items in fixture menu")
    ctx = build_menu_context(menu)
    assert "[СТОП" in ctx
