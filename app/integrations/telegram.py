"""
Отправка оповещений в Telegram при эскалации диалога на оператора.
"""

import logging

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


async def send_tg_fallback_alert(phone: str, user_message: str, bot_reply: str) -> None:
    """
    Уведомление администратору: клиент переведён в режим оператора (escalate).
    Если TELEGRAM_BOT_TOKEN / TELEGRAM_ADMIN_CHAT_ID не заданы — тихий no-op.
    """
    token = (settings.telegram_bot_token or "").strip()
    chat_id = (settings.telegram_admin_chat_id or "").strip()
    if not token or not chat_id:
        logger.debug("Telegram: токен или chat_id не заданы — алерт пропущен")
        return

    base = (settings.public_base_url or "").strip().rstrip("/")
    q = (phone or "").strip()
    if base and q:
        admin_link = f"{base}/admin#chats?phone={q}"
    elif base:
        admin_link = f"{base}/admin#chats"
    else:
        admin_link = "/admin#chats"

    u_short = (user_message or "")[:900]
    r_short = (bot_reply or "")[:400]

    text = (
        "<b>Нужна помощь оператора</b>\n\n"
        f"📱 <code>{_escape_html(q)}</code>\n\n"
        f"💬 Клиент: {_escape_html(u_short)}\n\n"
        f"🤖 Ответ бота: {_escape_html(r_short)}\n\n"
        f'<a href="{admin_link}">Открыть в админке</a>'
    )

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            url,
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
        )
        resp.raise_for_status()
