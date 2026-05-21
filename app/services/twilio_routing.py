"""Resolve tenant organization from Twilio Voice «To» number."""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import Organization


def normalize_e164(phone: str) -> str:
    """Normalize to +digits (E.164-ish) for comparison."""
    raw = (phone or "").strip()
    if not raw:
        return ""
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return ""
    return f"+{digits}"


def _org_twilio_voice_number(org: Organization) -> str:
    meta = org.meta_json if isinstance(org.meta_json, dict) else {}
    return normalize_e164(str(meta.get("twilio_voice_number") or ""))


async def resolve_org_from_twilio_number(
    db: AsyncSession,
    to_e164: str,
) -> tuple[Organization | None, int]:
    """
    Match Twilio «To» against Organization.meta_json.twilio_voice_number,
    then settings.twilio_voice_number, then default_organization_id.
    """
    target = normalize_e164(to_e164)
    if target:
        rows = (await db.scalars(select(Organization).order_by(Organization.id.asc()))).all()
        for org in rows:
            if _org_twilio_voice_number(org) == target:
                return org, int(org.id)

        env_num = normalize_e164(settings.twilio_voice_number)
        if env_num and env_num == target:
            oid = int(settings.default_organization_id)
            org = await db.get(Organization, oid)
            return org, oid

    oid = int(settings.default_organization_id)
    org = await db.get(Organization, oid)
    return org, oid
