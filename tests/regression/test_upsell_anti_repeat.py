"""Отказ от допродажи не предлагается повторно (§E10)."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import UpsellRule
from app.schemas.ai_schemas import AIBrainResponse
from app.services.order_logic import load_available_menu
from app.services.strategy_engine import apply_db_upsell_rules


@pytest.mark.asyncio
@pytest.mark.regression
async def test_rejected_upsell_iiko_excluded_from_db_rules(db_with_menu: AsyncSession) -> None:
    """
    uuid в upsell_rejected_iiko_ids исключается из кандидатов — то же SKU не предлагается снова.
    """
    db = db_with_menu
    db.add(
        UpsellRule(
            organization_id=1,
            trigger_mode="missing_category",
            trigger_category="кофе",
            suggest_category="кофе",
            min_order_sum=0,
            is_active=True,
        )
    )
    await db.flush()

    menu = await load_available_menu(db)
    items_json: dict = {
        "items": [
            {
                "name": "Плов",
                "category": "Горячее",
                "quantity": 1,
                "iiko_id": "uuid-plov",
            },
        ],
        "order_meta": {"upsell_rejected_iiko_ids": ["uuid-cappuccino"]},
    }
    ai = AIBrainResponse(
        intent="order",
        reply_text="Вот состав.",
        items=[],
        order_type="delivery",
        payment_method="cash",
        delivery_address="ул. 1",
    )
    reply, out = await apply_db_upsell_rules(
        db,
        organization_id=1,
        reply_text="Вот состав.",
        items_json=items_json,
        grand_total=5000.0,
        menu_items=menu,
        ai_eff=ai,
    )
    assert "💡" not in reply
    assert out == items_json
