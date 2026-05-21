"""Shift metrics helpers shared by G10 shift state engine."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import Order, OrderStatus
from app.services.tenant_scope import orders_location_filter, orders_tenant_clause

_COMPLETED_TODAY = (
    OrderStatus.CONFIRMED.value,
    OrderStatus.SENT_TO_IIKO.value,
    OrderStatus.IN_TRANSIT.value,
    OrderStatus.WAITING_PICKUP.value,
    OrderStatus.COMPLETED.value,
)


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _sql_dt(dt: datetime) -> datetime:
    u = _utc(dt)
    return u.replace(tzinfo=None) if settings.db_mode == "sqlite" else u


async def _saved_today_kzt(
    db: AsyncSession,
    org_id: int,
    *,
    location_id: int | None,
    allowed_location_ids: set[int] | None,
) -> float:
    today_start = _sql_dt(datetime.now(tz=timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0))
    total = float(
        await db.scalar(
            select(func.coalesce(func.sum(Order.total_price), 0)).where(
                orders_tenant_clause(org_id),
                Order.status.in_(list(_COMPLETED_TODAY)),
                Order.created_at >= today_start,
                Order.total_price > 0,
                orders_location_filter(allowed_location_ids, location_id),
            )
        )
        or 0
    )
    return round(total, 2)
