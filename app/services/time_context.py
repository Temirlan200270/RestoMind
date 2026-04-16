"""
Контекст времени заведения для system prompt.

Задача: дать модели точное "сейчас" с учётом timezone организации, чтобы
она корректно реагировала на поздние сообщения и предлагала предзаказ.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo


_WEEKDAYS_RU: dict[int, str] = {
    0: "понедельник",
    1: "вторник",
    2: "среда",
    3: "четверг",
    4: "пятница",
    5: "суббота",
    6: "воскресенье",
}


@dataclass(frozen=True, slots=True)
class OrgCurrentTime:
    timezone: str
    iso_dt: str
    date: str
    time_hm: str
    weekday_ru: str


def build_org_current_time(timezone_name: str | None) -> OrgCurrentTime:
    tz = (timezone_name or "").strip() or "UTC"
    try:
        z = ZoneInfo(tz)
    except Exception:
        tz = "UTC"
        z = ZoneInfo("UTC")
    now = datetime.now(tz=z)
    wd = _WEEKDAYS_RU.get(int(now.weekday()), "—")
    return OrgCurrentTime(
        timezone=tz,
        iso_dt=now.isoformat(timespec="seconds"),
        date=now.strftime("%Y-%m-%d"),
        time_hm=now.strftime("%H:%M"),
        weekday_ru=wd,
    )


def format_org_current_time_block(timezone_name: str | None) -> str:
    """
    Строковый блок для system instruction.
    Важно: коротко, без лишних слов (token economy).
    """
    t = build_org_current_time(timezone_name)
    return (
        f"СЕЙЧАС (локально для заведения): {t.date} ({t.weekday_ru}), {t.time_hm}\n"
        f"TIMEZONE: {t.timezone}\n"
        f"ISO: {t.iso_dt}"
    )

