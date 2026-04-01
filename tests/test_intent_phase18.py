"""
Phase 18: merge order_actions с существующим DRAFT (intent_router).
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Order, OrderStatus
from app.schemas.ai_schemas import AIBrainResponse, OrderAction, OrderItem
from app.services.intent_router import get_open_draft_order, route_intent
from app.services.order_logic import load_available_menu


@pytest.mark.asyncio
async def test_route_intent_merge_updates_same_draft(db_with_menu: AsyncSession) -> None:
    phone = "+77009998877"
    menu = await load_available_menu(db_with_menu)

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
async def test_get_open_draft_order_none_for_new_phone(db_with_menu: AsyncSession) -> None:
    assert await get_open_draft_order(db_with_menu, "+77000000001") is None
