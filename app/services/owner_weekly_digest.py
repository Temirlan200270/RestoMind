"""
Еженедельный Telegram-дайджест владельцу (чат персонала / TELEGRAM_ADMIN_CHAT_ID).

Вызывается из ARQ по расписанию: для каждой организации в её часовом поясе — понедельник 10:00–10:45.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import Organization
from app.db.session import redis_client
from app.integrations.telegram import send_ops_notification_html
from app.services.owner_roi import build_week_digest_payload

logger = logging.getLogger(__name__)


async def _redis_set_once(key: str, ttl_sec: int = 8 * 24 * 3600) -> bool:
    """True если ключ установлен впервые (дедупликация рассылки)."""
    try:
        ok = await redis_client.set(key, "1", nx=True, ex=ttl_sec)  # type: ignore[call-arg]
        return bool(ok)
    except TypeError:
        prev = await redis_client.get(key)
        if prev:
            return False
        await redis_client.set(key, "1")
        return True


async def maybe_send_weekly_digest_for_org(db: AsyncSession, org: Organization) -> None:
    """Если сейчас «окно» понедельника 10:00 в TZ организации и ещё не слали — отправить."""
    from zoneinfo import ZoneInfo

    tz_name = (org.timezone or "UTC").strip() or "UTC"
    try:
        zi = ZoneInfo(tz_name)
    except Exception:
        logger.warning("owner_digest: неверная TZ организации %s id=%s", tz_name, org.id)
        zi = ZoneInfo("UTC")
    local = datetime.now(timezone.utc).astimezone(zi)
    if local.weekday() != 0 or local.hour != 10 or local.minute >= 45:
        return

    iso_year, iso_week, _ = local.isocalendar()
    key = f"owner_weekly_digest:{org.id}:{iso_year}:W{iso_week:02d}"
    if not await _redis_set_once(key):
        return

    payload = await build_week_digest_payload(db, org)
    if not payload:
        return
    text_plain = payload.get("text") or ""
    if not text_plain.strip():
        return
    # send_ops_notification_html ожидает HTML — экранируем минимально
    safe = (
        text_plain.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    html = "<b>RestoMind — неделя</b><br/><br/>" + safe.replace("\n", "<br/>")
    try:
        await send_ops_notification_html(html, organization_id=int(org.id))
    except Exception as exc:
        logger.exception("owner_digest: не удалось отправить org=%s: %s", org.id, exc)


async def owner_digest_scheduled_tick(_ctx: dict[str, Any]) -> None:
    """Тик для ARQ cron: обход всех организаций."""
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
