"""Детерминированные короткие ответы без LLM (WhatsApp hot path)."""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import Order, OrderStatus, Organization, User
from app.db.session import redis_client
from app.services.dialog_mgr import UserState
from app.services.order_logic import format_menu_category_for_guest, load_available_menu
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
_MENU_PHRASES = frozenset({
    "меню",
    "menu",
    "покажите меню",
    "покажи меню",
    "что есть",
    "ассортимент",
    "что у вас",
    "что можно заказать",
})
_ORDER_STATUS_PHRASES = frozenset({
    "статус заказа",
    "статус моего заказа",
    "где мой заказ",
    "где заказ",
    "мой заказ",
    "как мой заказ",
    "проверь заказ",
})
_MORE_RECOMMENDATION_PHRASES = frozenset({
    "что еще",
    "что ещё",
    "еще",
    "ещё",
    "а еще",
    "а ещё",
    "еще варианты",
    "ещё варианты",
    "что-нибудь еще",
    "что-нибудь ещё",
})
_GENERAL_RECOMMENDATION_PHRASES = frozenset({
    "что предложишь",
    "что предложите",
    "что можете предложить",
    "что предложишь сегодня",
    "что предложите сегодня",
})

_ACTIVE_ORDER_STATUSES = (
    OrderStatus.CONFIRMED.value,
    OrderStatus.SENDING_TO_IIKO.value,
    OrderStatus.SENT_TO_IIKO.value,
    OrderStatus.IN_TRANSIT.value,
    OrderStatus.WAITING_PICKUP.value,
)

_STATUS_LABELS_RU: dict[str, str] = {
    OrderStatus.DRAFT.value: "черновик — ждёт подтверждения",
    OrderStatus.CONFIRMED.value: "подтверждён",
    OrderStatus.SENDING_TO_IIKO.value: "отправляется на кухню",
    OrderStatus.SENT_TO_IIKO.value: "на кухне",
    OrderStatus.IN_TRANSIT.value: "в пути",
    OrderStatus.WAITING_PICKUP.value: "готов к выдаче",
    OrderStatus.COMPLETED.value: "выполнен",
    OrderStatus.CANCELLED.value: "отменён",
}


@dataclass(frozen=True, slots=True)
class QuickReplyHit:
    template_id: str
    reply_text: str
    new_state: UserState | None = None
    set_human_mode: bool = False
    side_effects: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class QuickReplyPreload:
    org: Organization | None
    has_open_draft: bool
    menu_preview: str | None = None
    recommendation_preview: str | None = None
    menu_probe_text: str | None = None
    order_status_text: str | None = None


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
    return "Здравствуйте! Что бы вы хотели заказать? Могу подсказать по меню и помочь оформить заказ."


def peek_quick_reply_trigger(text: str) -> str | None:
    """Синхронный peek для preload в webhooks (без DB)."""
    return _match_trigger(text)


def _match_trigger(text: str) -> str | None:
    norm = _normalize(text)
    if not norm:
        return None
    if is_plain_greeting(text):
        return "greeting_plain"
    from app.services.upsell_safety_gate import is_recommendation_request

    if _looks_like_menu_probe(norm) or _looks_like_specific_menu_probe(norm):
        return "menu_probe"
    if len(norm) <= 80 and (
        is_recommendation_request(text)
        or norm in _GENERAL_RECOMMENDATION_PHRASES
        or norm in {"подскажите", "подскажи"}
        or norm in _MORE_RECOMMENDATION_PHRASES
    ):
        return "recommendation_request"
    if len(norm) > 40:
        return None
    if norm in _THANKS_PHRASES:
        return "thanks"
    if norm in _OPERATOR_PHRASES:
        return "operator_request"
    if norm in _CANCEL_PHRASES or norm.startswith("отмен"):
        return "cancel_order"
    if norm in _WORKING_HOURS_PHRASES:
        return "working_hours"
    if norm in _MENU_PHRASES:
        return "menu_request"
    if norm in _ORDER_STATUS_PHRASES:
        return "order_status"
    if _looks_like_menu_probe(norm):
        return "menu_probe"
    return None


def _looks_like_menu_probe(norm: str) -> bool:
    """Short menu/item/category clarification that should not wait for LLM."""
    words = [w for w in re.split(r"\s+", norm) if w]
    if not words or len(words) > 5:
        return False
    if norm in {"да", "нет", "не", "ок", "ладно"}:
        return False
    food_hints = (
        "плов", "мяс", "говяд", "баран", "кебаб", "шашлык", "лагман", "манты",
        "самса", "салат", "суп", "напит", "чай", "лимонад", "десерт", "сытн",
        "легк", "лёгк",
    )
    question_hints = ("есть", "какой", "какая", "какие", "давай")
    if any(h in norm for h in food_hints):
        return True
    return any(h in norm for h in question_hints) and len(words) <= 4


