"""Integration failure events on the OS Event Layer."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.system_events import BusinessEvent, emit_event

logger = logging.getLogger(__name__)


async def emit_integration_iiko_failed(
    db: AsyncSession,
    *,
    org_id: int,
    order_id: int,
    error: str,
    phone: str | None = None,
    location_id: int | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    payload: dict[str, Any] = {
        "order_id": int(order_id),
        "error": (error or "")[:500],
    }
    if phone:
        payload["phone"] = phone
    if extra:
        payload.update(extra)
    try:
        await emit_event(
            db,
            BusinessEvent(
                id=f"integration.iiko.failed:{org_id}:{order_id}",
                org_id=int(org_id),
                type="integration.iiko.failed",
                actor="system",
                location_id=location_id,
                entity_type="order",
                entity_id=int(order_id),
                payload=payload,
            ),
        )
    except Exception:
        logger.exception("emit_integration_iiko_failed order=%s org=%s", order_id, org_id)
