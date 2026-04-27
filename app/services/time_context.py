"""Контекст времени и расписания заведения для system prompt."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, field_validator


_WEEKDAYS_RU: dict[int, str] = {
    0: "понедельник",
    1: "вторник",
    2: "среда",
    3: "четверг",
    4: "пятница",
    5: "суббота",
    6: "воскресенье",
}
_WEEKDAY_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DEFAULT_ORG_TIMEZONE = "Asia/Almaty"


def _to_hhmm(v: str) -> str:
    s = (v or "").strip()
    if len(s) == 4 and s[1] == ":":
        s = f"0{s}"
    if len(s) != 5 or s[2] != ":":
        raise ValueError("Время должно быть в формате HH:MM")
    hh = int(s[:2])
    mm = int(s[3:])
    if hh < 0 or hh > 23 or mm < 0 or mm > 59:
        raise ValueError("Время должно быть в диапазоне 00:00..23:59")
    return f"{hh:02d}:{mm:02d}"


def _hm_to_minutes(s: str) -> int:
    hh, mm = s.split(":")
    return int(hh) * 60 + int(mm)


class DaySchedule(BaseModel):
    is_closed: bool = False
    open: str = "11:00"
    kitchen_close: str = "22:30"
    business_close: str = "23:00"

    @field_validator("open", "kitchen_close", "business_close")
    @classmethod
    def validate_hhmm(cls, v: str) -> str:
        return _to_hhmm(v)


class OrganizationSchedule(BaseModel):
    mon: DaySchedule = Field(default_factory=DaySchedule)
    tue: DaySchedule = Field(default_factory=DaySchedule)
    wed: DaySchedule = Field(default_factory=DaySchedule)
    thu: DaySchedule = Field(default_factory=DaySchedule)
    fri: DaySchedule = Field(default_factory=DaySchedule)
    sat: DaySchedule = Field(default_factory=DaySchedule)
    sun: DaySchedule = Field(default_factory=DaySchedule)


@dataclass(frozen=True, slots=True)
class OrgCurrentTime:
    timezone: str
    iso_dt: str
    date: str
    time_hm: str
    weekday_ru: str


@dataclass(frozen=True, slots=True)
class OperationalStatus:
    is_business_open: bool
    is_kitchen_open: bool
    next_business_open_at: str | None
    next_kitchen_open_at: str | None
    kitchen_closes_in_minutes: int | None
    human_label: str
    prompt_instruction: str


def default_schedule_json() -> dict[str, Any]:
    return OrganizationSchedule().model_dump(mode="json")


def parse_schedule_json(schedule_json: dict[str, Any] | None) -> OrganizationSchedule:
    src = schedule_json if isinstance(schedule_json, dict) else default_schedule_json()
    try:
        return OrganizationSchedule.model_validate(src)
    except Exception:
        return OrganizationSchedule()


def build_org_current_time(timezone_name: str | None) -> OrgCurrentTime:
    tz_in = (timezone_name or "").strip()
    tz = DEFAULT_ORG_TIMEZONE if not tz_in or tz_in.upper() == "UTC" else tz_in
    try:
        z = ZoneInfo(tz)
    except Exception:
        tz = DEFAULT_ORG_TIMEZONE
        z = ZoneInfo(DEFAULT_ORG_TIMEZONE)
    now = datetime.now(tz=z)
    wd = _WEEKDAYS_RU.get(int(now.weekday()), "—")
    return OrgCurrentTime(
        timezone=tz,
        iso_dt=now.isoformat(timespec="seconds"),
        date=now.strftime("%Y-%m-%d"),
        time_hm=now.strftime("%H:%M"),
        weekday_ru=wd,
    )


def _is_in_window(open_min: int, close_min: int, now_min: int) -> bool:
    if open_min == close_min:
        return False
    if close_min > open_min:
        return open_min <= now_min < close_min
    return now_min >= open_min or now_min < close_min


def _minutes_until_close(open_min: int, close_min: int, now_min: int) -> int | None:
    """Минут до закрытия текущего окна (если сейчас внутри окна), иначе None."""
    if not _is_in_window(open_min, close_min, now_min):
        return None
    if close_min > open_min:
        return max(0, close_min - now_min)
    # Ночное окно через полночь.
    if now_min >= open_min:
        return max(0, (24 * 60 - now_min) + close_min)
    return max(0, close_min - now_min)


def _next_open_local(now_local: datetime, sch: OrganizationSchedule) -> tuple[str | None, str | None]:
    for offset in range(0, 8):
        d = now_local + timedelta(days=offset)
        day_key = _WEEKDAY_KEYS[d.weekday()]
        ds = getattr(sch, day_key)
        if ds.is_closed:
            continue
        open_min = _hm_to_minutes(ds.open)
        cand = d.replace(hour=open_min // 60, minute=open_min % 60, second=0, microsecond=0)
        if offset == 0 and cand <= now_local:
            continue
        return cand.strftime("%Y-%m-%d %H:%M"), _WEEKDAYS_RU.get(d.weekday(), "")
    return None, None


def check_operational_status(
    timezone_name: str | None,
    schedule_json: dict[str, Any] | None,
    *,
    now: datetime | None = None,
) -> OperationalStatus:
    tz_in = (timezone_name or "").strip()
    tz = DEFAULT_ORG_TIMEZONE if not tz_in or tz_in.upper() == "UTC" else tz_in
    try:
        z = ZoneInfo(tz)
    except Exception:
        tz = DEFAULT_ORG_TIMEZONE
        z = ZoneInfo(DEFAULT_ORG_TIMEZONE)
    now_local = now.astimezone(z) if now is not None else datetime.now(tz=z)
    sch = parse_schedule_json(schedule_json)

    day_key = _WEEKDAY_KEYS[now_local.weekday()]
    day = getattr(sch, day_key)
    now_min = now_local.hour * 60 + now_local.minute

    business_open = False
    kitchen_open = False
    minutes_to_kitchen_close: int | None = None
    if not day.is_closed:
        open_min = _hm_to_minutes(day.open)
        b_close = _hm_to_minutes(day.business_close)
        k_close = _hm_to_minutes(day.kitchen_close)
        business_open = _is_in_window(open_min, b_close, now_min)
        kitchen_open = _is_in_window(open_min, k_close, now_min)
        if kitchen_open:
            minutes_to_kitchen_close = _minutes_until_close(open_min, k_close, now_min)

    # Ночная смена "со вчера": учитываем, если вчера закрытие после полуночи.
    prev_local = now_local - timedelta(days=1)
    prev_key = _WEEKDAY_KEYS[prev_local.weekday()]
    prev_day = getattr(sch, prev_key)
    if not prev_day.is_closed:
        prev_open = _hm_to_minutes(prev_day.open)
        prev_b_close = _hm_to_minutes(prev_day.business_close)
        prev_k_close = _hm_to_minutes(prev_day.kitchen_close)
        if prev_b_close < prev_open and now_min < prev_b_close:
            business_open = True
        if prev_k_close < prev_open and now_min < prev_k_close:
            kitchen_open = True
            minutes_to_kitchen_close = max(0, prev_k_close - now_min)

    next_open_at, next_open_wd = _next_open_local(now_local, sch)
    soft_close_warning = (
        kitchen_open and minutes_to_kitchen_close is not None and 0 < minutes_to_kitchen_close <= 20
    )

    if business_open and kitchen_open:
        label = "Заведение открыто, кухня принимает заказы."
        instr = (
            "OPERATIONAL_STATUS: BUSINESS_OPEN=1 KITCHEN_OPEN=1. "
            "Можно оформлять заказы на ближайшее время."
        )
        if soft_close_warning:
            instr += (
                " ПРЕДУПРЕДИ КЛИЕНТА: кухня закрывается совсем скоро, "
                "нужно определиться с заказом в ближайшие 5 минут."
            )
    elif business_open and not kitchen_open:
        label = "Заведение открыто, но кухня закрыта."
        instr = (
            "OPERATIONAL_STATUS: BUSINESS_OPEN=1 KITCHEN_OPEN=0. "
            "Консультируй по меню, но не обещай готовку сейчас; предложи предзаказ на ближайшее открытие кухни."
        )
    else:
        next_text = f" Ближайшее открытие: {next_open_at} ({next_open_wd})." if next_open_at else ""
        label = f"Заведение закрыто.{next_text}".strip()
        instr = (
            "OPERATIONAL_STATUS: BUSINESS_OPEN=0 KITCHEN_OPEN=0. "
            "Не принимай заказ на сейчас; предложи предзаказ на ближайшее открытие."
        )

    return OperationalStatus(
        is_business_open=business_open,
        is_kitchen_open=kitchen_open,
        next_business_open_at=next_open_at,
        next_kitchen_open_at=next_open_at,
        kitchen_closes_in_minutes=minutes_to_kitchen_close,
        human_label=label,
        prompt_instruction=instr,
    )


def format_org_current_time_block(
    timezone_name: str | None,
    schedule_json: dict[str, Any] | None = None,
) -> str:
    """Строковый блок для system instruction."""
    t = build_org_current_time(timezone_name)
    op = check_operational_status(timezone_name, schedule_json)
    kitchen_buffer = ""
    if op.kitchen_closes_in_minutes is not None and op.kitchen_closes_in_minutes > 0:
        kitchen_buffer = f"\nKITCHEN_CLOSES_IN_MINUTES: {op.kitchen_closes_in_minutes}"
    return (
        f"СЕЙЧАС (локально для заведения): {t.date} ({t.weekday_ru}), {t.time_hm}\n"
        f"TIMEZONE: {t.timezone}\n"
        f"ISO: {t.iso_dt}\n"
        f"OPERATIONAL_STATUS: {op.human_label}\n"
        f"{op.prompt_instruction}"
        f"{kitchen_buffer}"
    )

