"""Расписание казанов для плова — ближайший слот и подсказки гостю."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from app.core.config import settings
from app.db.models import MenuItem
from app.schemas.ai_schemas import AIBrainResponse
from app.services.time_context import build_org_current_time, parse_schedule_json
from app.services.timezones import zoneinfo_or_default


DEFAULT_PLOV_KAZAN_BATCH_TIMES: tuple[str, ...] = ("12:00", "16:00", "19:00")

_PLOV_TOKENS = ("плов", "plov")


def _norm(s: str) -> str:
    return (s or "").lower().strip()


def _parse_hhmm_list(raw: str) -> tuple[str, ...]:
    parts: list[str] = []
    for chunk in (raw or "").replace(";", ",").split(","):
        s = chunk.strip()
        if not s:
            continue
        if len(s) == 4 and s[1] == ":":
            s = f"0{s}"
        if len(s) == 5 and s[2] == ":":
            hh, mm = s.split(":")
            if hh.isdigit() and mm.isdigit():
                parts.append(f"{int(hh):02d}:{int(mm):02d}")
    return tuple(sorted(set(parts)))


def resolve_plov_kazan_batch_times(org_meta_json: dict[str, Any] | None = None) -> tuple[str, ...]:
    """Org override в meta_json.plov_kazan_batch_times или env PLOV_KAZAN_BATCH_TIMES."""
    if isinstance(org_meta_json, dict):
        raw = org_meta_json.get("plov_kazan_batch_times")
        if isinstance(raw, list):
            parsed = _parse_hhmm_list(",".join(str(x) for x in raw))
            if parsed:
                return parsed
        if isinstance(raw, str) and raw.strip():
            parsed = _parse_hhmm_list(raw)
            if parsed:
                return parsed
    env_raw = getattr(settings, "plov_kazan_batch_times", "") or ""
    parsed = _parse_hhmm_list(env_raw)
    return parsed or DEFAULT_PLOV_KAZAN_BATCH_TIMES


def is_plov_menu_item(item: MenuItem) -> bool:
    """Любая позиция меню с пловом в названии или категории."""
    name = _norm(getattr(item, "name", "") or "")
    cat = _norm(getattr(item, "category", "") or "")
    blob = f"{name} {cat}"
    return any(t in blob for t in _PLOV_TOKENS)


def is_portion_plov_menu_item(item: MenuItem) -> bool:
    """Обратная совместимость — см. is_plov_menu_item."""
    return is_plov_menu_item(item)


def stopped_plov_items(menu_items: list[MenuItem]) -> list[str]:
    names: list[str] = []
    for item in menu_items:
        if getattr(item, "is_available", True):
            continue
        if is_plov_menu_item(item):
            nm = (getattr(item, "name", "") or "").strip()
            if nm:
                names.append(nm)
    return names


def stopped_portion_plov_items(menu_items: list[MenuItem]) -> list[str]:
    """Обратная совместимость — см. stopped_plov_items."""
    return stopped_plov_items(menu_items)


def plov_on_stop(menu_items: list[MenuItem]) -> bool:
    return bool(stopped_plov_items(menu_items))


def portion_plov_on_stop(menu_items: list[MenuItem]) -> bool:
    """Обратная совместимость — см. plov_on_stop."""
    return plov_on_stop(menu_items)


@dataclass(frozen=True, slots=True)
class KazanBatchInfo:
    batch_times: tuple[str, ...]
    next_batch_at: datetime
    next_batch_hm: str
    wait_minutes: int
    is_today: bool


def _hm_to_minutes(hm: str) -> int:
    hh, mm = hm.split(":")
    return int(hh) * 60 + int(mm)


def compute_next_kazan_batch(
    *,
    timezone_name: str | None,
    batch_times: tuple[str, ...] | None = None,
    now: datetime | None = None,
) -> KazanBatchInfo | None:
    times = batch_times or DEFAULT_PLOV_KAZAN_BATCH_TIMES
    if not times:
        return None
    z = zoneinfo_or_default(timezone_name).zone
    ref = now.astimezone(z) if now is not None else datetime.now(tz=z)
    now_min = ref.hour * 60 + ref.minute

    for hm in times:
        cand_min = _hm_to_minutes(hm)
        if cand_min > now_min:
            cand = ref.replace(hour=cand_min // 60, minute=cand_min % 60, second=0, microsecond=0)
            wait = cand_min - now_min
            return KazanBatchInfo(
                batch_times=times,
                next_batch_at=cand,
                next_batch_hm=hm,
                wait_minutes=max(0, wait),
                is_today=True,
            )

    first = times[0]
    first_min = _hm_to_minutes(first)
    next_day = (ref + timedelta(days=1)).replace(
        hour=first_min // 60,
        minute=first_min % 60,
        second=0,
        microsecond=0,
    )
    wait = int((next_day - ref).total_seconds() // 60)
    return KazanBatchInfo(
        batch_times=times,
        next_batch_at=next_day,
        next_batch_hm=first,
        wait_minutes=max(0, wait),
        is_today=False,
    )


def _format_wait_ru(minutes: int) -> str:
    if minutes < 60:
        return f"около {minutes} мин"
    hours = minutes // 60
    mins = minutes % 60
    if mins == 0:
        return f"около {hours} ч"
    return f"около {hours} ч {mins} мин"


def format_plov_kazan_schedule_prompt_block(
    menu_items: list[MenuItem],
    *,
    timezone_name: str | None,
    org_meta_json: dict[str, Any] | None = None,
    schedule_json: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> str:
    """Блок для system prompt, когда любой плов на стопе."""
    stopped = stopped_plov_items(menu_items)
    if not stopped:
        return ""

    batch_times = resolve_plov_kazan_batch_times(org_meta_json)
    batch = compute_next_kazan_batch(
        timezone_name=timezone_name,
        batch_times=batch_times,
        now=now,
    )
    if batch is None:
        return ""

    t = build_org_current_time(timezone_name)
    times_line = ", ".join(batch.batch_times)
    wait_label = _format_wait_ru(batch.wait_minutes)
    day_hint = "сегодня" if batch.is_today else "завтра"
    stopped_line = ", ".join(stopped[:8])

    z = zoneinfo_or_default(timezone_name).zone
    ref = now.astimezone(z) if now is not None else datetime.now(tz=z)
    day_key = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")[ref.weekday()]
    day_sched = getattr(parse_schedule_json(schedule_json), day_key, None)
    open_hm = getattr(day_sched, "open", "11:00") if day_sched else "11:00"

    lines = [
        "# Плов на стопе — расписание казанов (критично)",
        f"Сейчас у заведения: {t.date} {t.time_hm} ({t.weekday_ru}).",
        f"На стопе (плов): {stopped_line}.",
        f"Казаны по плову открываются в: {times_line}.",
        f"Ближайший слот: {day_hint} в *{batch.next_batch_hm}* (ожидание {wait_label}).",
    ]
    if t.time_hm < open_hm and batch.is_today:
        lines.append(f"Заведение открывается в {open_hm} — учитывай это вместе со слотом казана.")

    lines.extend([
        "INSTRUCTION:",
        "- Если гость спрашивает «есть плов?» / «плов или нет?» — честно перечисли, что на стопе из списка выше.",
        f"- Обязательно назови ближайшее время появления плова из казана: *{batch.next_batch_hm}* ({day_hint}, {wait_label}).",
        "- Спроси: «Подождёте до этого времени или оформить предзаказ к слоту?» / «Устроит?»",
        "- Если какие-то виды плова в меню без [СТОП] — их можно предложить; на стопе — только после слота казана.",
        "- Не обещай «прямо сейчас», если текущее время до ближайшего слота казана.",
    ])
    return "\n".join(lines)


def format_plov_kazan_guest_hint(
    menu_items: list[MenuItem],
    *,
    timezone_name: str | None,
    org_meta_json: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> str:
    """Короткая детерминированная вставка для ответа гостю (fallback)."""
    stopped = stopped_plov_items(menu_items)
    if not stopped:
        return ""
    batch_times = resolve_plov_kazan_batch_times(org_meta_json)
    batch = compute_next_kazan_batch(
        timezone_name=timezone_name,
        batch_times=batch_times,
        now=now,
    )
    if batch is None:
        return ""
    day_hint = "сегодня" if batch.is_today else "завтра"
    wait_label = _format_wait_ru(batch.wait_minutes)
    times_line = ", ".join(batch_times)
    stopped_short = ", ".join(stopped[:3])
    return (
        f"Сейчас на стопе: {stopped_short}. Казаны по плову открываются в {times_line}; "
        f"ближайший слот — {day_hint} в *{batch.next_batch_hm}* ({wait_label}). "
        "Устроит подождать или оформить предзаказ к этому времени?"
    )


_PLOV_QUERY_RE = re.compile(r"плов|plov", re.IGNORECASE)


def enrich_plov_kazan_reply_if_needed(
    ai_response: AIBrainResponse,
    user_message: str,
    menu_items: list[MenuItem],
    *,
    timezone_name: str | None,
    org_meta_json: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> AIBrainResponse:
    """Дополняет ответ, если гость спрашивает про плов, а LLM не назвал слот казана."""
    if not _PLOV_QUERY_RE.search(user_message or ""):
        return ai_response
    if not plov_on_stop(menu_items):
        return ai_response

    batch_times = resolve_plov_kazan_batch_times(org_meta_json)
    batch = compute_next_kazan_batch(
        timezone_name=timezone_name,
        batch_times=batch_times,
        now=now,
    )
    if batch is None:
        return ai_response

    reply = ai_response.reply_text or ""
    if batch.next_batch_hm in reply:
        return ai_response
    if "казан" in reply.lower() and "стоп" in reply.lower():
        return ai_response

    hint = format_plov_kazan_guest_hint(
        menu_items,
        timezone_name=timezone_name,
        org_meta_json=org_meta_json,
        now=now,
    )
    if not hint:
        return ai_response

    merged = reply.strip()
    if merged:
        merged = f"{merged}\n\n{hint}"
    else:
        merged = hint
    return ai_response.model_copy(update={"reply_text": merged})
