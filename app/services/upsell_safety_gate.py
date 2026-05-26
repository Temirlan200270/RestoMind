"""Серверный запрет допродаж в неподходящих фазах диалога."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.schemas.ai_schemas import AIBrainResponse
from app.services.dialog_mgr import UserState

_SHORT_REPLY_RE = re.compile(
    r"^(?:да|нет|ок|ok|yes|no|ага|угу|спасибо|thanks|нал|наличн\w*|карт\w*|удал\w*|"
    r"подтвержда\w*|отмен\w*)[\s!.?,]*$",
    re.IGNORECASE,
)
_COMPLAINT_RE = re.compile(
    r"(долго|медлен|отстой|ужас|кошмар|хрен|на\s*хрен|жалоб|оператор|менеджер|"
    r"не\s+умеете|не\s+будем|отбой|разочар|бесит|задолб)",
    re.IGNORECASE,
)
_ORDER_START_RE = re.compile(
    r"(хочу\s+(?:сделать\s+)?заказ|можно\s+заказать|оформ\w+\s+заказ|что\s+(?:есть|посовет\w*))",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class UpsellSafetyContext:
    user_message: str = ""
    dialog_state: UserState | None = None
    order_meta: dict[str, Any] | None = None
    intent: str = ""


def upsell_suppression_reasons(ctx: UpsellSafetyContext) -> list[str]:
    reasons: list[str] = []
    msg = (ctx.user_message or "").strip()
    meta = ctx.order_meta if isinstance(ctx.order_meta, dict) else {}

    if ctx.intent == "escalate":
        reasons.append("intent_escalate")

    if ctx.dialog_state in (
        UserState.CONFIRMING_ORDER,
        UserState.AWAITING_ORDER_PAYMENT,
        UserState.HUMAN_MODE,
    ):
        reasons.append(f"state_{ctx.dialog_state.value}")

    if msg and _SHORT_REPLY_RE.match(msg):
        reasons.append("short_reply")

    if msg and _COMPLAINT_RE.search(msg):
        reasons.append("complaint_or_frustration")

    ot = str(meta.get("order_type") or "").strip().lower()
    pm = str(meta.get("payment_method") or "").strip().lower()
    addr = str(meta.get("delivery_address") or "").strip()
    pickup = str(meta.get("pickup_time_note") or "").strip()

    if ot == "delivery" and not addr:
        reasons.append("missing_delivery_address")
    if ot == "pickup" and not pickup:
        reasons.append("missing_pickup_time")
    if not pm and ot and (ot != "hall" or meta.get("requires_payment")):
        if meta.get("payment_method") is None and ctx.dialog_state == UserState.AWAITING_ORDER_PAYMENT:
            reasons.append("awaiting_payment")

    conf = meta.get("confidence")
    if isinstance(conf, dict) and conf.get("low_confidence"):
        reasons.append("low_confidence")

    trace = meta.get("recommendation_trace")
    if isinstance(trace, list) and len(trace) >= 2:
        rejected = sum(
            1 for ev in trace
            if isinstance(ev, dict) and str(ev.get("status") or "").lower() == "rejected"
        )
        if rejected >= 1:
            reasons.append("recent_rejection")

    return reasons


def should_suppress_upsell(ctx: UpsellSafetyContext) -> bool:
    return bool(upsell_suppression_reasons(ctx))


def strip_upsell_from_ai_response(ai: AIBrainResponse) -> AIBrainResponse:
    """Убирает upsell-поля из structured output до route_intent."""
    if not (
        ai.is_recommendation
        or (ai.upsell_offered or "").strip()
        or (getattr(ai, "upsell_offered_id", None) or "").strip()
    ):
        return ai
    return ai.model_copy(
        update={
            "is_recommendation": False,
            "upsell_offered": "",
            "upsell_offered_id": "",
            "upsell_reasoning": "",
        },
    )


def is_order_start_without_items(message_text: str) -> bool:
    """Гость начинает заказ без конкретных блюд — не ошибка парсинга."""
    text = (message_text or "").strip()
    if not text:
        return False
    return bool(_ORDER_START_RE.search(text))
