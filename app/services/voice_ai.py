"""Voice AI feature flags and operational logging."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import Organization, VoiceCallLog


def org_voice_enabled(org: Organization | None) -> bool:
    meta = org.meta_json if org is not None and isinstance(org.meta_json, dict) else {}
    return bool(meta.get("voice_ai_enabled"))


def get_voice_mode(org: Organization | None) -> str:
    """stt_fallback | realtime from Organization.meta_json.voice_ai_mode."""
    if org is None:
        return "stt_fallback"
    raw = str((org.meta_json or {}).get("voice_ai_mode") or "").strip().lower()
    return "realtime" if raw == "realtime" else "stt_fallback"


def realtime_ready_for_org(org: Organization | None) -> bool:
    """OpenAI Realtime path is configured and org mode is realtime."""
    if get_voice_mode(org) != "realtime":
        return False
    if not (settings.openai_api_key or "").strip():
        return False
    if not (settings.public_base_url or "").strip():
        return False
    return True


def voice_status_for_org(org: Organization | None) -> dict[str, Any]:
    enabled = org_voice_enabled(org)
    mode = get_voice_mode(org)
    return {
        "enabled": enabled,
        "mode": mode,
        "twilio_configured": bool(settings.twilio_auth_token.strip() or settings.public_base_url.strip()),
        "stt_supported": bool(settings.openai_api_key.strip() or settings.gemini_api_key.strip()),
        "realtime_ready": realtime_ready_for_org(org) if enabled else False,
    }


async def set_voice_enabled(db: AsyncSession, org: Organization, *, enabled: bool, mode: str = "stt_fallback") -> dict[str, Any]:
    meta = dict(org.meta_json or {})
    meta["voice_ai_enabled"] = bool(enabled)
    meta["voice_ai_mode"] = "realtime" if mode == "realtime" else "stt_fallback"
    org.meta_json = meta
    await db.flush()
    return voice_status_for_org(org)


def voice_call_log_to_item(row: VoiceCallLog) -> dict[str, Any]:
    """Public shape for admin GET /voice/calls (matches admin-app.js helpers)."""
    payload = row.payload_json if isinstance(row.payload_json, dict) else {}
    duration_raw = payload.get("duration_sec", payload.get("duration"))
    duration_sec: int | None = None
    if duration_raw is not None:
        try:
            duration_sec = int(duration_raw)
        except (TypeError, ValueError):
            duration_sec = None
    recording_raw = payload.get("recording_url") or payload.get("recording")
    recording_url = str(recording_raw).strip() if recording_raw else ""
    item: dict[str, Any] = {
        "id": int(row.id),
        "call_sid": row.call_sid or "",
        "phone": row.phone or "",
        "provider": row.provider or "twilio",
        "mode": row.mode or "stt_fallback",
        "status": row.status or "started",
        "transcript": row.transcript or "",
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "payload": payload,
    }
    if duration_sec is not None and duration_sec > 0:
        item["duration_sec"] = duration_sec
    if recording_url:
        item["recording_url"] = recording_url
    return item


async def list_voice_call_logs(
    db: AsyncSession,
    *,
    org_id: int,
    limit: int = 15,
    offset: int = 0,
    location_id: int | None = None,
) -> dict[str, Any]:
    """Paginated voice call journal scoped to organization (optional payload location_id)."""
    cap = max(1, min(int(limit), 100))
    off = max(0, int(offset))
    filters = [VoiceCallLog.organization_id == org_id]
    if location_id is not None:
        filters.append(VoiceCallLog.payload_json["location_id"].as_integer() == int(location_id))

    total = int(
        await db.scalar(
            select(func.count(VoiceCallLog.id)).where(*filters),
        )
        or 0,
    )
    rows = (
        await db.scalars(
            select(VoiceCallLog)
            .where(*filters)
            .order_by(VoiceCallLog.created_at.desc())
            .offset(off)
            .limit(cap),
        )
    ).all()
    return {
        "items": [voice_call_log_to_item(row) for row in rows],
        "total": total,
        "offset": off,
        "limit": cap,
        "location_id": int(location_id) if location_id is not None else None,
    }


async def record_voice_call(
    db: AsyncSession,
    *,
    org_id: int,
    call_sid: str,
    phone: str,
    status: str,
    transcript: str = "",
    mode: str = "stt_fallback",
    payload: dict[str, Any] | None = None,
) -> VoiceCallLog:
    row = await db.scalar(
        select(VoiceCallLog).where(
            VoiceCallLog.organization_id == org_id,
            VoiceCallLog.call_sid == call_sid,
        )
    )
    if row is None:
        row = VoiceCallLog(organization_id=org_id, call_sid=call_sid, phone=phone, mode=mode)
        db.add(row)
    row.status = status
    if transcript:
        row.transcript = ((row.transcript or "") + "\n" + transcript).strip()
    row.payload_json = {**(row.payload_json or {}), **(payload or {})}
    await db.flush()
    return row
