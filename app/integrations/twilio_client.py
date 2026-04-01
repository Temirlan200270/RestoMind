"""
Twilio Programmable Voice: проверка подписи вебхука и TTS ответа через TwiML (REST).
Секреты только из переменных окружения.
"""

import base64
import hashlib
import hmac
import html
import logging
from urllib.parse import urlparse, urlunparse

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


def _normalize_twilio_request_url(url: str) -> str:
    """
    Twilio подписывает URL без query string (документация: full URL without query params).
    """
    p = urlparse(url)
    return urlunparse((p.scheme, p.netloc, p.path or "", "", "", ""))


def verify_twilio_signature(
    request_url: str,
    post_params: dict[str, str],
    signature_header: str | None,
    auth_token: str,
) -> bool:
    """Проверка X-Twilio-Signature (base64 HMAC-SHA1)."""
    if not signature_header or not auth_token:
        return False
    base_url = _normalize_twilio_request_url(request_url)
    payload = base_url + "".join(f"{k}{post_params[k]}" for k in sorted(post_params.keys()))
    digest = hmac.new(
        auth_token.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    expected_b64 = base64.b64encode(digest).decode("ascii")
    try:
        return hmac.compare_digest(signature_header.strip(), expected_b64)
    except Exception:
        return False


async def twilio_speak_on_call(call_sid: str, text: str) -> bool:
    """
    Обновляет активный звонок: воспроизвести текст (Polly ru-RU).
    Важно: при активном Media Stream поведение зависит от Twilio; для продакшена часто нужен bidirectional stream.
    """
    account = (settings.twilio_account_sid or "").strip()
    token = (settings.twilio_auth_token or "").strip()
    sid = (call_sid or "").strip()
    if not account or not token or not sid:
        logger.warning("Twilio speak: не заданы TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN или CallSid")
        return False
    safe = html.escape((text or "").strip()[:4000])
    if not safe:
        return False
    twiml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f'<Say language="ru-RU" voice="Polly.Tatyana">{safe}</Say>'
        "</Response>"
    )
    url = f"https://api.twilio.com/2010-04-01/Accounts/{account}/Calls/{sid}.json"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(url, auth=(account, token), data={"Twiml": twiml})
        if not r.is_success:
            logger.warning("Twilio speak HTTP %s: %s", r.status_code, r.text[:500])
        return r.is_success
    except Exception as exc:
        logger.error("Twilio speak error: %s", exc)
        return False
