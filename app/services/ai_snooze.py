"""
Временная пауза ИИ по клиенту (User.ai_snoozed_until).

Семантика: пока now < ai_snoozed_until, входящие сообщения не проходят через LLM
(как при operator-only / ai_paused), но Redis FSM не переводится в HUMAN_MODE —
по истечении срока ответы ИИ возобновляются без действия оператора.

Проверка выполняется в точке входа AI-пайплайна (process_message), не в route_intent:
route_intent вызывается уже после ответа модели.
"""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User

logger = logging.getLogger(__name__)

AiSnoozePreset = Literal["30m", "2h", "until_tomorrow", "forever", "off"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc_aware(dt: datetime) -> datetime:
    """SQLite can return naive datetimes for timezone=True columns; treat them as UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def ai_snooze_is_active(row: User | None, *, now: datetime | None = None) -> bool:
    if row is None:
        return False
    until = getattr(row, "ai_snoozed_until", None)
    if until is None:
        return False
    t = _as_utc_aware(now or utc_now())
    return bool(_as_utc_aware(until) > t)


def snooze_until_for_preset(preset: AiSnoozePreset, org_timezone: str | None) -> datetime | None:
    """
    Момент окончания паузы в UTC.
    until_tomorrow — полночь следующего календарного дня в таймзоне филиала.
    """
    now = utc_now()
    if preset == "30m":
        return now + timedelta(minutes=30)
    if preset == "2h":
        return now + timedelta(hours=2)
    if preset == "until_tomorrow":
        tz_name = (org_timezone or "").strip() or "UTC"
        try:
            z = ZoneInfo(tz_name)
        except Exception:
            logger.warning("ai_snooze: неизвестная таймзона %r, используем UTC", tz_name)
            z = ZoneInfo("UTC")
        local = now.astimezone(z)
        next_cal = local.date() + timedelta(days=1)
        midnight_local = datetime.combine(next_cal, time(0, 0, 0), tzinfo=z)
        return midnight_local.astimezone(timezone.utc)
    return None


async def clear_ai_snooze_if_expired(db: AsyncSession, user: User, *, now: datetime | None = None) -> None:
    """Сбрасывает просроченный ai_snoozed_until (лениво при обработке сообщения)."""
    until = getattr(user, "ai_snoozed_until", None)
    if until is None:
        return
    t = _as_utc_aware(now or utc_now())
    if _as_utc_aware(until) <= t:
        user.ai_snoozed_until = None
        await db.flush()
