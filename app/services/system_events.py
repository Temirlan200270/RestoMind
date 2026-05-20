"""Durable domain events for analytics and AI operations."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import SystemEvent

logger = logging.getLogger(__name__)


@dataclass
class BusinessEvent:
    """Unified business event schema for the Event-First OS layer.

    Fields map to SystemEvent columns:
      - id           → idempotency_key (UUID string, prevents duplicates)
      - actor        → source column
      - location_id  → payload_json._location_id
      - version      → payload_json._version
    """

    org_id: int
    type: str  # e.g. "order.created", "ai.escalated"
    actor: str  # "ai" | "operator" | "customer" | "system"
    payload: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))
    location_id: int | None = None
    entity_type: str | None = None
    entity_id: str | int | None = None
    version: int = 1


async def emit_event(db: AsyncSession, event: BusinessEvent) -> SystemEvent | None:
    """Единственный способ записи бизнес-событий через OS Event Layer.

    Оборачивает emit_system_event(), добавляя структурированные поля:
    actor, version, location_id — хранятся в payload_json под ключами _actor, _version, _location_id.

    После записи в БД вызывает синхронные consumers (analytics_consumer).
    Не коммитит — вызывается внутри существующей транзакции.
    """
    enriched_payload = {
        **event.payload,
        "_actor": event.actor,
        "_version": event.version,
    }
    location_id = event.location_id if event.location_id is not None else int(event.org_id)
    enriched_payload["_location_id"] = location_id

    result = await emit_system_event(
        db,
        organization_id=event.org_id,
        event_type=event.type,
        payload=enriched_payload,
        source=event.actor,
        entity_type=event.entity_type,
        entity_id=event.entity_id,
        idempotency_key=event.id,
    )

    if result is not None:
        try:
            from app.services.analytics_consumer import on_business_event
            await on_business_event(event, db)
        except Exception:
            logger.exception("analytics_consumer failed for event type=%s org=%d", event.type, event.org_id)

        # Phase 5 OS: audit_consumer — иммутабельный лог действий
        try:
            from app.services.audit_consumer import on_business_event as _audit_on_event
            await _audit_on_event(event, db)
        except Exception:
            logger.exception("audit_consumer failed for event type=%s org=%d", event.type, event.org_id)

        try:
            from app.services.healing_realtime import maybe_trigger_realtime_healing
            await maybe_trigger_realtime_healing(db, event)
        except Exception:
            logger.exception("healing_realtime failed for event type=%s org=%d", event.type, event.org_id)

        # Phase 2a/5 OS: websocket_consumer — org-scoped Pub/Sub push
        try:
            import asyncio
            from app.services.events import publish_org_event as _ws_publish
            asyncio.create_task(
                _ws_publish(
                    event.org_id,
                    event.type,
                    {
                        "organization_id": event.org_id,
                        "type": event.type,
                        "payload": {k: v for k, v in event.payload.items() if not k.startswith("_")},
                    },
                )
            )
        except Exception:
            logger.debug("websocket_consumer skipped for event type=%s", event.type)

    return result


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