def _looks_like_specific_menu_probe(norm: str) -> bool:
    """Composite menu question: concrete food/category beats generic recommendation words."""
    words = [w for w in re.split(r"\s+", norm) if w]
    if not words or len(words) > 22:
        return False
    food_hints = (
        "плов", "мяс", "говяд", "баран", "кебаб", "шашлык", "лагман", "манты",
        "самса", "салат", "суп", "напит", "чай", "лимонад", "десерт", "сытн",
        "легк", "лёгк",
    )
    question_hints = (
        "есть", "какой", "какая", "какие", "вид", "виды", "посовет",
        "порекоменду", "предлож", "подскаж", "давай", "хочу",
    )
    return any(h in norm for h in food_hints) and any(h in norm for h in question_hints)


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


def _order_item_names(order: Order, *, limit: int = 4) -> list[str]:
    raw = order.items_json if isinstance(order.items_json, dict) else {}
    items_list = raw.get("items")
    names: list[str] = []
    if isinstance(items_list, list):
        for it in items_list:
            if isinstance(it, dict) and it.get("name"):
                names.append(str(it["name"]).strip())
            if len(names) >= limit:
                break
    return names


def _format_order_status_reply(order: Order) -> str:
    status = str(order.status or OrderStatus.DRAFT.value)
    label = _STATUS_LABELS_RU.get(status, status)
    names = _order_item_names(order)
    items_line = ", ".join(names) if names else "состав уточняется"
    total = float(order.total_price or 0)
    total_line = f" на {total:,.0f} ₸".replace(",", " ") if total > 0 else ""
    return f"Заказ: {items_line}{total_line}. Статус: {label}."


async def build_menu_quick_reply_text(db: AsyncSession, organization_id: int) -> str:
    items = await load_available_menu(
        db,
        organization_id=organization_id,
        include_unavailable=False,
    )
    by_cat: dict[str, list[str]] = defaultdict(list)
    cat_order: list[str] = []
    for item in items:
        if not item.is_available:
            continue
        display_cat = format_menu_category_for_guest(item.category or "")
        if display_cat not in by_cat:
            cat_order.append(display_cat)
        if len(by_cat[display_cat]) < 3:
            by_cat[display_cat].append(str(item.name or "").strip())

    lines: list[str] = []
    for cat in cat_order[:6]:
        sample = ", ".join(by_cat[cat][:3])
        if sample:
            lines.append(f"• {cat}: {sample}")

    menu_url = (settings.menu_public_url or "").strip()
    if not lines:
        if menu_url:
            return f"Меню обновляется. Полный список блюд: {menu_url}"
        return "Меню временно недоступно — напишите, что хотите заказать, и я помогу."

    body = "Кратко по меню:\n" + "\n".join(lines)
    if menu_url:
        body += f"\n\nПолное меню: {menu_url}"
    return body[:600]


async def build_recommendation_quick_reply_text(db: AsyncSession, organization_id: int) -> str:
    items = await load_available_menu(
        db,
        organization_id=organization_id,
        include_unavailable=False,
    )
    return build_recommendation_quick_reply_from_items(items)


def build_recommendation_quick_reply_from_items(items: list[object]) -> str:
    available = [
        i
        for i in items
        if bool(getattr(i, "is_available", True)) and str(getattr(i, "name", "") or "").strip()
    ]
    if not available:
        return "Могу подсказать по меню, но список блюд сейчас обновляется. Напишите, что любите: сытное, лёгкое, мясное или напиток."

    def score(item) -> tuple[int, str]:
        blob = f"{item.name or ''} {item.category or ''} {item.tags or ''}".lower()
        points = 0
        if any(k in blob for k in ("хит", "популяр", "рекоменд")):
            points += 50
        if any(k in blob for k in ("плов", "манты", "шашлык", "лагман", "самса")):
            points += 30
        if any(k in blob for k in ("кофе", "напит", "чай", "лимонад", "десерт")):
            points -= 10
        return (-points, str(item.name or ""))

    picks = sorted(available, key=score)[:3]
    lines: list[str] = []
    for item in picks:
        price = float(item.price or 0)
        price_part = f" — {price:.0f} ₸" if price > 0 else ""
        lines.append(f"• {str(item.name).strip()}{price_part}")

    return (
        "Из популярного могу посоветовать:\n"
        + "\n".join(lines)
        + "\n\nЧто добавить в заказ? Если хотите, подберу под вкус: сытное, лёгкое, мясное или напиток."
    )


