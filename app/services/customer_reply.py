"""
Доставка текста клиенту: WhatsApp или голосовой звонок Twilio (через TwiML Say).
Контекст звонка задаётся через contextvars на время process_message.
"""

import asyncio
import contextvars
import logging

from app.integrations.whatsapp import send_message
from app.services.chat_delivery import finalize_outbound_delivery
from app.services.events import publish_event

logger = logging.getLogger(__name__)

_active_twilio_call_sid: contextvars.ContextVar[str] = contextvars.ContextVar(
    "active_twilio_call_sid",
    default="",
)


def twilio_call_context(call_sid: str) -> contextvars.Token:
    """Возвращает token для сброса в finally."""
    return _active_twilio_call_sid.set((call_sid or "").strip())


def reset_twilio_call_context(token: contextvars.Token) -> None:
    _active_twilio_call_sid.reset(token)


def current_twilio_call_sid() -> str:
    return (_active_twilio_call_sid.get() or "").strip()


async def _bg_finalize_whatsapp(outbound_chat_log_id: int, wa_ok: bool, message_id: str | None, error: dict | None) -> None:
    """Фоновое обновление статуса доставки — не блокирует ответ боту."""
    from app.db.session import async_session_factory
    try:
        async with async_session_factory() as db:
            evt = await finalize_outbound_delivery(
                db,
                outbound_chat_log_id,
                send_ok=wa_ok,
                provider_message_id=message_id,
                error_details=error,
            )
            await db.commit()
        if evt is not None:
            await publish_event("message_status_updated", evt)
    except Exception:
        logger.exception("_bg_finalize_whatsapp: ошибка обновления chat_log id=%d", outbound_chat_log_id)


async def _bg_finalize_twilio(outbound_chat_log_id: int, ok: bool) -> None:
    """Фоновое обновление статуса доставки Twilio."""
    from app.db.session import async_session_factory
    try:
        async with async_session_factory() as db:
            evt = await finalize_outbound_delivery(
                db, outbound_chat_log_id, send_ok=ok,
                error_details=None if ok else {"channel": "twilio", "detail": "speak_failed"},
            )
            await db.commit()
        if evt is not None:
            await publish_event("message_status_updated", evt)
    except Exception:
        logger.exception("_bg_finalize_twilio: ошибка обновления chat_log id=%d", outbound_chat_log_id)


async def send_customer_text(
    phone: str,
    text: str,
    *,
    outbound_chat_log_id: int | None = None,
) -> None:
    """
    Если открыт контекст Twilio-звонка — озвучить через REST;
    если активен Telegram customer channel — sendMessage;
    иначе WhatsApp.
    При outbound_chat_log_id обновляет chat_logs асинхронно (fire-and-forget),
    не блокируя pipeline бота.
    """
    sid = current_twilio_call_sid()
    if sid:
        from app.integrations.twilio_client import twilio_speak_on_call

        ok = await twilio_speak_on_call(sid, text)
        if not ok:
            logger.warning("Не удалось озвучить ответ в Twilio (CallSid=%s)", sid[:8])
        if outbound_chat_log_id is not None:
            asyncio.ensure_future(_bg_finalize_twilio(outbound_chat_log_id, ok))
        return

    from app.services.telegram_customer import current_customer_channel, current_telegram_chat_id, send_telegram_customer_text

    if current_customer_channel() == "telegram":
        tg_chat_id = current_telegram_chat_id()
        tg_result = await send_telegram_customer_text(tg_chat_id, text) if tg_chat_id else {"ok": False}
        tg_ok = bool(tg_result.get("ok"))
        if outbound_chat_log_id is not None:
            msg_id = None
            result = tg_result.get("result")
            if isinstance(result, dict):
                msg_id = str(result.get("message_id") or "") or None
            asyncio.ensure_future(
                _bg_finalize_whatsapp(
                    outbound_chat_log_id,
                    tg_ok,
                    msg_id,
                    None if tg_ok else {"channel": "telegram", "detail": tg_result.get("error")},
                )
            )
        return

    wa = await send_message(phone, text)
    if outbound_chat_log_id is not None:
        asyncio.ensure_future(
            _bg_finalize_whatsapp(outbound_chat_log_id, wa.ok, wa.message_id, wa.error)
        )
