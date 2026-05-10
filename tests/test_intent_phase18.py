"""
Phase 18: merge order_actions с существующим DRAFT (intent_router).
"""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Order, OrderStatus, User
from app.schemas.ai_schemas import AIBrainResponse, OrderAction, OrderItem
from app.services.intent_router import get_open_draft_order, route_intent
from app.services.order_logic import load_available_menu


def _fee_kinds(items_json: dict) -> list[str]:
    fl = items_json.get("fee_lines") or []
    if not isinstance(fl, list):
        return []
    out = []
    for x in fl:
        if isinstance(x, dict) and x.get("kind"):
            out.append(str(x["kind"]))
    return out


@pytest.mark.asyncio
async def test_route_intent_merge_updates_same_draft(db_with_menu: AsyncSession) -> None:
    phone = "+77009998877"
    menu = await load_available_menu(db_with_menu, organization_id=1)

    ai1 = AIBrainResponse(
        intent="order",
        reply_text="Принял",
        items=[OrderItem(name="Плов", quantity=1, iiko_item_id="uuid-plov")],
        order_type="delivery",
        payment_method="cash",
        delivery_address="ул. Абая 1",
    )
    r1 = await route_intent(db_with_menu, phone, ai1, menu_items=menu)
    await db_with_menu.commit()
    assert r1.pending_order_id is not None
    oid = r1.pending_order_id

    ai2 = AIBrainResponse(
        intent="order",
        reply_text="Добавил напиток",
        items=[],
        order_actions=[OrderAction(item_id="Капучино", action="add", quantity=2)],
        order_type="delivery",
        payment_method="cash",
    )
    r2 = await route_intent(db_with_menu, phone, ai2, menu_items=menu)
    await db_with_menu.commit()

    assert r2.pending_order_id == oid
    order = await db_with_menu.get(Order, oid)
    assert order is not None
    assert order.status == OrderStatus.DRAFT
    names = {str(x.get("name")) for x in (order.items_json or {}).get("items", []) if isinstance(x, dict)}
    assert "Плов" in names
    assert "Капучино" in names


@pytest.mark.asyncio
async def test_route_intent_saves_upsell_in_order_meta(db_with_menu: AsyncSession) -> None:
    """Рекомендация бота попадает в order_meta для админки."""
    phone = "+77008887766"
    menu = await load_available_menu(db_with_menu, organization_id=1)
    ai = AIBrainResponse(
        intent="order",
        reply_text="Добавил плов. К нему советую ачичук.",
        items=[OrderItem(name="Плов", quantity=1, iiko_item_id="uuid-plov")],
        order_type="delivery",
        payment_method="cash",
        delivery_address="ул. Тест 1",
        is_recommendation=True,
        upsell_offered="Ачичук",
        upsell_reasoning="Свежие овощи к насыщенному плову",
    )
    r = await route_intent(db_with_menu, phone, ai, menu_items=menu)
    await db_with_menu.commit()
    assert r.pending_order_id is not None
    order = await db_with_menu.get(Order, r.pending_order_id)
    assert order is not None
    meta = (order.items_json or {}).get("order_meta") or {}
    assert isinstance(meta, dict)
    rec = meta.get("recommendation")
    assert isinstance(rec, dict)
    assert rec.get("offered") == "Ачичук"
    assert (rec.get("reason") or "") == "Свежие овощи к насыщенному плову"
    trace = meta.get("recommendation_trace")
    assert isinstance(trace, list) and len(trace) >= 1


@pytest.mark.asyncio
async def test_route_intent_upsell_trace_marks_accepted_on_follow_up(db_with_menu: AsyncSession) -> None:
    """§4.8: трасса не теряется между сообщениями; принятие фиксируется по offered_iiko_id."""
    phone = "+77007776655"
    menu = await load_available_menu(db_with_menu, organization_id=1)
    r1 = await route_intent(
        db_with_menu,
        phone,
        AIBrainResponse(
            intent="order",
            reply_text="Плов в заказе, к нему советую капучино.",
            items=[OrderItem(name="Плов", quantity=1, iiko_item_id="uuid-plov")],
            order_type="delivery",
            payment_method="cash",
            delivery_address="ул. Тест 2",
            is_recommendation=True,
            upsell_offered="Капучино",
            upsell_offered_id="uuid-cappuccino",
            upsell_reasoning="К классике",
        ),
        menu_items=menu,
    )
    await db_with_menu.commit()
    oid = r1.pending_order_id
    assert oid is not None

    await route_intent(
        db_with_menu,
        phone,
        AIBrainResponse(
            intent="order",
            reply_text="Добавил капучино.",
            items=[],
            order_actions=[OrderAction(item_id="Капучино", action="add", quantity=1)],
            order_type="delivery",
            payment_method="cash",
        ),
        menu_items=menu,
    )
    await db_with_menu.commit()
    order = await db_with_menu.get(Order, oid)
    assert order is not None
    meta = (order.items_json or {}).get("order_meta") or {}
    trace = meta.get("recommendation_trace")
    assert isinstance(trace, list) and len(trace) >= 1
    assert trace[0].get("accepted") is True
    assert float(trace[0].get("accepted_revenue_kzt") or 0) >= 1190.0 - 1