def _menu_item_line(item) -> str:
    name = str(item.name or "").strip()
    price = float(item.price or 0)
    return f"• {name} — {price:.0f} ₸" if price > 0 else f"• {name}"


def _probe_terms(text: str) -> list[str]:
    norm = _normalize(text)
    stop = {
        "есть", "какой", "какая", "какие", "что", "давай", "дайте", "хочу",
        "можно", "у", "вас", "мне", "пожалуйста", "подскажите", "подскажи",
    }
    return [w for w in re.split(r"\s+", norm) if len(w) >= 3 and w not in stop]


async def build_menu_probe_quick_reply_text(
    db: AsyncSession,
    organization_id: int,
    message_text: str,
) -> str | None:
    items = await load_available_menu(
        db,
        organization_id=organization_id,
        include_unavailable=True,
    )
    return _build_menu_probe_reply_from_items(items, message_text)


def _build_menu_probe_reply_from_items(items: list[object], message_text: str) -> str | None:
    named = [i for i in items if str(getattr(i, "name", "") or "").strip()]
    available = [i for i in named if bool(getattr(i, "is_available", True))]
    unavailable = [i for i in named if not bool(getattr(i, "is_available", True))]
    if not named:
        return None

    norm = _normalize(message_text)
    terms = _probe_terms(message_text)

    def blob(item) -> str:
        return f"{getattr(item, 'name', '') or ''} {getattr(item, 'category', '') or ''} {getattr(item, 'tags', '') or ''}".lower()

    def unique(items_in: list[object], *, limit: int = 5) -> list[object]:
        seen: set[str] = set()
        out: list[object] = []
        for item in items_in:
            key = str(getattr(item, "name", "") or "").strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(item)
            if len(out) >= limit:
                break
        return out

    def by_needles(source: list[object], needles: tuple[str, ...], *, limit: int = 5) -> list[object]:
        return unique([item for item in source if any(k in blob(item) for k in needles)], limit=limit)

    def section(title: str, rows: list[object]) -> str:
        lines = "\n".join(_menu_item_line(item) for item in rows)
        return f"{title}:\n{lines}" if lines else ""

    mentions_plov = "плов" in norm
    mentions_salad = "салат" in norm
    if mentions_plov or mentions_salad:
        parts: list[str] = []
        if mentions_plov:
            plov_rows = by_needles(available, ("плов",), limit=6)
            if plov_rows:
                parts.append(section("По плову есть", plov_rows))
            stopped_plov = by_needles(unavailable, ("плов",), limit=4)
            if stopped_plov:
                stopped_names = ", ".join(str(getattr(item, "name", "") or "").strip() for item in stopped_plov)
                parts.append(f"Сейчас на стопе: {stopped_names}.")
        if mentions_salad:
            salad_rows = by_needles(available, ("салат",), limit=8)
            if "сытн" in norm:
                hearty = (
                    "мяс", "говяд", "баран", "кур", "цыпл", "тауык", "люля",
                    "плов", "нут", "сыр", "яйц", "хрустящ",
                )
                salad_rows = sorted(
                    salad_rows,
                    key=lambda item: 0 if any(k in blob(item) for k in hearty) else 1,
                )
            salad_rows = unique(salad_rows, limit=3)
            if salad_rows:
                title = "Из сытных салатов могу предложить" if "сытн" in norm else "По салатам есть"
                parts.append(section(title, salad_rows))
        if parts:
            cta = "Какой добавить в заказ?" if mentions_plov and not mentions_salad else "Что добавить в заказ?"
            return "\n\n".join(parts) + f"\n\n{cta}"

    category_title = ""
    category_needles: tuple[str, ...] = ()
    if "мяс" in norm:
        category_title = "Из мясного могу предложить"
        category_needles = ("мяс", "говяд", "баран", "кебаб", "шашлык", "манты", "дапанжи", "казан")
    elif "сытн" in norm:
        category_title = "Из сытного могу предложить"
        category_needles = ("плов", "лагман", "манты", "кебаб", "дапанжи", "казан", "говяд", "баран")
    elif "легк" in norm or "лёгк" in norm:
        category_title = "Из лёгкого могу предложить"
        category_needles = ("салат", "суп", "овощ", "чай", "напит")
    elif "напит" in norm or "чай" in norm or "лимонад" in norm:
        category_title = "Из напитков могу предложить"
        category_needles = ("напит", "чай", "лимонад", "сок", "компот", "кофе")

    candidates = []
    if category_needles:
        candidates = [item for item in available if any(k in blob(item) for k in category_needles)]
    elif terms:
        candidates = [
            item
            for item in available
            if all(term in blob(item) for term in terms) or " ".join(terms) in blob(item)
        ]

    unique_items = unique(candidates, limit=5)

    if not unique_items:
        return None

    lines = "\n".join(_menu_item_line(item) for item in unique_items[:5])
    if category_title:
        return f"{category_title}:\n{lines}\n\nЧто добавить в заказ?"

    query_label = " ".join(terms).strip() or norm
    if len(unique_items) == 1:
        return f"Да, есть:\n{lines}\n\nДобавить в заказ?"
    return f"По «{query_label}» есть варианты:\n{lines}\n\nКакой добавить в заказ?"


