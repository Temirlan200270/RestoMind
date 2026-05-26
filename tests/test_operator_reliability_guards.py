"""Unit-тесты guard-слоёв надёжности оператора."""

from __future__ import annotations

import pytest
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MenuItem, Order, OrderStatus
from app.schemas.ai_schemas import AIBrainResponse
from app.services.dialog_mgr import UserState
from app.services.fulfillment_infer import enrich_ai_fulfillment_from_message
from app.services.order_confirm_gate import validate_order_ready_to_confirm
from app.services.upsell_safety_gate import (
    UpsellSafetyContext,
    should_suppress_upsell,
    strip_upsell_from_ai_response,
)


def test_fulfillment_infer_pickup_half_hour() -> None:
    ai = AIBrainResponse(intent="order", reply_text="Ок", items=[])
    out = enrich_ai_fulfillment_from_message(
        ai, "Самовывоз через полчаса", has_draft=True,
    )
    assert out.order_type == "pickup"
    assert "30" in (out.pickup_time_note or "")


def test_upsell_suppressed_on_complaint() -> None:
    ctx = UpsellSafetyContext(
        user_message="Вы отстой, полный",
        order_meta={"order_type": "delivery", "payment_method": "cash"},
    )
    assert should_suppress_upsell(ctx)


def test_upsell_suppressed_on_short_yes() -> None:
    ctx = UpsellSafetyContext(user_message="Да", order_meta={})
    assert should_suppress_upsell(ctx)


def test_strip_upsell_clears_fields() -> None:
    ai = AIBrainResponse(
        intent="order",
        reply_text="x",
        is_recommendation=True,
        upsell_offered="Кола",
        upsell_reasoning="test",
    )
    stripped = strip_upsell_from_ai_response(ai)
    assert stripped.is_recommendation is False
    assert not stripped.upsell_offered


@pytest.mark.asyncio
async def test_confirm_gate_blocks_delivery_without_address(db_with_menu: AsyncSession) -> None:
    from app.db.models import User

    user = User(phone="+77001112233", organization_id=1, name="Test")
    db_with_menu.add(user)
    await db_with_menu.flush()

    order = Order(
        organization_id=1,
        user_id=user.id,
        status=OrderStatus.DRAFT,
        total_price=2790.0,
        items_json={
            "items": [{
                "name": "Плов",
                "quantity": 1,
                "price_per_unit": 2790.0,
                "item_total": 2790.0,
                "iiko_id": "uuid-plov",
                "category": "Горячее",
            }],
            "order_meta": {
                "order_type": "delivery",
                "payment_method": "cash",
                "delivery_address": "",
            },
        },
        updated_at=datetime.now(timezone.utc),
    )
    db_with_menu.add(order)
    await db_with_menu.flush()

    menu = [
        MenuItem(
            name="Плов",
            category="Горячее",
            price=2790.0,
            is_available=True,
            iiko_id="uuid-plov",
            organization_id=1,
        ),
    ]
    gate = await validate_order_ready_to_confirm(
        db_with_menu, order, menu_items=menu, check_fulfillment=True,
    )
    assert gate.ok is False
    assert "адрес" in gate.reason.lower()
