"""
Доставка текста клиенту: WhatsApp или голосовой звонок Twilio (через TwiML Say).
Контекст звонка задаётся через contextvars на время process_message.
"""

import contextvars
import logging

from app.integrations.whatsapp import send_message

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


async def send_customer_text(phone: str, text: str) -> None:
    """
    Если открыт контекст Twilio-звонка — озвучить через REST; иначе WhatsApp.
    """
    sid = current_twilio_call_sid()
    if sid:
        from app.integrations.twilio_client import twilio_speak_on_call

        ok = await twilio_speak_on_call(sid, text)
        if not ok:
            logger.warning("Не удалось озвучить ответ в Twilio (CallSid=%s)", sid[:8])
        return
    await send_message(phone, text)
