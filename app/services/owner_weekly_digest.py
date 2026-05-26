"""
Еженедельный Telegram-дайджест владельцу (чат персонала / TELEGRAM_ADMIN_CHAT_ID).

Вызывается из ARQ по расписанию: для каждой организации в её часовом поясе — понедельник 10:00–10:45.
Ручная отправка и preview — через `owner_digest_delivery`.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import Organization
from app.services.owner_digest_delivery import send_weekly_digest

logger = logging.getLogger(__name__)


async def maybe_send_weekly_digest_for_org(db: AsyncSession, org: Organization) -> None:
    """Cron: понедельник 10:00 в TZ организации, Redis dedupe на неделю."""
    result = await send_weekly_digest(
        db,
        int(org.id),
        force=False,
        channel="telegram",
        triggered_by="cron",
    )
    if result.get("sent") or result.get("error") == "send_failed" or result.get("error") == "telegram_not_configured":
        await db.commit()


async def owner_digest_scheduled_tick(_ctx: dict[str, Any]) -> None:
    """Тик для ARQ cron: обход всех организаций (TZ org для окна понедельника)."""
    if not settings.telegram_bot_token.strip():
        logger.debug("owner_digest: пропуск (нет TELEGRAM_BOT_TOKEN)")
        return
    from app.db.session import async_session_factory

    async with async_session_factory() as db:
        res = await db.execute(select(Organization).where(Organization.is_active.is_(True)))
        orgs = list(res.scalars().all())
        for org in orgs:
            try:
                await maybe_send_weekly_digest_for_org(db, org)
            except Exception as exc:
                logger.exception("owner_digest: org %s: %s", org.id, exc)
                await db.rollback()