@pytest.mark.asyncio
async def test_get_open_draft_order_none_for_new_phone(db_with_menu: AsyncSession) -> None:
    assert await get_open_draft_order(db_with_menu, "+77000000001", 1) is None


@pytest.mark.asyncio
async def test_route_intent_merge_chain_add_remove_set_quantity_recomputes_fees(
    db_with_menu: AsyncSession,
) -> None:
    """
    §4.10: цепочка order_actions — итог и fee_lines пересчитываются через finalize_order_draft
    (compute_fee_lines), а не из сырого JSON модели.
    """
    phone = "+77001112233"
    menu = await load_available_menu(db_with_menu, organization_id=1)

    r1 = await route_intent(
        db_with_menu,
        phone,
        AIBrainResponse(
            intent="order",
            reply_text="ок",
            items=[
                OrderItem(name="Лагман", quantity=2, iiko_item_id="uuid-lagman"),
            ],
            order_type="delivery",
            payment_method="cash",
            delivery_address="ул. Тест 10",
        ),
        menu_items=menu,
    )
    await db_with_menu.commit()
    oid = r1.pending_order_id
    assert oid is not None

    await route_intent(
        db_with_menu,
        phone,
        AIBrainResponse(
            intent="order",
            reply_text="добавил",
            items=[],
            order_actions=[OrderAction(item_id="uuid-cappuccino", action="add", quantity=1)],
            order_type="delivery",
            payment_method="cash",
        ),
        menu_items=menu,
    )
    await db_with_menu.commit()

    await route_intent(
        db_with_menu,
        phone,
        AIBrainResponse(
            intent="order",
            reply_text="убрал один лагман",
            items=[],
            order_actions=[OrderAction(item_id="uuid-lagman", action="remove", quantity=1)],
            order_type="delivery",
            payment_method="cash",
        ),
        menu_items=menu,
    )
    await db_with_menu.commit()

    await route_intent(
        db_with_menu,
        phone,
        AIBrainResponse(
            intent="order",
            reply_text="два капучино",
            items=[],
            order_actions=[OrderAction(item_id="Капучино", action="set_quantity", quantity=2)],
            order_type="delivery",
            payment_method="cash",
        ),
        menu_items=menu,
    )
    await db_with_menu.commit()

    order = await db_with_menu.get(Order, oid)
    assert order is not None
    ij = order.items_json or {}
    names = {(x.get("name"), x.get("quantity")) for x in ij.get("items", []) if isinstance(x, dict)}
    assert ("Лагман", 1) in names
    assert ("Капучино", 2) in names
    assert isinstance(ij.get("fee_lines"), list)
    assert len(ij["fee_lines"]) >= 1
    assert float(order.total_price) > 0


@pytest.mark.asyncio
async def test_route_intent_remove_plov_1kg_drops_plov_packaging_fee_lines(
    db_with_menu: AsyncSession,
) -> None:
    """После удаления плова 1 кг не остаётся строк упаковки tabak/foil для этой позиции."""
    phone = "+77002223344"
    menu = await load_available_menu(db_with_menu, organization_id=1)

    r1 = await route_intent(
        db_with_menu,
        phone,
        AIBrainResponse(
            intent="order",
            reply_text="ок",
            items=[
                OrderItem(
                    name="Плов 1 кг",
                    quantity=1,
                    iiko_item_id="uuid-plov-1kg",
                    packaging_plov_1kg="tabak",
                ),
                OrderItem(name="Лагман", quantity=1, iiko_item_id="uuid-lagman"),
            ],
            order_type="delivery",
            payment_method="cash",
            delivery_address="ул. Плов 1",
        ),
        menu_items=menu,
    )
    await db_with_menu.commit()
    oid = r1.pending_order_id
    order = await db_with_menu.get(Order, oid)
    ij0 = order.items_json or {}
    kinds_before = _fee_kinds(ij0)
    assert any("plov_1kg" in k for k in kinds_before)

    await route_intent(
        db_with_menu,
        phone,
        AIBrainResponse(
            intent="order",
            reply_text="убрал плов",
            items=[],
            order_actions=[OrderAction(item_id="uuid-plov-1kg", action="remove", quantity=1)],
            order_type="delivery",
            payment_method="cash",
        ),
        menu_items=menu,
    )
    await db_with_menu.commit()

    order2 = await db_with_menu.get(Order, oid)
    ij1 = order2.items_json or {}
    kinds_after = _fee_kinds(ij1)
    assert not any("plov_1kg" in k for k in kinds_after)
    names = {x.get("name") for x in ij1.get("items", []) if isinstance(x, dict)}
    assert "Плов 1 кг" not in names
    assert "Лагман" in names


