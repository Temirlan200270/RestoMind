"""Сохранение сырых платёжных webhook для аудита (до проверки подписи)."""

from __future__ import annotations

import base64
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.db.models import PaymentWebhookEvent

logger = logging.getLogger(__name__)

MAX_PAYMENT_WEBHOOK_BYTES = 65_536


def headers_to_json(request: Request) -> dict[str, Any]:
    """Заголовки в JSON-дружественном виде (значения — строки)."""
    out: dict[str, Any] = {}
    for k, v in request.headers.items():
        out[k] = v
    return out


def payload_text_from_bytes(raw: bytes) -> tuple[str, str]:
    """
    Декодирует UTF-8 или кладёт base64 в текст с пометкой.
    Returns: (payload_text, verify_error_suffix).
    """
    err_suffix = ""
    try:
        return raw.decode("utf-8"), err_suffix
    except UnicodeDecodeError:
        b64 = base64.b64encode(raw).decode("ascii")
        return f"base64:{b64}", "payload_not_utf8"


async def insert_payment_webhook_audit(
    db: AsyncSession,
    *,
    provider_slug: str,
    raw_body: bytes,
    request: Request,
) -> PaymentWebhookEvent:
    """
    Создаёт строку аудита до верификации.
    В БД сохраняется не более MAX_PAYMENT_WEBHOOK_BYTES байт (подпись проверяйте по полному телу снаружи).
    """
    truncated = len(raw_body) > MAX_PAYMENT_WEBHOOK_BYTES
    body_store = raw_body[:MAX_PAYMENT_WEBHOOK_BYTES]
    payload_txt, decode_note = payload_text_from_bytes(body_store)
    verify_parts: list[str] = []
    if truncated:
        verify_parts.append("payload_truncated")
    if decode_note:
        verify_parts.append(decode_note)

    ev = PaymentWebhookEvent(
        provider_slug=(provider_slug or "unknown").strip().lower()[:64],
        signature_header=(request.headers.get("x-restomind-payment-signature") or "")[:512]
        or (request.headers.get("content-hmac") or "")[:512]
        or (request.headers.get("x-signature-256") or "")[:512],
        http_headers_json=headers_to_json(request),
        payload_bytes=body_store,
        payload_text=payload_txt,
        verified=False,
        verify_error=";".join(verify_parts),
    )
    db.add(ev)
    await db.flush()
    return ev


async def update_payment_webhook_audit(
    db: AsyncSession,
    event: PaymentWebhookEvent,
    *,
    verified: bool | None = None,
    verify_error: str | None = None,
    organization_id: int | None = None,
    order_id: int | None = None,
    external_payment_id: str | None = None,
    parsed_status: str | None = None,
    parsed_amount: float | None = None,
    applied: bool | None = None,
    duplicate: bool | None = None,
    payment_event_id: int | None = None,
) -> None:
    if verified is not None:
        event.verified = verified
    if verify_error is not None:
        event.verify_error = (verify_error or "")[:4000]
    if organization_id is not None:
        event.organization_id = organization_id
    if order_id is not None:
        event.order_id = order_id
    if external_payment_id is not None:
        event.external_payment_id = (external_payment_id or "")[:200] or None
    if parsed_status is not None:
        event.parsed_status = (parsed_status or "")[:20] or None
    if parsed_amount is not None:
        event.parsed_amount = parsed_amount
    if applied is not None:
        event.applied = applied
    if duplicate is not None:
        event.duplicate = duplicate
    if payment_event_id is not None:
        event.payment_event_id = payment_event_id
    await db.flush()
