"""Durable order lifecycle events from admin actions."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Order
from app.services.system_events import BusinessEvent, emit_event


async def emit_order_cancelled_from_admin(
    db: AsyncSession,
    order: Order,
    *,
    actor: str,
) -> None:
    """Emit order.cancelled when operator cancels via admin (any prior status)."""
    org_id = int(order.organization_id or 0)
    if org_id <= 0:
        return
    await emit_event(
        db,
        BusinessEvent(
            id=f"order.cancelled:{order.id}",
            org_id=org_id,
            type="order.cancelled",
            actor=actor,
            location_id=getattr(order, "location_id", None),
            entity_type="order",
            entity_id=order.id,
            payload={
                "order_id": order.id,
                "total_price": float(order.total_price or 0),
                "source": "admin",
            },
        ),
    )
