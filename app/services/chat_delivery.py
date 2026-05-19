"""
Доставка исходящих сообщений WhatsApp: ранги статусов, обновление chat_logs, события админки.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ChatLog, User
from app.services.events import publish_event

logger = logging.getLogger(__name__)

# Внутренние + статусы Meta (sent, delivered, read, failed)
_STATUS_RANK: dict[str, int] = {
    "sending": 1,
    "sent": 2,
    "delivered": 3,
    "read": 4,
    "failed": 100,
}


def delivery_status_rank(status: str | None) -> int:
    if not status:
        return 0
    return _STATUS_RANK.get(status.strip().lower(), 0)


def should_upgrade_delivery_status(old: str | None, new: str) -> bool:
    """Новый статус применяем только если он «весомее» или меняем failed."""
    new_l = (new or "").strip().lower()
    old_l = (old or "").strip().lower() if old else ""
    if not new_l:
        return False
    if old_l == "failed":
        return False
    return delivery_status_rank(new_l) > delivery_status_rank(old_l)


def normalize_whatsapp_status(meta_status: str) -> str:
    """Meta: sent | delivered | read | failed → наши имена."""
    s = (meta_status or "").strip().lower()
    if s in _STATUS_RANK:
        return s
    return ""


async def publish_message_status_updated(
    phone: str,
    chat_log_id: int,
    delivery_status: str,
    *,
    provider_message_id: str | None = None,
    error_details: dict[str, Any] | list[Any] | None = None,
    organization_id: int | None = None,
) -> None:
    data: dict[str, Any] = {
        "phone": phone,
        "chat_log_id": chat_log_id,
        "delivery_status": delivery_status,
        "provider_message_id": provider_message_id,
        "error_details": error_details,
    }
    if organization_id is not None:
        data["organization_id"] = organization_id
    await publish_event("message_status_updated", data)


async def finalize_outbound_delivery(
    db: AsyncSession,
    chat_log_id: int,
    send_ok: bool,
    *,
    provider_message_id: str | None = None,
    error_details: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """
    После отправки клиенту (WhatsApp Graph API или успешный Twilio Say): sent или failed.
    Для WhatsApp вебхук statuses позже может поднять до delivered/read.
    """
    log = await db.get(ChatLog, chat_log_id)
    if log is None:
        return
    phone = await db.scalar(select(User.phone).where(User.id == log.user_id))
    phone_s = (phone or "").strip()
    now = datetime.now(timezone.utc)
    if send_ok:
        log.delivery_status = "sent"
        if provider_message_id:
            log.provider_message_id = provider_message_id
        log.error_details = None
    else:
        log.delivery_status = "failed"
        if error_details is not None:
            log.error_details = error_details
    log.status_updated_at = now
    await db.flush()
    if not send_ok and log.organization_id:
        try:
            from app.services.system_events import BusinessEvent, emit_event

            await emit_event(
                db,
                BusinessEvent(
                    id=f"integration.whatsapp.failed:{chat_log_id}",
                    org_id=int(log.organization_id),
                    type="integration.whatsapp.failed",
                    actor="system",
                    location_id=getattr(log, "location_id", None),
                    entity_type="chat_log",
                    entity_id=chat_log_id,
                    payload={
                        "chat_log_id": chat_log_id,
                        "phone": phone_s,
                        "error_details": error_details,
                    },
                ),
            )
        except Exception:
            logger.exception("emit integration.whatsapp.failed chat_log=%s", chat_log_id)
    if not phone_s:
        return None
    return {
        "phone": phone_s,
        "chat_log_id": chat_log_id,
        "delivery_status": log.delivery_status or "",
        "provider_message_id": log.provider_message_id,
        "error_details": log.error_details if isinstance(log.error_details, dict) else None,
        "organization_id": int(log.organization_id) if log.organization_id is not None else None,
    }


async def apply_whatsapp_status_webhook(
    db: AsyncSession,
    provider_message_id: str,
    meta_status: str,
    errors: Any,
) -> dict[str, Any] | None:
    """Обновление по вебхуку statuses от Meta."""
    mid = (provider_message_id or "").strip()
    if not mid:
        return
    norm = normalize_whatsapp_status(meta_status)
    if not norm:
        logger.debug("WhatsApp status: неизвестный статус %r", meta_status)
        return

    result = await db.execute(
        select(ChatLog, User.phone)
        .join(User, User.id == ChatLog.user_id)
        .where(
            ChatLog.provider_message_id == mid,
            ChatLog.role.in_(("assistant", "operator")),
        )
        .limit(1),
    )
    row = result.first()
    if row is None:
        logger.debug("WhatsApp status: нет chat_logs для wamid=%s", mid[:24])
        return None
    log, phone = row
    phone_s = (phone or "").strip()
    old = log.delivery_status
    if norm == "failed":
        log.delivery_status = "failed"
        if errors is not None:
            log.error_details = {"errors": errors} if not isinstance(errors, dict) else errors
        log.status_updated_at = datetime.now(timezone.utc)
        await db.flush()
        if not phone_s:
            return None
        return {
            "phone": phone_s,
            "chat_log_id": int(log.id),
            "delivery_status": "failed",
            "provider_message_id": mid,
            "error_details": log.error_details,
            "organization_id": int(log.organization_id) if log.organization_id is not None else None,
        }

    if not should_upgrade_delivery_status(old, norm):
        return None
    log.delivery_status = norm
    if norm != "failed":
        log.error_details = None
    log.status_updated_at = datetime.now(timezone.utc)
    await db.flush()
    if not phone_s:
        return None
    return {
        "phone": phone_s,
        "chat_log_id": int(log.id),
        "delivery_status": norm,
        "provider_message_id": mid,
        "error_details": None,
        "organization_id": int(log.organization_id) if log.organization_id is not None else None,
    }
