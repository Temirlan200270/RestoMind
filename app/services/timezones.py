from __future__ import annotations

import re
from dataclasses import dataclass
from zoneinfo import ZoneInfo


@dataclass(frozen=True, slots=True)
class NormalizedTimezone:
    name: str
    zone: ZoneInfo


_UTC_OFFSET_RE = re.compile(
    r"^(?:(?:UTC|GMT)\s*)?([+-])\s*(\d{1,2})(?::?\s*(\d{2}))?$",
    flags=re.IGNORECASE,
)


def _strip_parens(s: str) -> str:
    # "UTC+5 (Казахстан)" -> "UTC+5"
    i = s.find("(")
    if i >= 0:
        return s[:i].strip()
    return s.strip()


def _offset_hours_to_etc_gmt(hours: int) -> str:
    # IANA "Etc/GMT" has inverted sign: UTC+5 => Etc/GMT-5
    if hours == 0:
        return "UTC"
    sign = "-" if hours > 0 else "+"
    return f"Etc/GMT{sign}{abs(hours)}"


def normalize_timezone_name(
    tz_name: str | None,
    *,
    default: str = "Asia/Almaty",
) -> str:
    """
    Нормализовать строку часового пояса до ключа, который понимает zoneinfo.

    Поддержка:
    - IANA: "Asia/Almaty"
    - Offset формы: "UTC+5", "UTC +5", "GMT-3", "+5", "-10"
    - С пометкой в скобках: "UTC+5 (Казахстан)"

    Если распознать невозможно — возвращает default.
    """
    raw = (tz_name or "").strip()
    if not raw:
        return default
    s = _strip_parens(raw)
    if not s:
        return default
    if s.upper() == "UTC":
        return "UTC"

    # 1) Уже IANA?
    try:
        ZoneInfo(s)
        return s
    except Exception:
        pass

    # 2) Offset?
    m = _UTC_OFFSET_RE.match(s.replace("−", "-"))  # иногда минус бывает U+2212
    if m:
        sign_s, hh_s, mm_s = m.group(1), m.group(2), m.group(3)
        hh = int(hh_s)
        mm = int(mm_s) if mm_s is not None else 0
        # Только целые часы: "Etc/GMT" не поддерживает минуты (Asia/Kathmandu и т.п. — только IANA).
        if mm != 0 or hh > 14:
            return default
        signed_h = hh if sign_s == "+" else -hh
        etc = _offset_hours_to_etc_gmt(signed_h)
        try:
            ZoneInfo(etc)
            return etc
        except Exception:
            return default

    return default


def zoneinfo_or_default(
    tz_name: str | None,
    *,
    default: str = "Asia/Almaty",
) -> NormalizedTimezone:
    """Вернуть валидный ZoneInfo и нормализованное имя."""
    name = normalize_timezone_name(tz_name, default=default)
    # name всегда должен быть валидным ключом, но на всякий случай — fallback.
    try:
        return NormalizedTimezone(name=name, zone=ZoneInfo(name))
    except Exception:
        return NormalizedTimezone(name=default, zone=ZoneInfo(default))

