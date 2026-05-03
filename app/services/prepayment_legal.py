"""Дополнительный дисклеймер предоплаты с уровня организации (юр./продуктовый текст)."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Organization
from app.services.prompts import (
    DEFAULT_PREPAYMENT_LEGAL_TEXT_RU,
    DEFAULT_PREPAYMENT_LEGAL_TEXT_KZ,
    DEFAULT_PREPAYMENT_LEGAL_TEXT_EN,
)


async def append_prepayment_legal_disclaimer(
    db: AsyncSession,
    organization_id: int,
    reply: str,
) -> str:
    """Добавляет к ответу гостю необязательный многострочный текст из настроек филиала."""
    org = await db.get(Organization, organization_id)
    if org is None:
        return reply

    raw = getattr(org, "prepayment_legal_text", None)
    if raw and raw.strip():
        extra = raw.strip()
    else:
        # Выбор дефолта по таймзоне (RU/KZ) или метаданным
        tz = str(getattr(org, "timezone", "")).lower()
        if "almaty" in tz or "astana" in tz or "aqtobe" in tz:
            extra = DEFAULT_PREPAYMENT_LEGAL_TEXT_KZ
        elif "europe" in tz or "london" in tz or "utc" in tz:
            extra = DEFAULT_PREPAYMENT_LEGAL_TEXT_EN
        else:
            extra = DEFAULT_PREPAYMENT_LEGAL_TEXT_RU

    if not extra:
        return reply

    return f"{reply.rstrip()}\n\n{extra}"