async def build_order_status_quick_reply_text(
    db: AsyncSession,
    *,
    phone: str,
    organization_id: int,
    draft_row: Order | None,
) -> str:
    if draft_row is not None:
        return _format_order_status_reply(draft_row)

    phone_s = (phone or "").strip()
    user = await db.scalar(
        select(User).where(
            User.phone == phone_s,
            User.organization_id == organization_id,
        ),
    )
    if user is None:
        return "Активных заказов не нашёл. Напишите, что хотите заказать — оформлю."

    order = await db.scalar(
        select(Order)
        .where(
            Order.user_id == user.id,
            Order.organization_id == organization_id,
            Order.status.in_(_ACTIVE_ORDER_STATUSES),
        )
        .order_by(desc(Order.created_at))
        .limit(1),
    )
    if order is None:
        order = await db.scalar(
            select(Order)
            .where(
                Order.user_id == user.id,
                Order.organization_id == organization_id,
                Order.status != OrderStatus.CANCELLED.value,
            )
            .order_by(desc(Order.created_at))
            .limit(1),
        )
    if order is None:
        return "Активных заказов не нашёл. Напишите, что хотите заказать — оформлю."

    return _format_order_status_reply(order)


async def load_quick_reply_preload(
    db: AsyncSession,
    *,
    phone: str,
    organization_id: int,
    message_text: str,
) -> QuickReplyPreload:
    """Один DB roundtrip для quick reply (org + draft + опционально menu/status)."""
    org = await db.get(Organization, organization_id)
    from app.services.intent_router import get_open_draft_order

    draft_row = await get_open_draft_order(db, phone, organization_id)
    trigger = peek_quick_reply_trigger(message_text)
    menu_preview: str | None = None
    recommendation_preview: str | None = None
    menu_probe_text: str | None = None
    order_status_text: str | None = None
    if trigger == "menu_request":
        menu_preview = await build_menu_quick_reply_text(db, organization_id)
    elif trigger == "recommendation_request":
        recommendation_preview = await build_recommendation_quick_reply_text(db, organization_id)
    elif trigger == "menu_probe":
        menu_probe_text = await build_menu_probe_quick_reply_text(db, organization_id, message_text)
    elif trigger == "order_status":
        order_status_text = await build_order_status_quick_reply_text(
            db,
            phone=phone,
            organization_id=organization_id,
            draft_row=draft_row,
        )
    return QuickReplyPreload(
        org=org,
        has_open_draft=draft_row is not None,
        menu_preview=menu_preview,
        recommendation_preview=recommendation_preview,
        menu_probe_text=menu_probe_text,
        order_status_text=order_status_text,
    )


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
    menu_preview: str | None = None,
    recommendation_preview: str | None = None,
    menu_probe_text: str | None = None,
    order_status_text: str | None = None,
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
    elif template_id == "menu_request":
        if state != UserState.CHATTING or not menu_preview:
            return None
        hit = QuickReplyHit(template_id=template_id, reply_text=menu_preview)
    elif template_id == "recommendation_request":
        if state != UserState.CHATTING or not recommendation_preview:
            return None
        hit = QuickReplyHit(template_id=template_id, reply_text=recommendation_preview)
    elif template_id == "menu_probe":
        if state != UserState.CHATTING or not menu_probe_text:
            return None
        hit = QuickReplyHit(template_id=template_id, reply_text=menu_probe_text)
    elif template_id == "order_status":
        if not order_status_text:
            return None
        hit = QuickReplyHit(template_id=template_id, reply_text=order_status_text)
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
