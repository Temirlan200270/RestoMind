"""Money Layer — recovered revenue tracking (shift focus + G6 draft recovery)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Booking, Order
from app.services.revenue_leak import _calc_aov

logger = logging.getLogger(__name__)


async def resolve_focus_recovery_kzt(
    db: AsyncSession,
    org_id: int,
    focus_id: str,
    *,
    aov: float = 0.0,
) -> tuple[float, str]:
    """Resolve amount and kind from money-queue focus_id."""
    fid = str(focus_id or "").strip()
    if not fid or ":" not in fid:
        return 0.0, ""

    prefix, rest = fid.split(":", 1)
    if prefix in ("draft", "prepay", "high"):
        try:
            order_id = int(rest)
        except ValueError:
            return 0.0, ""
        order = await db.get(Order, order_id)
        if order is None or int(order.organization_id or 0) != int(org_id):
            return 0.0, ""
        kind_map = {
            "draft": "abandoned_draft",
            "prepay": "pending_prepay",
            "high": "high_value_stuck",
        }
        return round(float(order.total_price or 0), 2), kind_map[prefix]

    if prefix == "chat":
        est = round(float(aov) * 0.5, 2) if aov > 0 else 0.0
        return est, "slow_chat"

    if prefix == "menu":
        est = round(float(aov) * 0.5, 2) if aov > 0 else 0.0
        return est, "menu_confusion"

    if prefix == "booking":
        try:
            booking_id = int(rest)
        except ValueError:
            return 0.0, ""
        booking = await db.get(Booking, booking_id)
        if booking is None or int(booking.organization_id or 0) != int(org_id):
            return 0.0, ""
        guests = max(1, int(booking.guests or 1))
        est = round(float(aov) * 0.3 * guests, 2) if aov > 0 else 0.0
        return est, "booking_at_risk"

    return 0.0, ""


async def get_recovered_today_kzt(db: AsyncSession, org_id: int) -> dict[str, float | int]:
    """Read today's recovered totals from daily_org_stats."""
    today = datetime.now(tz=timezone.utc).date()
    try:
        row = (
            await db.execute(
                text(
                    """
                    SELECT recovered_kzt, focus_completed_count
                    FROM daily_org_stats
                    WHERE organization_id = :org_id AND day = :day
                    """
                ),
                {"org_id": int(org_id), "day": today},
            )
        ).mappings().first()
    except SQLAlchemyError as exc:
        logger.warning("get_recovered_today_kzt schema lag org=%s: %s", org_id, exc)
        try:
            await db.rollback()
        except SQLAlchemyError:
            logger.exception("rollback after recovered_kzt read failure failed")
        return {"recovered_kzt": 0.0, "focus_completed_count": 0}

    if not row:
        return {"recovered_kzt": 0.0, "focus_completed_count": 0}
    return {
        "recovered_kzt": round(float(row["recovered_kzt"] or 0), 2),
        "focus_completed_count": int(row["focus_completed_count"] or 0),
    }


async def resolve_focus_recovery_with_aov(
    db: AsyncSession,
    org_id: int,
    focus_id: str,
) -> tuple[float, str]:
    amount, kind = await resolve_focus_recovery_kzt(db, org_id, focus_id)
    if amount > 0 or kind in ("abandoned_draft", "pending_prepay", "high_value_stuck"):
        return amount, kind
    aov = await _calc_aov(db, org_id)
    return await resolve_focus_recovery_kzt(db, org_id, focus_id, aov=aov)