@pytest.mark.asyncio
async def test_route_intent_merge_skips_duplicate_explicit_action_id(db_with_menu: AsyncSession) -> None:
    """Повтор того же action_id на черновике не удваивает количество."""
    phone = "+77005554433"
    menu = await load_available_menu(db_with_menu, organization_id=1)
    await route_intent(
        db_with_menu,
        phone,
        AIBrainResponse(
            intent="order",
            reply_text="Плов",
            items=[OrderItem(name="Плов", quantity=1, iiko_item_id="uuid-plov")],
            order_type="delivery",
            payment_method="cash",
            delivery_address="ул. Тест 5",
        ),
        menu_items=menu,
        organization_id=1,
    )
    await db_with_menu.commit()
    add_cap = OrderAction(
        action_id="idem-cap-1",
        item_id="Капучино",
        action="add",
        quantity=2,
    )
    await route_intent(
        db_with_menu,
        phone,
        AIBrainResponse(
            intent="order",
            reply_text="добавил кофе",
            items=[],
            order_actions=[add_cap],
            order_type="delivery",
            payment_method="cash",
        ),
        menu_items=menu,
        inbound_message_id="wa-1",
        organization_id=1,
    )
    await db_with_menu.commit()
    await route_intent(
        db_with_menu,
        phone,
        AIBrainResponse(
            intent="order",
            reply_text="повтор той же дельты",
            items=[],
            order_actions=[add_cap],
            order_type="delivery",
            payment_method="cash",
        ),
        menu_items=menu,
        inbound_message_id="wa-2",
        organization_id=1,
    )
    await db_with_menu.commit()
    uid = await db_with_menu.scalar(
        select(User.id).where(User.phone == phone, User.organization_id == 1),
    )
    assert uid is not None
    oid = await db_with_menu.scalar(
        select(Order.id).where(Order.user_id == uid, Order.status == OrderStatus.DRAFT).order_by(Order.id.desc()),
    )
    assert oid is not None
    row = await db_with_menu.get(Order, oid)
    cap = next(
        x for x in (row.items_json or {}).get("items", []) if isinstance(x, dict) and x.get("name") == "Капучино"
    )
    assert int(cap.get("quantity", 0)) == 2


@pytest.mark.asyncio
async def test_route_intent_merge_skips_duplicate_synthetic_fingerprint(db_with_menu: AsyncSession) -> None:
    """Один и тот же inbound_message_id + те же дельты без action_id — вторая обработка no-op."""
    phone = "+77005554444"
    menu = await load_available_menu(db_with_menu, organization_id=1)
    await route_intent(
        db_with_menu,
        phone,
        AIBrainResponse(
            intent="order",
            reply_text="Плов",
            items=[OrderItem(name="Плов", quantity=1, iiko_item_id="uuid-plov")],
            order_type="delivery",
            payment_method="cash",
            delivery_address="ул. Тест 6",
        ),
        menu_items=menu,
        organization_id=1,
    )
    await db_with_menu.commit()
    dup_actions = [
        OrderAction(item_id="Капучино", action="add", quantity=3),
    ]
    mid = "wamid.duplicate.test"
    await route_intent(
        db_with_menu,
        phone,
        AIBrainResponse(
            intent="order",
            reply_text="кофе",
            items=[],
            order_actions=list(dup_actions),
            order_type="delivery",
            payment_method="cash",
        ),
        menu_items=menu,
        inbound_message_id=mid,
        organization_id=1,
    )
    await db_with_menu.commit()
    await route_intent(
        db_with_menu,
        phone,
        AIBrainResponse(
            intent="order",
            reply_text="повтор обработки",
            items=[],
            order_actions=list(dup_actions),
            order_type="delivery",
            payment_method="cash",
        ),
        menu_items=menu,
        inbound_message_id=mid,
        organization_id=1,
    )
    await db_with_menu.commit()
    uid2 = await db_with_menu.scalar(select(User.id).where(User.phone == phone, User.organization_id == 1))
    assert uid2 is not None
    oid2 = await db_with_menu.scalar(
        select(Order.id).where(Order.user_id == uid2, Order.status == OrderStatus.DRAFT).order_by(Order.id.desc()),
    )
    assert oid2 is not None
    row2 = await db_with_menu.get(Order, oid2)
    cap2 = next(
        x for x in (row2.items_json or {}).get("items", []) if isinstance(x, dict) and x.get("name") == "Капучино"
    )
    assert int(cap2.get("quantity", 0)) == 3
