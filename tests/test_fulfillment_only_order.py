"""Fulfillment-only turns: обновление черновика без новых items."""

import pytest

from app.db.models import Order, OrderStatus
from app.schemas.ai_schemas import AIBrainResponse, OrderItem
from app.services.decision_engine import decision_engine
from app.services.dialog_mgr import UserState
from app.services.intent_router import _handle_order


@pytest.mark.asyncio
async def test_fulfillment_only_pickup_updates_existing_draft(db_with_menu) -> None:
    """«Самовывоз через полчаса» при активном черновике — сохраняем meta и спрашиваем оплату."""
    seed_ai = AIBrainResponse(
        intent="order",
        reply_text="Добавил салат.",
        items=[OrderItem(name="Плов", iiko_item_id="uuid-plov", quantity=1)],
        order_type="pickup",
    )
    seed = await _handle_order(
        db_with_menu,
        "+77051310837",
        seed_ai,
        organization_id=1,
    )
    assert seed.pending_order_id is not None
    draft = await db_with_menu.get(Order, seed.pending_order_id)
    assert draft is not None
    assert draft.status == OrderStatus.DRAFT

    fulfillment_ai = AIBrainResponse(
        intent="order",
        reply_text="Хорошо, самовывоз через полчаса.",
        items=[],
        order_type="pickup",
        pickup_time_note="через полчаса",
    )
    result = await _handle_order(
        db_with_menu,
        "+77051310837",
        fulfillment_ai,
        organization_id=1,
        draft_order=draft,
    )

    assert "не смог разобрать позиции" not in (result.reply_text or "").lower()
    assert "салат" in (result.reply_text or "").lower() or "плов" in (result.reply_text or "").lower()
    assert result.pending_order_id == draft.id

    await db_with_menu.refresh(draft)
    meta = (draft.items_json or {}).get("order_meta") or {}
    assert meta.get("order_type") == "pickup"
    assert "полчаса" in str(meta.get("pickup_time_note") or "").lower()


@pytest.mark.asyncio
async def test_decision_engine_allows_fulfillment_turn_with_draft(db_with_menu) -> None:
    from app.services.context_engine import AIReadContext

    seed_ai = AIBrainResponse(
        intent="order",
        reply_text="Ок",
        items=[OrderItem(name="Плов", iiko_item_id="uuid-plov", quantity=1)],
    )
    seed = await _handle_order(
        db_with_menu, "+77009998877", seed_ai, organization_id=1,
    )
    draft = await db_with_menu.get(Order, seed.pending_order_id)
    assert draft is not None

    proposal = AIBrainResponse(
        intent="order",
        reply_text="Самовывоз через полчаса.",
        items=[],
        order_type="pickup",
        pickup_time_note="через полчаса",
    )
    ctx = AIReadContext(
        menu_items=[],
        user=None,
        org=None,
        kb_context="",
        draft_row=draft,
        customer_ctx="",
        user_preferences={},
    )
    validation = await decision_engine.validate(proposal, ctx, None)
    assert validation.is_valid
    assert proposal.intent == "order"
