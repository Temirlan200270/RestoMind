"""OpenAI Realtime tool handlers for voice calls."""

from __future__ import annotations

import json
import logging
from difflib import get_close_matches
from typing import Any

from app.core.config import settings
from app.db.models import MenuItem, Organization
from app.integrations.whatsapp import send_message
from app.services.message_accounting import schedule_log_message
from app.services.order_logic import load_available_menu

logger = logging.getLogger(__name__)

REALTIME_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "lookup_menu",
        "description": "Find menu items by dish or category name for the caller (scoped to restaurant).",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Optional dish or category name"},
            },
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "escalate_to_whatsapp",
        "description": "Send the caller a WhatsApp message to continue orders or complex requests in chat.",
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {"type": "string", "description": "Why WhatsApp is suggested"},
            },
            "additionalProperties": False,
        },
    },
]

_LOOKUP_LIMIT = 5


def _menu_item_brief(item: MenuItem) -> dict[str, Any]:
    return {
        "name": item.name,
        "category": item.category or "",
        "price_kzt": int(float(item.price or 0)),
        "available": bool(item.is_available),
    }


def _format_lookup_message(items: list[MenuItem], *, query: str) -> str:
    if not items:
        if query:
            return f"По запросу «{query[:80]}» ничего не нашёл. Уточните название или спросите категорию."
        return "Спросите блюдо или категорию — подскажу цену и наличие."

    parts: list[str] = []
    for item in items[:_LOOKUP_LIMIT]:
        price = int(float(item.price or 0))
        if item.is_available:
            parts.append(f"{item.name} — {price} ₸")
        else:
            parts.append(f"{item.name} — {price} ₸ (сейчас на стопе)")
    prefix = f"По запросу «{query[:80]}»: " if query else ""
    return prefix + "; ".join(parts)


async def _lookup_menu_for_org(org_id: int, query: str) -> dict[str, Any]:
    """Load menu scoped by organization_id and match by substring / fuzzy name."""
    from app.db.session import async_session_factory

    q = (query or "").strip().lower()
    async with async_session_factory() as db:
        items = await load_available_menu(
            db,
            organization_id=int(org_id),
            include_unavailable=True,
        )

    if not items:
        menu_url = (settings.menu_public_url or "").strip()
        hint = "Меню пока пусто. Напишите нам в WhatsApp — подскажем по блюдам."
        if menu_url:
            hint += f" Меню: {menu_url}"
        return {"ok": True, "message": hint, "items": [], "org_id": org_id}

    matched: list[MenuItem] = []
    if q:
        for item in items:
            name_l = (item.name or "").lower()
            cat_l = (item.category or "").lower()
            if q in name_l or q in cat_l:
                matched.append(item)

        if not matched:
            available_names = [
                (i.name or "").lower().strip()
                for i in items
                if i.is_available and (i.name or "").strip()
            ]
            close = get_close_matches(q, available_names, n=_LOOKUP_LIMIT, cutoff=0.55)
            if close:
                close_set = set(close)
                matched = [
                    i for i in items
                    if (i.name or "").lower().strip() in close_set
                ]
    else:
        matched = [i for i in items if i.is_available][: _LOOKUP_LIMIT]

    matched = matched[:_LOOKUP_LIMIT]
    message = _format_lookup_message(matched, query=q)
    menu_url = (settings.menu_public_url or "").strip()
    if menu_url and len(matched) >= _LOOKUP_LIMIT:
        message += f" Полное меню: {menu_url}"

    return {
        "ok": True,
        "message": message,
        "items": [_menu_item_brief(i) for i in matched],
        "org_id": org_id,
    }


async def _escalate_to_whatsapp_for_org(
    org_id: int,
    phone: str,
    *,
    reason: str,
) -> dict[str, Any]:
    """Send WhatsApp handoff message using the same outbound path as the bot pipeline."""
    from app.db.session import async_session_factory

    reason_clean = (reason or "заказ или детали").strip()
    org_label = "ресторан"
    async with async_session_factory() as db:
        org = await db.get(Organization, int(org_id))
        if org is not None and (org.name or "").strip():
            org_label = org.name.strip()

    wa_sent = False
    wa_error: str | None = None
    phone_digits = (phone or "").strip()
    if phone_digits:
        wa_text = (
            f"Здравствуйте! Вы звонили в {org_label} по поводу {reason_clean}. "
            "Продолжим здесь — напишите, что хотите заказать или забронировать, "
            "и бот поможет оформить."
        )
        result = await send_message(phone_digits, wa_text)
        wa_sent = bool(result.ok)
        if result.ok:
            schedule_log_message(int(org_id), "outbound", "ai", "text")
        elif result.error:
            wa_error = str(result.error.get("message") or result.error.get("code") or "send_failed")
        logger.info(
            "voice realtime escalate org=%s phone=%s wa_sent=%s",
            org_id,
            phone_digits[:6] + "…" if len(phone_digits) > 6 else phone_digits,
            wa_sent,
        )
    else:
        wa_error = "missing_phone"

    if wa_sent:
        voice_hint = (
            f"Отправил вам сообщение в WhatsApp по поводу {reason_clean}. "
            "Откройте чат и напишите, чем помочь."
        )
    elif phone_digits:
        voice_hint = (
            f"Для {reason_clean} напишите нам в WhatsApp с номера {phone_digits} — "
            "там бот оформит заказ и бронь."
        )
    else:
        voice_hint = (
            f"Для {reason_clean} напишите нам в WhatsApp с вашего номера — "
            "там бот оформит заказ и бронь."
        )

    payload: dict[str, Any] = {
        "ok": True,
        "message": voice_hint,
        "whatsapp_sent": wa_sent,
    }
    if wa_error:
        payload["whatsapp_error"] = wa_error
    return payload


async def dispatch_realtime_tool(
    name: str,
    arguments_json: str,
    *,
    org_id: int,
    phone: str,
) -> str:
    """Run a Realtime function call; returns JSON string for function_call_output."""
    try:
        args: dict[str, Any] = json.loads(arguments_json or "{}") if arguments_json else {}
    except json.JSONDecodeError:
        args = {}

    if name == "lookup_menu":
        query = str(args.get("query") or "").strip()
        result = await _lookup_menu_for_org(org_id, query)
        return json.dumps(result, ensure_ascii=False)

    if name == "escalate_to_whatsapp":
        reason = str(args.get("reason") or "заказ или детали").strip()
        result = await _escalate_to_whatsapp_for_org(org_id, phone, reason=reason)
        return json.dumps(result, ensure_ascii=False)

    return json.dumps({"ok": False, "error": f"unknown_tool:{name}"}, ensure_ascii=False)
