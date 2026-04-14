"""
Отправка оповещений в Telegram при эскалации диалога на оператора.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import quote

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


def _escape_html(s: str) -> str:
    if not s:
        return ""
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


_FSM_LABELS_RU: dict[str, str] = {
    "chatting": "Обычный диалог",
    "awaiting_order_payment": "Выбор оплаты по заказу",
    "confirming_order": "Подтверждение заказа",
    "confirming_booking": "Подтверждение брони",
    "human_mode": "У оператора",
}

_INTENT_LABELS_RU: dict[str, str] = {
    "order": "Заказ",
    "book": "Бронирование",
    "faq": "Вопрос (FAQ)",
    "escalate": "Запрос оператора / эскалация",
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


def _intent_ru(intent: str) -> str:
    return _INTENT_LABELS_RU.get((intent or "").strip().lower(), intent or "—")


def _fsm_ru(fsm: str) -> str:
    key = (fsm or "").strip().lower()
    ru = _FSM_LABELS_RU.get(key)
    if ru:
        return f"{ru} (<code>{_escape_html(key)}</code>)"
    return f"<code>{_escape_html(key or '—')}</code>"


def _absolute_admin_dialog_url(phone: str) -> str | None:
    """
    Полный URL открытия админки на диалоге с клиентом.
    Нужен для inline-кнопки и для кликабельных ссылок в Telegram (относительные пути не работают).
    """
    raw = (settings.public_base_url or "").strip().rstrip("/")
    if not raw:
        return None
    if not raw.startswith(("http://", "https://")):
        raw = f"https://{raw.lstrip('/')}"
    q = (phone or "").strip()
    frag = f"chats?phone={quote(q, safe='')}" if q else "chats"
    return f"{raw}/admin#{frag}"


async def send_tg_fallback_alert(
    phone: str,
    user_message: str,
    bot_reply: str,
    *,
    extras: EscalationAlertExtras | None = None,
) -> None:
    """
    Уведомление администратору: клиент переведён в режим оператора (escalate).
    Если TELEGRAM_BOT_TOKEN / TELEGRAM_ADMIN_CHAT_ID не заданы — тихий no-op.
    """
    token = (settings.telegram_bot_token or "").strip()
    chat_id = (settings.telegram_admin_chat_id or "").strip()
    if not token or not chat_id:
        logger.debug("Telegram: токен или chat_id не заданы — алерт пропущен")
        return

    q = (phone or "").strip()
    admin_link_abs = _absolute_admin_dialog_url(q)

    u_short = (user_message or "")[:1400]
    r_short = (bot_reply or "")[:900]

    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    app_line = _escape_html((settings.app_name or "RestoMind").strip())
    ver = (settings.app_version or "").strip()
    if ver:
        app_line = f"{app_line} <i>v{_escape_html(ver)}</i>"

    lines: list[str] = [
        "<b>Нужна помощь оператора</b>",
        "",
        f"<b>Время (UTC):</b> <code>{now_utc}</code>",
        f"<b>Сервис:</b> {app_line}",
        f"<b>Телефон:</b> <code>{_escape_html(q)}</code>",
    ]

    if extras:
        if extras.customer_name:
            lines.append(f"<b>Имя в БД:</b> {_escape_html(extras.customer_name)}")
        lines.append(f"<b>Состояние диалога:</b> {_fsm_ru(extras.fsm_state)}")
        lines.append(
            "<b>Интент модели:</b> "
            f"{_escape_html(_intent_ru(extras.intent))} "
            f"(<code>{_escape_html(extras.intent or '—')}</code>)",
        )
        voice_line = "голос (WhatsApp)" if extras.had_voice else "текст"
        lines.append(f"<b>Ввод клиента:</b> {voice_line}")
        if extras.detected_language:
            lines.append(
                "<b>Язык ответа (модель):</b> "
                f"<code>{_escape_html(extras.detected_language)}</code>",
            )
        if extras.technical_fallback:
            lines.append(
                "<b>Признак:</b> запасной ответ при сбое OpenAI "
                "(квота, лимиты, сеть — проверьте биллинг и логи).",
            )
        oid = extras.outbound_chat_log_id
        if oid is not None:
            lines.append(f"<b>Запись чата (id):</b> <code>{oid}</code>")
        if extras.draft_order_id is not None:
            extra_tot = ""
            if extras.draft_order_total:
                extra_tot = f", сумма {_escape_html(extras.draft_order_total)}"
            lines.append(
                "<b>Черновик заказа:</b> "
                f"\u2116<code>{extras.draft_order_id}</code>{extra_tot}",
            )
        if extras.pending_order_id is not None:
            lines.append(
                "<b>Ожидает подтверждения заказ:</b> "
                f"\u2116<code>{extras.pending_order_id}</code>",
            )
        if extras.pending_booking_id is not None:
            lines.append(
                "<b>Ожидает подтверждения бронь:</b> "
                f"\u2116<code>{extras.pending_booking_id}</code>",
            )

    lines.extend(
        [
            "",
            "<b>Сообщение клиента:</b>",
            _escape_html(u_short),
            "",
            "<b>Ответ бота клиенту:</b>",
            _escape_html(r_short),
        ],
    )

    if admin_link_abs:
        lines.extend(
            [
                "",
                f'<a href="{_escape_html(admin_link_abs)}">Открыть диалог в админке</a>',
                "",
                "<i>Или нажмите кнопку ниже.</i>",
            ],
        )
    else:
        lines.extend(
            [
                "",
                "<b>Ссылка в админку недоступна:</b> задайте на сервере переменную "
                "<code>PUBLIC_BASE_URL</code> (полный URL сайта, например "
                "<code>https://your-app.onrender.com</code>).",
                f"Фрагмент для ручного открытия: <code>/admin#chats?phone={_escape_html(q)}</code>",
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
    # Кнопка-ссылка: Telegram принимает только абсолютные http(s) URL.
    if admin_link_abs:
        payload["reply_markup"] = {
            "inline_keyboard": [
                [{"text": "Открыть диалог в админке", "url": admin_link_abs}],
            ],
        }

    api_url = f"https://api.telegram.org/bot{token}/sendMessage"
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(api_url, json=payload)
        resp.raise_for_status()
