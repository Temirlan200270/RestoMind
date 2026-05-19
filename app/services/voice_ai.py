"""Voice AI feature flags and operational logging."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import Organization, VoiceCallLog


def org_voice_enabled(org: Organization | None) -> bool:
    meta = org.meta_json if org is not None and isinstance(org.meta_json, dict) else {}
    return bool(meta.get("voice_ai_enabled"))


def voice_status_for_org(org: Organization | None) -> dict[str, Any]:
    enabled = org_voice_enabled(org)
    mode = "realtime" if str((org.meta_json or {}).get("voice_ai_mode") if org else "").lower() == "realtime" else "stt_fallback"
    return {
        "enabled": enabled,
        "mode": mode,
        "twilio_configured": bool(settings.twilio_auth_token.strip() or settings.public_base_url.strip()),
        "stt_supported": bool(settings.openai_api_key.strip() or settings.gemini_api_key.strip()),
        "realtime_ready": bool(settings.openai_api_key.strip()) and mode == "realtime",
    }


async def set_voice_enabled(db: AsyncSession, org: Organization, *, enabled: bool, mode: str = "stt_fallback") -> dict[str, Any]:
    meta = dict(org.meta_json or {})
    meta["voice_ai_enabled"] = bool(enabled)
    meta["voice_ai_mode"] = "realtime" if mode == "realtime" else "stt_fallback"
    org.meta_json = meta
    await db.flush()
    return voice_status_for_org(org)


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
