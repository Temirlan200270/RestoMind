"""Organization memory for Copilot and ROI explanations."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import OrganizationMemoryEvent
from app.services.system_events import BusinessEvent, emit_event


async def record_memory_event(
    db: AsyncSession,
    org_id: int,
    *,
    event_type: str,
    summary: str,
    event_date: date | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    payload: dict[str, Any] | None = None,
    source: str = "system",
    confidence_score: float | None = 1.0,
) -> OrganizationMemoryEvent:
    row = OrganizationMemoryEvent(
        organization_id=int(org_id),
        event_date=event_date or datetime.now(tz=timezone.utc).date(),
        event_type=event_type,
        entity_type=entity_type,
        entity_id=entity_id,
        summary=summary[:4000],
        payload_json=payload or {},
        source=source,
        confidence_score=confidence_score,
    )
    db.add(row)
    await db.flush()
    await emit_event(
        db,
        BusinessEvent(
            org_id=int(org_id),
            type="organization_memory.recorded",
            actor="system" if source != "manual" else "operator",
            entity_type="organization_memory_event",
            entity_id=int(row.id),
            payload={"event_type": event_type, "source": source, "summary": summary[:300]},
        ),
    )
    return row


async def list_memory_events(
    db: AsyncSession,
    org_id: int,
    *,
    days: int = 90,
    limit: int = 20,
) -> list[OrganizationMemoryEvent]:
    cutoff = datetime.now(tz=timezone.utc).date() - timedelta(days=max(1, int(days)))
    return list(
        (
            await db.execute(
                select(OrganizationMemoryEvent)
                .where(
                    OrganizationMemoryEvent.organization_id == int(org_id),
                    OrganizationMemoryEvent.event_date >= cutoff,
                )
                .order_by(OrganizationMemoryEvent.event_date.desc(), OrganizationMemoryEvent.id.desc())
                .limit(max(1, int(limit))),
            )
        ).scalars().all(),
    )


async def find_related_memory_events(
    db: AsyncSession,
    org_id: int,
    *,
    query: str,
    days: int = 180,
    limit: int = 10,
) -> list[OrganizationMemoryEvent]:
    terms = [t.strip().lower() for t in (query or "").split() if len(t.strip()) >= 3][:6]
    cutoff = datetime.now(tz=timezone.utc).date() - timedelta(days=max(1, int(days)))
    stmt = select(OrganizationMemoryEvent).where(
        OrganizationMemoryEvent.organization_id == int(org_id),
        OrganizationMemoryEvent.event_date >= cutoff,
    )
    if terms:
        stmt = stmt.where(or_(*[OrganizationMemoryEvent.summary.ilike(f"%{term}%") for term in terms]))
    return list(
        (
            await db.execute(
                stmt.order_by(OrganizationMemoryEvent.event_date.desc(), OrganizationMemoryEvent.id.desc()).limit(
                    max(1, int(limit)),
                ),
            )
        ).scalars().all(),
    )


def memory_event_public(row: OrganizationMemoryEvent) -> dict[str, Any]:
    return {
        "id": int(row.id),
        "date": row.event_date.isoformat(),
        "event_type": row.event_type,
        "entity_type": row.entity_type,
        "entity_id": row.entity_id,
        "summary": row.summary,
        "source": row.source,
        "confidence_score": round(float(row.confidence_score or 0), 4) if row.confidence_score is not None else None,
        "payload": row.payload_json or {},
    }
