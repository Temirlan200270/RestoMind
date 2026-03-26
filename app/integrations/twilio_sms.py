"""
Twilio SMS — оповещения для MVP.

Используем только SMS (без WhatsApp). Если переменные окружения не заданы — модуль работает как no-op.
"""

from __future__ import annotations

import asyncio
import logging

from app.core.config import settings

logger = logging.getLogger(__name__)


def _mask_phone(phone: str) -> str:
    p = (phone or "").strip()
    if len(p) <= 6:
        return p
    return f"{p[:3]}…{p[-2:]}"


async def send_twilio_sms_alert(phone: str, user_message: str, bot_reply: str) -> None:
    """
    Уведомление оператору по SMS при эскалации (HUMAN_MODE).
    Если TWILIO_* не заданы — тихий no-op.
    """
    account_sid = (settings.twilio_account_sid or "").strip()
    auth_token = (settings.twilio_auth_token or "").strip()
    from_phone = (settings.twilio_from or "").strip()
    to_phone = (settings.twilio_admin_to or "").strip()

    if not account_sid or not auth_token or not from_phone or not to_phone:
        logger.debug("Twilio SMS: не настроен — алерт пропущен")
        return

    # Импортируем только если реально нужно, чтобы не требовать пакет в окружениях без Twilio.
    from twilio.rest import Client  # type: ignore

    client = Client(account_sid, auth_token)

    u_short = (user_message or "").replace("\n", " ").strip()[:240]
    r_short = (bot_reply or "").replace("\n", " ").strip()[:240]

    text = (
        "RestoMind: нужна помощь оператора\n"
        f"Клиент: {phone}\n"
        f"Сообщение: {u_short}\n"
        f"Ответ бота: {r_short}"
    )

    def _send_sync() -> None:
        client.messages.create(
            from_=from_phone,
            to=to_phone,
            body=text,
        )

    try:
        await asyncio.to_thread(_send_sync)
        logger.info("Twilio SMS алерт отправлен на %s (клиент %s)", _mask_phone(to_phone), _mask_phone(phone))
    except Exception as exc:
        # Не пробрасываем, чтобы не ломать основной webhook flow
        logger.warning("Twilio SMS алерт не отправлен: %s", exc)

