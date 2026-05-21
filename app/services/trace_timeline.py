"""Control Plane Phase 2: unified trace timeline from durable stores."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ChatLog, SystemEvent, User


async def latest_trace_for_phone(
    db: AsyncSession,
    *,
    org_id: int,
    phone: str,
) -> tuple[str | None, str | None]:
    """Reuse trace_id from the latest chat log meta, if any."""
    from app.services.trace_context import build_conversation_id

    conversation_id = build_conversation_id(org_id, phone)
    meta = await db.scalar(
        select(ChatLog.meta_json)
        .join(User, User.id == ChatLog.user_id)
        .where(
            ChatLog.organization_id == org_id,
            User.phone == phone,
            ChatLog.meta_json.isnot(None),
        )
        .order_by(ChatLog.created_at.desc())
        .limit(1),
    )
    if isinstance(meta, dict):
        trace_id = meta.get("trace_id")
        if trace_id:
            return str(trace_id), conversation_id
    return None, conversation_id


async def build_trace_timeline(
    db: AsyncSession,
    *,
    org_id: int,
    trace_id: str,
    limit: int = 100,
) -> dict[str, Any]:
    """Merge SystemEvent + ChatLog rows sharing the same trace_id (sorted by time)."""
    tid = (trace_id or "").strip()
    if not tid:
        return {"trace_id": "", "entries": [], "total": 0}

    cap = max(1, min(int(limit), 200))

    system_rows = (
        await db.scalars(
            select(SystemEvent)
            .where(
                SystemEvent.organization_id == org_id,
                SystemEvent.payload_json["trace_id"].as_string() == tid,
            )
            .order_by(SystemEvent.created_at.asc())
            .limit(cap),
        )
    ).all()

    chat_rows = (
        await db.scalars(
            select(ChatLog)
            .where(
                ChatLog.organization_id == org_id,
                ChatLog.meta_json["trace_id"].as_string() == tid,
            )
            .order_by(ChatLog.created_at.asc())
            .limit(cap),
        )
    ).all()

    entries: list[dict[str, Any]] = []
    for ev in system_rows:
        payload = ev.payload_json if isinstance(ev.payload_json, dict) else {}
        public_payload = {k: v for k, v in payload.items() if not str(k).startswith("_")}
        entries.append({
            "kind": "system_event",
            "at": ev.created_at.isoformat() if ev.created_at else None,
            "type": ev.event_type,
            "actor": ev.source,
            "entity_type": ev.entity_type,
            "entity_id": ev.entity_id,
            "parent_event_id": payload.get("parent_event_id") or payload.get("_parent_event_id"),
            "caused_by": payload.get("caused_by") or payload.get("_caused_by"),
            "payload": public_payload,
        })
    for row in chat_rows:
        entries.append({
            "kind": "chat_log",
            "at": row.created_at.isoformat() if row.created_at else None,
            "role": row.role,
            "content": (row.content or "")[:500],
            "chat_log_id": int(row.id),
            "delivery_status": row.delivery_status,
        })

    entries.sort(key=lambda item: item.get("at") or "")
    if len(entries) > cap:
        entries = entries[:cap]

    return {
        "trace_id": tid,
        "organization_id": org_id,
        "entries": entries,
        "total": len(entries),
    }
