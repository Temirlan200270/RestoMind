"""Детерминированные короткие ответы без LLM (WhatsApp hot path)."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime

from app.core.config import settings
from app.db.models import Organization
from app.db.session import redis_client
from app.services.dialog_mgr import UserState
from app.services.time_context import (
    _WEEKDAY_KEYS,
    check_operational_status,
    parse_schedule_json,
)
from app.services.timezones import zoneinfo_or_default

logger = logging.getLogger(__name__)

_THANKS_PHRASES = frozenset({
    "спасибо", "благодарю", "сяй рахмет", "рахмет", "ok", "ок", "хорошо", "👍",
})
_OPERATOR_PHRASES = frozenset({
    "оператор", "человек", "менеджер", "позови человека", "соедини", "соедините",
})
_CANCEL_PHRASES = frozenset({
    "отмена", "отменить", "отменить заказ", "не нужно", "передумал",
})
_WORKING_HOURS_PHRASES = frozenset({
    "во сколько открываетесь",
    "время работы",
    "график",
    "работаете",
    "часы работы",
    "когда открываетесь",
    "до скольки работаете",
})


@dataclass(frozen=True, slots=True)
class QuickReplyHit:
    template_id: str
    reply_text: str
    new_state: UserState | None = None
    set_human_mode: bool = False
    side_effects: tuple[str, ...] = ()


def _normalize(text: str) -> str:
    return re.sub(r"[^\w\s]", " ", (text or "").lower(), flags=re.UNICODE).strip()


def is_plain_greeting(text: str) -> bool:
    """Простое приветствие без запроса (детерминированный short-circuit до LLM)."""
    raw = (text or "").strip().lower()
    if not raw:
        return False
    norm = re.sub(r"[^\w\s]+", " ", raw, flags=re.UNICODE)
    words = [w for w in re.split(r"\s+", norm) if w]
    if not words or len(words) > 4:
        return False
    greeting_words = {
        "привет", "здравствуйте", "здравствуй", "салам", "ассаламуалейкум",
        "hello", "hi", "добрый", "день", "вечер", "утро",
    }
    intent_words = {
        "меню", "заказ", "доставка", "самовывоз", "бронь", "бронирование",
        "адрес", "часы", "время", "order", "menu",
    }
    if any(w in intent_words for w in words):
        return False
    return all(w in greeting_words for w in words)


def greeting_reply() -> str:
    return "Здравствуйте! Чем могу помочь?"


def _match_trigger(text: str) -> str | None:
    norm = _normalize(text)
    if not norm or len(norm) > 40:
        return None
    if is_plain_greeting(text):
        return "greeting_plain"
    if norm in _THANKS_PHRASES:
        return "thanks"
    if norm in _OPERATOR_PHRASES:
        return "operator_request"
    if norm in _CANCEL_PHRASES or norm.startswith("отмен"):
        return "cancel_order"
    if norm in _WORKING_HOURS_PHRASES:
        return "working_hours"
    return None


def _format_working_hours_reply(org: Organization) -> str:
    op = check_operational_status(
        org.timezone,
        org.schedule_json,
        force_closed_until=org.force_closed_until,
    )
    sch = parse_schedule_json(org.schedule_json)
    tz = zoneinfo_or_default(org.timezone).zone
    now_local = datetime.now(tz=tz)
    day_key = _WEEKDAY_KEYS[now_local.weekday()]
    day = getattr(sch, day_key)
    if day.is_closed:
        sched_line = "Сегодня выходной."
    else:
        sched_line = (
            f"Сегодня {day.open}–{day.business_close}, "
            f"кухня до {day.kitchen_close}."
        )
    return f"{sched_line} {op.human_label}"


async def _bump_metric(org_id: int, template_id: str) -> None:
    if not settings.redis_enabled:
        return
    try:
        key = f"rm:metrics:quick_reply:{int(org_id)}:{template_id}:{date.today().isoformat()}"
        await redis_client.incr(key)
        await redis_client.expire(key, 7 * 86400)
    except Exception as exc:
        logger.debug("quick_reply metric incr failed org=%s tpl=%s: %s", org_id, template_id, exc)


async def try_quick_reply(
    *,
    phone: str,
    organization_id: int,
    message_text: str,
    state: UserState,
    has_open_draft: bool,
    org: Organization | None = None,
    user_locale: str = "ru",
) -> QuickReplyHit | None:
    if not settings.quick_replies_enabled:
        return None
    if not (message_text or "").strip():
        return None

    template_id = _match_trigger(message_text)
    if template_id is None:
        return None

    if template_id == "greeting_plain":
        if state != UserState.CHATTING:
            return None
        hit = QuickReplyHit(template_id=template_id, reply_text=greeting_reply())
    elif template_id == "thanks":
        if state not in (
            UserState.CHATTING,
            UserState.AWAITING_ORDER_PAYMENT,
            UserState.CONFIRMING_ORDER,
            UserState.CONFIRMING_BOOKING,
        ):
            return None
        hit = QuickReplyHit(
            template_id=template_id,
            reply_text="Пожалуйста! Чем ещё могу помочь?",
        )
    elif template_id == "operator_request":
        hit = QuickReplyHit(
            template_id=template_id,
            reply_text="Передаю менеджеру — он скоро ответит вам здесь.",
            new_state=UserState.HUMAN_MODE,
            set_human_mode=True,
            side_effects=("alert_operator_telegram",),
        )
    elif template_id == "cancel_order":
        if not has_open_draft:
            return None
        hit = QuickReplyHit(
            template_id=template_id,
            reply_text="Заказ отменён. Если что — пишите.",
            side_effects=("cancel_open_draft",),
        )
    elif template_id == "working_hours":
        if state != UserState.CHATTING or org is None:
            return None
        hit = QuickReplyHit(
            template_id=template_id,
            reply_text=_format_working_hours_reply(org),
        )
    else:
        return None

    await _bump_metric(organization_id, hit.template_id)
    logger.info(
        "quick_reply bypass org=%s phone=%s template=%s",
        organization_id,
        phone[-4:] if len(phone) >= 4 else "***",
        hit.template_id,
    )
    return hit
