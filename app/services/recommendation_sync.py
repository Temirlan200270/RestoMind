"""Синхронизация recommendation_events из items_json (тот же разбор, что intelligence_analytics)."""

from __future__ import annotations

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Order, RecommendationEvent, User
from app.services.intelligence_analytics import list_recommendation_events


async def sync_recommendation_events_for_order(db: AsyncSession, order: Order) -> None:
    """Перезаписывает строки recommendation_events для заказа (вызывать после подтверждения)."""
    ij = order.items_json if isinstance(order.items_json, dict) else None
    org_id = order.organization_id
    if org_id is None and order.user_id:
        u = await db.get(User, order.user_id)
        if u is not None:
            org_id = int(u.organization_id)
    if org_id is None or not ij:
        return

    await db.execute(delete(RecommendationEvent).where(RecommendationEvent.order_id == order.id))
    for ev in list_recommendation_events(ij):
        oid = (ev.get("offered_iiko_id") or "").strip() or None
        off = (ev.get("offered") or "").strip() or None
        accepted = bool(ev.get("accepted") is True)
        reasoning = ev.get("reasoning")
        reason_s = str(reasoning).strip() if reasoning is not None else None
        db.add(
            RecommendationEvent(
                organization_id=int(org_id),
                order_id=int(order.id),
                item_iiko_id=oid,
                item_name=off,
                accepted=accepted,
                reasoning=reason_s,
            ),
        )
    await db.flush()
