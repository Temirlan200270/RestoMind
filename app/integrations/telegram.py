"""
Отправка оповещений в Telegram при эскалации диалога на оператора.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx

from app.core.config import settings
from app.db.session import async_session_factory

logger = logging.getLogger(__name__)


def _escape_html(s: str) -> str:
    if not s:
        return ""
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def format_phone_for_alert(phone: str) -> str:
    """
    Человекочитаемый E.164 для карточки в Telegram: +7 705 131 08 37.
    Вставляется внутрь <code>…</code> — удобно копировать одним тапом.
    """
    raw = (phone or "").strip()
    digits = "".join(c for c in raw if c.isdigit())
    if len(digits) == 11 and digits.startswith("7"):
        rest = digits[1:]
        return f"+7 {rest[0:3]} {rest[3:6]} {rest[6:8]} {rest[8:10]}"
    if len(digits) == 12 and digits.startswith("77"):  # редкий префикс
        rest = digits[2:]
        if len(rest) == 10:
            return f"+77 {rest[0:3]} {rest[3:6]} {rest[6:8]} {rest[8:10]}"
    if digits:
        return raw if raw.startswith("+") else f"+{digits}"
    return raw


def _timezone_place_label() -> str:
    """Короткая подпись к времени (последний сегмент IANA), без «системщины»."""
    tz_name = (settings.display_timezone or "").strip()
    if not tz_name:
        return "UTC"
    tail = tz_name.split("/")[-1].replace("_", " ")
    return tail


def _primary_time_line_html() -> str:
    """Одна строка «Время» для карточки: локаль заведения или UTC."""
    now = datetime.now(timezone.utc)
    tz_name = (settings.display_timezone or "").strip()
    place = _escape_html(_timezone_place_label())
    if tz_name:
        try:
            local = now.astimezone(ZoneInfo(tz_name))
            hm = local.strftime("%H:%M")
            return f"<b>Время:</b> <code>{_escape_html(hm)}</code> <i>({place})</i>"
        except Exception:
            logger.warning("DISPLAY_TIMEZONE недействителен (%r) — в карточке UTC", tz_name)
    hm = now.strftime("%H:%M")
    return f"<b>Время:</b> <code>{_escape_html(hm)}</code> <i>(UTC)</i>"


_FSM_LABELS_RU: dict[str, str] = {
    "chatting": "Обычный диалог",
    "awaiting_order_payment": "Выбор оплаты по заказу",
    "confirming_order": "Подтверждение заказа",
    "confirming_booking": "Подтверждение брони",
    "human_mode": "У оператора",
}

@dataclass
class EscalationAlertExtras:
    """Контекст для расширенного Telegram-алерта (опционально)."""

    intent: str = ""
    fsm_state: str = ""
    had_voice: bool = False
    detected_language: str | None = None
    draft_order_id: int | None = None
    draft_order_total: str | None = None
    customer_name: str | None = None
    technical_fallback: bool = False
    outbound_chat_log_id: int | None = None
    pending_order_id: int | None = None
    pending_booking_id: int | None = None


def _fsm_plain_ru(fsm: str) -> str:
    """Только по-русски, без machine-id (для карточек персоналу)."""
    key = (fsm or "").strip().lower()
    return _FSM_LABELS_RU.get(key, "Диалог с гостем")


async def _staff_chat_id_for_org(organization_id: int | None) -> str:
    """Telegram chat_id персонала: из организации или глобальный env."""
    if organization_id is not None:
        from app.db.models import Organization

        async with async_session_factory() as db:
            org = await db.get(Organization, int(organization_id))
            if org is not None:
                cid = (org.telegram_ops_chat_id or "").strip()
                if cid:
                    return cid
    return (settings.telegram_admin_chat_id or "").strip()


async def send_ops_notification_html(
    text: str,
    *,
    organization_id: int | None = None,
    reply_markup: dict[str, object] | None = None,
) -> None:
    """Короткое уведомление персоналу (HTML). Без токена/chat_id — no-op."""
    token = (settings.telegram_bot_token or "").strip()
    chat_id = await _staff_chat_id_for_org(organization_id)
    if not token or not chat_id:
        logger.debug("Telegram ops: пропуск (нет токена или chat_id)")
        return
    payload: dict = {
        "chat_id": chat_id,
        "text": text[:4000],
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    api_url = f"https://api.telegram.org/bot{token}/sendMessage"
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(api_url, json=payload)
        resp.raise_for_status()


def _absolute_admin_base_url() -> str | None:
    raw = (settings.public_base_url or "").strip().rstrip("/")
    if not raw:
        return None
    if not raw.startswith(("http://", "https://")):
        raw = f"https://{raw.lstrip('/')}"
    return raw


def _absolute_admin_dialog_url(phone: str) -> str | None:
    """
    Полный URL открытия админки на диалоге с клиентом.
    Нужен для inline-кнопки и для кликабельных ссылок в Telegram (относительные пути не работают).
    """
    base = _absolute_admin_base_url()
    if not base:
        return None
    q = (phone or "").strip()
    frag = f"chats?phone={quote(q, safe='')}" if q else "chats"
    return f"{base}/admin#{frag}"


def _absolute_admin_orders_url() -> str | None:
    """Вкладка заказов в админке (глубокая ссылка #orders)."""
    base = _absolute_admin_base_url()
    if not base:
        return None
    return f"{base}/admin#orders"


async def send_tg_fallback_alert(
    phone: str,
    user_message: str,
    bot_reply: str,
    *,
    extras: EscalationAlertExtras | None = None,
    organization_id: int | None = None,
) -> None:
    """
    Карточка SOS в Telegram: эскалация на оператора или запасной ответ при сбое AI.
    TELEGRAM_BOT_TOKEN + chat_id (организация или env) — иначе no-op.
    """
    token = (settings.telegram_bot_token or "").strip()
    chat_id = await _staff_chat_id_for_org(organization_id)
    if not token or not chat_id:
        logger.debug("Telegram: токен или chat_id не заданы — алерт пропущен")
        return

    q = (phone or "").strip()
    admin_link_abs = _absolute_admin_dialog_url(q)
    phone_code = _escape_html(format_phone_for_alert(q))

    u_short = (user_message or "").strip()[:900]
    r_short = (bot_reply or "").strip()[:700]

    technical = bool(extras and extras.technical_fallback)
    if technical:
        what = "🤖 <i>Бот не смог обработать запрос (сбой AI-провайдера).</i>"
    else:
        what = "👤 <i>Гость запросил помощь менеджера.</i>"

    if extras and extras.had_voice:
        last_client = "🎤 <i>Голосовое сообщение</i>"
    elif u_short:
        last_client = f"<i>{_escape_html(u_short)}</i>"
    else:
        last_client = "<i>—</i>"

    lines: list[str] = [
        "🚨 <b>НУЖНА ПОМОЩЬ МЕНЕДЖЕРА</b>",
        "",
        f"<b>Гость:</b> <code>{phone_code}</code>",
        _primary_time_line_html(),
        "",
        "<b>Что случилось</b>",
        what,
        "",
        "<b>Последнее от клиента</b>",
        last_client,
        "",
        "<b>Ответ гостю в WhatsApp</b>",
        _escape_html(r_short) if r_short else "<i>—</i>",
        "",
        "📥 <b>Клиент ждёт ответа в WhatsApp.</b>",
    ]

    if extras:
        if extras.customer_name:
            lines.extend(["", f"<b>Имя в базе:</b> {_escape_html(extras.customer_name.strip())}"])
        fsm_key = (extras.fsm_state or "").strip().lower()
        if fsm_key and fsm_key != "chatting":
            lines.extend(["", f"<b>Этап:</b> {_escape_html(_fsm_plain_ru(extras.fsm_state))}"])
        if extras.draft_order_id is not None:
            extra_tot = ""
            if extras.draft_order_total:
                extra_tot = f" · {_escape_html(extras.draft_order_total)}"
            lines.append(
                f"<b>Корзина:</b> черновик №<code>{extras.draft_order_id}</code>{extra_tot}",
            )
        if extras.pending_order_id is not None:
            lines.append(
                f"<b>Ожидает «Да» по заказу:</b> №<code>{extras.pending_order_id}</code>",
            )
        if extras.pending_booking_id is not None:
            lines.append(
                f"<b>Ожидает «Да» по брони:</b> №<code>{extras.pending_booking_id}</code>",
            )

    brand = _escape_html((settings.app_name or "RestoMind").strip())
    lines.extend(["", f"<i>{brand}</i>"])

    if admin_link_abs:
        lines.extend(
            [
                "",
                f'<a href="{_escape_html(admin_link_abs)}">Открыть в браузере</a>',
            ],
        )
    else:
        lines.extend(
            [
                "",
                "<b>Ссылка в админку:</b> задайте <code>PUBLIC_BASE_URL</code> (полный <code>https://…</code> без <code>/admin</code>). "
                "На Render часто подставляется <code>RENDER_EXTERNAL_URL</code>.",
                f"Фрагмент: <code>/admin#chats?phone={_escape_html(q)}</code>",
            ],
        )

    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:3997] + "..."

    payload: dict = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if admin_link_abs:
        payload["reply_markup"] = {
            "inline_keyboard": [
                [{"text": "ОТКРЫТЬ ЧАТ ↗️", "url": admin_link_abs}],
            ],
        }

    api_url = f"https://api.telegram.org/bot{token}/sendMessage"
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(api_url, json=payload)
        resp.raise_for_status()
