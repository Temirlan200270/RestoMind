"""Жёсткий kitchen-gate: заказы при закрытой кухне → night_preorder."""

from datetime import date, timedelta
from unittest.mock import patch

import pytest

from app.db.models import Order, Organization
from app.schemas.ai_schemas import AIBrainResponse, BookingDetails, OrderItem
from app.services.intent_router import _handle_order
from app.services.time_context import OperationalStatus


def _overnight_schedule() -> dict:
    day = {
        "is_closed": False,
        "open": "11:00",
        "kitchen_close": "23:45",
        "business_close": "01:00",
    }
    return {k: dict(day) for k in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")}


@pytest.mark.asyncio
async def test_kitchen_closed_routes_to_night_preorder(db_with_menu) -> None:
    org = await db_with_menu.get(Organization, 1)
    assert org is not None
    org.timezone = "Asia/Almaty"
    org.schedule_json = _overnight_schedule()
    await db_with_menu.flush()

    op = OperationalStatus(
        is_business_open=True,
        is_kitchen_open=False,
        next_business_open_at="2026-04-28 11:00",
        next_kitchen_open_at="2026-04-28 11:00",
        kitchen_closes_in_minutes=None,
        human_label="Заведение открыто, но кухня закрыта.",
        prompt_instruction="KITCHEN_OPEN=0",
    )
    ai = AIBrainResponse(
        intent="order",
        reply_text="Принял.",
        items=[OrderItem(name="Плов", iiko_item_id="uuid-plov", quantity=1)],
        order_type="pickup",
        payment_method="cash",
        pickup_time_note="завтра 12:00",
    )
    with patch("app.services.time_context.check_operational_status", return_value=op):
        r = await _handle_order(
            db_with_menu,
            "+77001112233",
            ai,
            organization_id=1,
            org=org,
        )
    assert r.pending_order_id is not None
    assert "Утром оператор свяжется" in (r.reply_text or "")
    order = await db_with_menu.get(Order, r.pending_order_id)
    assert order is not None
    assert order.kind == "night_preorder"


@pytest.mark.asyncio
async def test_hall_preorder_skips_kitchen_gate(db_with_menu) -> None:
    org = await db_with_menu.get(Organization, 1)
    assert org is not None
    org.timezone = "Asia/Almaty"
    org.schedule_json = _overnight_schedule()
    await db_with_menu.flush()

    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    ai = AIBrainResponse(
        intent="order",
        reply_text="Ок",
        items=[OrderItem(name="Плов", iiko_item_id="uuid-plov", quantity=1)],
        order_type="hall",
        payment_method="cash",
        is_preorder=True,
        booking_details=BookingDetails(
            date=tomorrow,
            time="19:00",
            guests=2,
            hall="hall_1",
        ),
    )
    with patch(
        "app.services.time_context.check_operational_status",
        side_effect=AssertionError("must not call when is_preorder"),
    ):
        r = await _handle_order(
            db_with_menu,
            "+77002223344",
            ai,
            organization_id=1,
            org=org,
        )
    assert r.pending_order_id is not None
    assert "🌙" not in (r.reply_text or "")
    order = await db_with_menu.get(Order, r.pending_order_id)
    assert order is not None
    assert order.kind != "night_preorder"


@pytest.mark.asyncio
async def test_kitchen_open_regular_order(db_with_menu) -> None:
    org = await db_with_menu.get(Organization, 1)
    assert org is not None
    org.timezone = "Asia/Almaty"
    org.schedule_json = _overnight_schedule()
    await db_with_menu.flush()

    op = OperationalStatus(
        is_business_open=True,
        is_kitchen_open=True,
        next_business_open_at=None,
        next_kitchen_open_at=None,
        kitchen_closes_in_minutes=120,
        human_label="Открыто",
        prompt_instruction="KITCHEN_OPEN=1",
    )
    ai = AIBrainResponse(
        intent="order",
        reply_text="Принял.",
        items=[OrderItem(name="Плов", iiko_item_id="uuid-plov", quantity=1)],
        order_type="pickup",
        payment_method="cash",
        pickup_time_note="через час",
    )
    with patch("app.services.time_context.check_operational_status", return_value=op):
        r = await _handle_order(
            db_with_menu,
            "+77003334455",
            ai,
            organization_id=1,
            org=org,
        )
    assert r.pending_order_id is not None
    assert "🌙" not in (r.reply_text or "")
    order = await db_with_menu.get(Order, r.pending_order_id)
    assert order is not None
    assert order.kind != "night_preorder"
