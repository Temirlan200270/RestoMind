"""Связка бронь + предзаказ в зале: Order.booking_id и intent_router."""

from datetime import date, timedelta

import pytest

from app.schemas.ai_schemas import AIBrainResponse, BookingDetails, OrderItem
from app.services.intent_router import _handle_order, confirm_order, route_intent
from app.db.models import Booking, Order


@pytest.mark.asyncio
async def test_hall_preorder_creates_booking_and_order(db_with_menu) -> None:
    """Предзаказ в зале: создаётся Booking и Order с booking_id."""
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    ai = AIBrainResponse(
        intent="order",
        reply_text="Ок",
        items=[
            OrderItem(name="Плов", iiko_item_id="uuid-plov", quantity=1),
        ],
        order_type="hall",
        payment_method="cash",
        is_preorder=True,
        booking_details=BookingDetails(
            date=tomorrow,
            time="19:00",
            guests=3,
            hall="hall_1",
            comment="у окна",
        ),
    )
    r = await _handle_order(db_with_menu, "+77001234567", ai, menu_items=None)
    assert r.pending_order_id is not None

    oid = r.pending_order_id
    order = await db_with_menu.get(Order, oid)
    assert order is not None
    assert order.booking_id is not None
    assert order.prepayment_status == "pending"

    bk = await db_with_menu.get(Booking, order.booking_id)
    assert bk is not None
    assert bk.guests == 3
    assert bk.status == "draft"
    assert (bk.comment or "") == "у окна"


@pytest.mark.asyncio
async def test_hall_preorder_without_booking_details_fails_gracefully(db_with_menu) -> None:
    """Без booking_details — возврат без заказа, с подсказкой."""
    ai = AIBrainResponse(
        intent="order",
        reply_text="Ок",
        items=[OrderItem(name="Плов", iiko_item_id="uuid-plov", quantity=1)],
        order_type="hall",
        is_preorder=True,
        booking_details=None,
    )
    r = await _handle_order(db_with_menu, "+77001234567", ai, menu_items=None)
    assert r.pending_order_id is None
    assert "дату" in (r.reply_text or "").lower() or "Дата" in (r.reply_text or "")


@pytest.mark.asyncio
async def test_confirm_order_confirms_linked_booking(db_with_menu) -> None:
    """Подтверждение заказа подтверждает связанную бронь."""
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    ai = AIBrainResponse(
        intent="order",
        reply_text="Ок",
        items=[OrderItem(name="Плов", iiko_item_id="uuid-plov", quantity=1)],
        order_type="hall",
        is_preorder=True,
        booking_details=BookingDetails(date=tomorrow, time="20:00", guests=2, hall="hall_2"),
    )
    r = await route_intent(db_with_menu, "+77009999999", ai, menu_items=None)
    assert r.pending_order_id
    order = await db_with_menu.get(Order, r.pending_order_id)
    assert order and order.booking_id

    o2 = await confirm_order(db_with_menu, order.id)
    assert o2 is not None
    assert o2.status == "confirmed"

    bk = await db_with_menu.get(Booking, order.booking_id)
    assert bk is not None
    assert bk.status == "confirmed"
