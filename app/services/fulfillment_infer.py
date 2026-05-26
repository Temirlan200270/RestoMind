"""Детерминированное извлечение типа получения и времени из текста гостя."""

from __future__ import annotations

import re

from app.schemas.ai_schemas import AIBrainResponse

_PICKUP_RE = re.compile(
    r"(самовывоз|с\s*собой|на\s*вынос|заберу|забрать|pickup)",
    re.IGNORECASE,
)
_DELIVERY_RE = re.compile(
    r"(доставк|привез|привезти|delivery|курьер)",
    re.IGNORECASE,
)
_HALL_RE = re.compile(
    r"(в\s*зал|в\s*зале|в\s*ресторан|на\s*месте|hall)",
    re.IGNORECASE,
)
_PICKUP_TIME_RE = re.compile(
    r"(?:"
    r"через\s+(?P<rel>\d+\s*(?:мин|минут|час|часа|часов))"
    r"|к\s+(?P<clock>\d{1,2}[:\.]\d{2})"
    r"|на\s+(?P<tomorrow>завтра)(?:\s+к\s+(?P<tomorrow_clock>\d{1,2}[:\.]?\d{0,2}))?"
    r"|(?P<half>полчаса|пол\s*часа)"
    r")",
    re.IGNORECASE,
)


def _extract_pickup_time_note(text: str) -> str:
    compact = re.sub(r"\s+", " ", (text or "").strip())
    if not compact:
        return ""
    m = _PICKUP_TIME_RE.search(compact)
    if not m:
        return ""
    if m.group("half"):
        return "через 30 минут"
    if m.group("rel"):
        return f"через {m.group('rel').strip()}"
    if m.group("tomorrow"):
        tc = (m.group("tomorrow_clock") or "").strip()
        return f"завтра к {tc}" if tc else "завтра"
    if m.group("clock"):
        return f"к {m.group('clock').replace('.', ':')}"
    return ""


def enrich_ai_fulfillment_from_message(
    ai: AIBrainResponse,
    message_text: str,
    *,
    has_draft: bool = False,
) -> AIBrainResponse:
    """
    Если LLM не заполнил order_type / pickup_time_note, но гость явно написал —
    дополняем ответ до Decision Engine / route_intent.
    """
    text = (message_text or "").strip()
    if not text:
        return ai

    updates: dict[str, object] = {}
    ot = (ai.order_type or "").strip().lower()

    if not ot:
        if _PICKUP_RE.search(text):
            updates["order_type"] = "pickup"
        elif _DELIVERY_RE.search(text):
            updates["order_type"] = "delivery"
        elif _HALL_RE.search(text):
            updates["order_type"] = "hall"

    effective_ot = str(updates.get("order_type") or ot or "").lower()
    pickup_note = (ai.pickup_time_note or "").strip()
    if not pickup_note and effective_ot == "pickup":
        inferred = _extract_pickup_time_note(text)
        if inferred:
            updates["pickup_time_note"] = inferred

    if not updates:
        return ai

    if has_draft and ai.intent not in ("order", "faq"):
        updates.setdefault("intent", "order")

    return ai.model_copy(update=updates)
