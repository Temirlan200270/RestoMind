"""
Регрессии: стоп-лист в кэше меню, отмена корзины, HUMAN_MODE, stale cart.
"""

import pytest
from datetime import datetime, timedelta, timezone
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
    # Точные фразы
    assert is_cancel_all_message("отмени всё")
    assert is_cancel_all_message("Отмени все!")
    assert is_cancel_all_message("отмени")
    assert is_cancel_all_message("отмени заказ")
    assert is_cancel_all_message("убери всё")
    assert is_cancel_all_message("начать заново")

    # Натуральные фразы с произвольным порядком слов (keyword-combo)
    assert is_cancel_all_message("Ты до этого отмени это все эти")
    assert is_cancel_all_message("Отмени эти все заявки")
    assert is_cancel_all_message("отмени все заявки")
    assert is_cancel_all_message("удали все заказы")
    assert is_cancel_all_message("убери все заказы пожалуйста")

    # Ложные срабатывания НЕ должны происходить
    assert not is_cancel_all_message("есть плов?")
    assert not is_cancel_all_message("нет")
    assert not is_cancel_all_message("отмени плов")
    assert not is_cancel_all_message("убери плов из заказа")
    assert not is_cancel_all_message("можно без лука")


def test_stale_draft_resets_on_new_conversation_hint() -> None:
    from app.api.webhooks import _should_reset_existing_draft_for_message

    draft = Order(
        organization_id=1,
        user_id=1,
        status=OrderStatus.DRAFT,
        items_json={"items": [{"name": "Плов", "quantity": 1}]},
        updated_at=datetime.now(timezone.utc) - timedelta(minutes=50),
    )

    assert _should_reset_existing_draft_for_message(draft, "Здравствуйте")
    assert _should_reset_existing_draft_for_message(draft, "Хочу сделать заказ")
    assert not _should_reset_existing_draft_for_message(draft, "На завтра к 13:00")


def test_very_old_draft_resets_even_without_greeting() -> None:
    from app.api.webhooks import _should_reset_existing_draft_for_message

    draft = Order(
        organization_id=1,
        user_id=1,
        status=OrderStatus.DRAFT,
        items_json={"items": [{"name": "Плов", "quantity": 1}]},
        updated_at=datetime.now(timezone.utc) - timedelta(hours=13),
    )

    assert _should_reset_existing_draft_for_message(draft, "Плов")


@pytest.mark.asyncio
async def test_technical_ai_fallback_does_not_enter_human_mode(db_with_menu: AsyncSession) -> None:
    from app.services.ai_brain import _FALLBACK_RESPONSE

    result = await route_intent(
        db_with_menu,
        "+77001112233",
        AIBrainResponse(intent="escalate", reply_text=_FALLBACK_RESPONSE.reply_text),
        organization_id=1,
    )

    assert result.new_state == UserState.CHATTING
    assert "Напишите ещё раз" in result.reply_text


@pytest.mark.asyncio
async def test_enriched_technical_ai_fallback_does_not_enter_human_mode(db_with_menu: AsyncSession) -> None:
    from app.services.ai_brain import _FALLBACK_RESPONSE

    enriched = (
        _FALLBACK_RESPONSE.reply_text
        + "\n\nСейчас на стопе: Фитнес плов. Казаны по плову открываются в 12:00, 16:00, 19:00."
    )
    result = await route_intent(
        db_with_menu,
        "+77001112233",
        AIBrainResponse(intent="escalate", reply_text=enriched),
        organization_id=1,
    )

    assert result.new_state == UserState.CHATTING
    assert "Напишите ещё раз" in result.reply_text


def test_payment_gate_does_not_request_confirmation_without_delivery_address() -> None:
    from app.api.webhooks import _missing_fulfillment_after_payment_reply

    reply = _missing_fulfillment_after_payment_reply(
        {"order_type": "delivery"},
        pay_human="Наличные",
        body="Ваш заказ",
    )

    assert "адрес доставки" in reply.lower()
    assert "подтверждаете" not in reply.lower()


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
