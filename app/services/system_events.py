"""Durable domain events for analytics and AI operations."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import SystemEvent

logger = logging.getLogger(__name__)


async def emit_system_event(
    db: AsyncSession,
    *,
    organization_id: int,
    event_type: str,
    payload: dict[str, Any] | None = None,
    source: str = "app",
    entity_type: str | None = None,
    entity_id: str | int | None = None,
    idempotency_key: str | None = None,
) -> SystemEvent | None:
    """Append a durable domain event in the caller's transaction.

    The function does not commit. Callers can use it inside their existing unit of
    work so event persistence follows the business change.
    """
    if not organization_id or not event_type:
        return None
    ev = SystemEvent(
        organization_id=int(organization_id),
        event_type=str(event_type).strip()[:80],
        source=str(source or "app").strip()[:80],
        entity_type=(str(entity_type).strip()[:80] if entity_type else None),
        entity_id=(str(entity_id).strip()[:120] if entity_id is not None else None),
        idempotency_key=(str(idempotency_key).strip()[:200] if idempotency_key else None),
        payload_json=payload or {},
    )
    if idempotency_key:
        existing = await db.scalar(
            select(SystemEvent.id).where(SystemEvent.idempotency_key == idempotency_key).limit(1),
        )
        if existing is not None:
            logger.info("Duplicate system event ignored: %s", idempotency_key)
            return None
    db.add(ev)
    await db.flush()
    return ev
