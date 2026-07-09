from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ChannelConnection, ChannelMessage


def _status_rank(status: str) -> int:
    return {
        "works": 0,
        "needs_reconnect": 1,
        "degraded": 2,
        "blocked": 3,
        "failed": 4,
    }.get(status, 2)


def _worst(*statuses: str) -> str:
    values = [s for s in statuses if s]
    if not values:
        return "degraded"
    return max(values, key=_status_rank)


def classify_connection_health(connection: ChannelConnection) -> str:
    status = (connection.status or "").strip().lower()
    if status == "connected":
        return "works"
    if status in {"qr_required", "connecting", "expired"}:
        return "needs_reconnect"
    if status in {"banned", "disabled"}:
        return "blocked"
    if status in {"error", "rate_limited"}:
        return "failed"
    return "degraded"


async def build_channel_health_summary(
    db: AsyncSession,
    *,
    organization_id: int,
) -> dict[str, Any]:
    connections = (
        await db.execute(
            select(ChannelConnection)
            .where(ChannelConnection.organization_id == int(organization_id))
            .order_by(ChannelConnection.id.asc())
        )
    ).scalars().all()

    pending_in = int(
        await db.scalar(
            select(func.count())
            .select_from(ChannelMessage)
            .where(
                ChannelMessage.organization_id == int(organization_id),
                ChannelMessage.direction == "in",
                ChannelMessage.status.in_(("received", "retrying", "processing")),
            )
        )
        or 0
    )
    pending_out = int(
        await db.scalar(
            select(func.count())
            .select_from(ChannelMessage)
            .where(
                ChannelMessage.organization_id == int(organization_id),
                ChannelMessage.direction == "out",
                ChannelMessage.status.in_(("pending", "retrying", "processing")),
            )
        )
        or 0
    )
    failed_recent = int(
        await db.scalar(
            select(func.count())
            .select_from(ChannelMessage)
            .where(
                ChannelMessage.organization_id == int(organization_id),
                ChannelMessage.status == "failed",
            )
        )
        or 0
    )

    connection_payload: list[dict[str, Any]] = []
    connection_health = "degraded" if not connections else "works"
    for row in connections:
        h = classify_connection_health(row)
        connection_health = _worst(connection_health, h)
        connection_payload.append(
            {
                "id": int(row.id),
                "provider": row.provider,
                "status": row.status,
                "health": h,
                "phone": row.phone or "",
                "last_error": row.last_error or "",
                "last_seen_at": row.last_seen_at.isoformat() if row.last_seen_at else None,
            }
        )

    queue_health = "works"
    if pending_in + pending_out > 50:
        queue_health = "failed"
    elif pending_in + pending_out > 10 or failed_recent > 0:
        queue_health = "degraded"

    overall = _worst(connection_health, queue_health)
    return {
        "overall": overall,
        "connection": connection_health,
        "queue": queue_health,
        "delivery": "degraded" if failed_recent else "works",
        "counts": {
            "connections": len(connections),
            "pending_in": pending_in,
            "pending_out": pending_out,
            "failed": failed_recent,
        },
        "connections": connection_payload,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
