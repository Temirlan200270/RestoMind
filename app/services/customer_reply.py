"""
Доставка текста клиенту: WhatsApp или голосовой звонок Twilio (через TwiML Say).
Контекст звонка задаётся через contextvars на время process_message.
"""

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


async def send_customer_text(
    phone: str,
    text: str,
    *,
    outbound_chat_log_id: int | None = None,
) -> None:
    """
    Если открыт контекст Twilio-звонка — озвучить через REST; иначе WhatsApp.
    При outbound_chat_log_id обновляет chat_logs (sent/failed) и шлёт message_status_updated.
    """
    from app.db.session import async_session_factory

    sid = current_twilio_call_sid()
    if sid:
        from app.integrations.twilio_client import twilio_speak_on_call

        ok = await twilio_speak_on_call(sid, text)
        if not ok:
            logger.warning("Не удалось озвучить ответ в Twilio (CallSid=%s)", sid[:8])
        if outbound_chat_log_id is not None:
            async with async_session_factory() as db:
                evt = await finalize_outbound_delivery(
                    db, outbound_chat_log_id, send_ok=ok,
                    error_details=None if ok else {"channel": "twilio", "detail": "speak_failed"},
                )
                await db.commit()
            if evt is not None:
                await publish_event("message_status_updated", evt)
        return

    wa = await send_message(phone, text)
    if outbound_chat_log_id is None:
        return
    async with async_session_factory() as db:
        evt = await finalize_outbound_delivery(
            db,
            outbound_chat_log_id,
            send_ok=wa.ok,
            provider_message_id=wa.message_id,
            error_details=wa.error,
        )
        await db.commit()
    if evt is not None:
        await publish_event("message_status_updated", evt)
