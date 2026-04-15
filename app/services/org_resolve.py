"""Определение organization_id из контекста WhatsApp и настроек."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import Organization


async def organization_id_for_whatsapp_value(db: AsyncSession, value: dict[str, Any]) -> int:
    """
    Meta Cloud API: value.metadata.phone_number_id сопоставляем с Organization.whatsapp_phone_number_id.
    """
    meta = value.get("metadata")
    if isinstance(meta, dict):
        pnid = str(meta.get("phone_number_id") or "").strip()
        if pnid:
            oid = await db.scalar(
                select(Organization.id).where(Organization.whatsapp_phone_number_id == pnid),
            )
            if oid is not None:
                return int(oid)
    env_pid = str(settings.whatsapp_phone_number_id or "").strip()
    if env_pid:
        oid = await db.scalar(
            select(Organization.id).where(Organization.whatsapp_phone_number_id == env_pid),
        )
        if oid is not None:
            return int(oid)
    fallback = await db.scalar(select(Organization.id).order_by(Organization.id.asc()).limit(1))
    if fallback is not None:
        return int(fallback)
    return int(settings.default_organization_id)
