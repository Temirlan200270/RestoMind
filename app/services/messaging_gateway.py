from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import ChannelConnection, ChannelMessage, Conversation
from app.db.session import async_session_factory
from app.schemas.messaging import (
    ChannelConnectionOut,
    ChannelConnectionStatusEvent,
    ChannelDeliveryEvent,
    ChannelInboundEvent,
    ChannelMessageContent,
    ChannelMessageOut,
)
from app.services.chat_delivery import finalize_outbound_delivery
from app.services.events import publish_event
from app.services.intent_router import get_or_create_user
from app.services.trace_context import build_trace_id

logger = logging.getLogger(__name__)


BAILEYS_PROVIDER = "whatsapp_baileys"
META_PROVIDER = "whatsapp_meta"
OUTBOUND_RETRY_DELAYS = (10, 30, 120, 300)


def normalize_provider(provider: str | None) -> str:
    value = (provider or "").strip().lower()
    if value in {"baileys", "whatsapp-web", "whatsapp_web"}:
        return BAILEYS_PROVIDER
    if value in {"meta", "whatsapp", "whatsapp_cloud"}:
        return META_PROVIDER
    return value or BAILEYS_PROVIDER


def channel_connection_to_out(row: ChannelConnection) -> ChannelConnectionOut:
    return ChannelConnectionOut(
        id=int(row.id),
        organization_id=int(row.organization_id),
        provider=row.provider,
        status=row.status,
        external_account_id=row.external_account_id or "",
        phone=row.phone or "",
        display_name=row.display_name or "",
        session_ref=row.session_ref or "",
        last_qr=row.last_qr or "",
        health=dict(row.health_json or {}),
        last_error=row.last_error or "",
        last_seen_at=row.last_seen_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def channel_message_to_out(row: ChannelMessage) -> ChannelMessageOut:
    payload = row.payload_json if isinstance(row.payload_json, dict) else {}
    return ChannelMessageOut(
        id=int(row.id),
        organization_id=int(row.organization_id),
        conversation_id=int(row.conversation_id) if row.conversation_id is not None else None,
        channel_connection_id=int(row.channel_connection_id),
        chat_log_id=int(row.chat_log_id) if row.chat_log_id is not None else None,
        trace_id=row.trace_id or "",
        correlation_id=row.correlation_id or "",
        provider=row.provider,
        direction=row.direction,
        external_chat_id=row.external_chat_id or "",
        external_message_id=row.external_message_id or "",
        idempotency_key=row.idempotency_key,
        status=row.status,
        message_type=row.message_type or "text",
        text=row.text or "",
        payload=payload,
        error_code=row.error_code or "",
        error_message=row.error_message or "",
        attempt_count=int(row.attempt_count or 0),
        next_attempt_at=row.next_attempt_at,
        created_at=row.created_at,
    )


def build_channel_idempotency_key(
    *,
    provider: str,
    channel_connection_id: int,
    external_message_id: str,
    fallback_seed: str,
) -> str:
    mid = (external_message_id or "").strip()
    if mid:
        return f"{normalize_provider(provider)}:{int(channel_connection_id)}:{mid}"
    digest = hashlib.sha256(fallback_seed.encode("utf-8")).hexdigest()[:32]
    return f"{normalize_provider(provider)}:{int(channel_connection_id)}:synthetic:{digest}"


async def ensure_channel_connection(
    db: AsyncSession,
    *,
    organization_id: int,
    provider: str = BAILEYS_PROVIDER,
    phone: str = "",
    display_name: str = "",
) -> ChannelConnection:
    provider_n = normalize_provider(provider)
    external = (phone or "").strip()
    existing = await db.scalar(
        select(ChannelConnection).where(
            ChannelConnection.organization_id == int(organization_id),
            ChannelConnection.provider == provider_n,
            ChannelConnection.external_account_id == external,
        )
    )
    if existing is not None:
        return existing

    row = ChannelConnection(
        organization_id=int(organization_id),
        provider=provider_n,
        status="qr_required" if provider_n == BAILEYS_PROVIDER else "connected",
        external_account_id=external,
        phone=(phone or "").strip(),
        display_name=(display_name or "").strip(),
        session_ref=f"{provider_n}/{organization_id}/{uuid.uuid4().hex}",
        health_json={"provider": provider_n, "health": "needs_reconnect" if provider_n == BAILEYS_PROVIDER else "works"},
    )
    db.add(row)
    await db.flush()
    return row


async def find_or_create_conversation(
    db: AsyncSession,
    *,
    organization_id: int,
    phone: str,
) -> tuple[Conversation, int]:
    user = await get_or_create_user(db, phone, int(organization_id))
    row = await db.scalar(
        select(Conversation)
        .where(
            Conversation.organization_id == int(organization_id),
            Conversation.customer_id == int(user.id),
            Conversation.status == "active",
        )
        .order_by(Conversation.id.desc())
    )
    now = datetime.now(timezone.utc)
    if row is None:
        row = Conversation(
            organization_id=int(organization_id),
            customer_id=int(user.id),
            status="active",
            last_message_at=now,
        )
        db.add(row)
        await db.flush()
    else:
        row.last_message_at = now
    return row, int(user.id)


async def record_inbound_event(db: AsyncSession, event: ChannelInboundEvent) -> tuple[ChannelMessage, bool]:
    connection = await db.get(ChannelConnection, int(event.channel_connection_id))
    if connection is None:
        raise ValueError(f"channel_connection_not_found:{event.channel_connection_id}")

    provider = normalize_provider(event.provider or connection.provider)
    phone = (event.sender.phone or event.sender.external_id or event.external_chat_id or "").strip()
    conversation, _user_id = await find_or_create_conversation(
        db,
        organization_id=int(connection.organization_id),
        phone=phone,
    )
    trace_id = (event.trace_id or build_trace_id(event.external_message_id)).strip()
    correlation_id = (event.correlation_id or f"conversation:{conversation.id}").strip()
    idem = (event.idempotency_key or "").strip() or build_channel_idempotency_key(
        provider=provider,
        channel_connection_id=int(connection.id),
        external_message_id=event.external_message_id,
        fallback_seed=f"{event.external_chat_id}:{event.message.text}:{event.received_at}",
    )

    existing = await db.scalar(
        select(ChannelMessage)
        .where(
            ChannelMessage.organization_id == int(connection.organization_id),
            ChannelMessage.idempotency_key == idem,
        )
        .limit(1)
    )
    if existing is not None:
        return existing, False

    payload = {
        "sender": event.sender.model_dump(),
        "message": event.message.model_dump(),
        "received_at": event.received_at.isoformat() if event.received_at else None,
    }
    row = ChannelMessage(
        organization_id=int(connection.organization_id),
        conversation_id=int(conversation.id),
        channel_connection_id=int(connection.id),
        trace_id=trace_id,
        correlation_id=correlation_id,
        provider=provider,
        direction="in",
        external_chat_id=event.external_chat_id,
        external_message_id=event.external_message_id or "",
        idempotency_key=idem,
        status="received",
        message_type=event.message.type or "text",
        text=event.message.text or "",
        payload_json=payload,
    )
    db.add(row)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        async with async_session_factory() as retry_db:
            existing_retry = await retry_db.scalar(
                select(ChannelMessage)
                .where(
                    ChannelMessage.organization_id == int(connection.organization_id),
                    ChannelMessage.idempotency_key == idem,
                )
                .limit(1)
            )
            if existing_retry is None:
                raise
            return existing_retry, False
    return row, True


async def process_channel_message(channel_message_id: int) -> None:
    from app.services.conversation_service import conversation_service

    await conversation_service.process_channel_message(channel_message_id)


def _retry_delay(attempt_count: int | None) -> int:
    idx = max(0, min(len(OUTBOUND_RETRY_DELAYS) - 1, int(attempt_count or 0)))
    return OUTBOUND_RETRY_DELAYS[idx]


async def enqueue_outbound_text(
    *,
    channel_connection_id: int,
    external_chat_id: str,
    text: str,
    outbound_chat_log_id: int | None = None,
    trace_id: str | None = None,
    correlation_id: str | None = None,
) -> ChannelMessage:
    async with async_session_factory() as db:
        connection = await db.get(ChannelConnection, int(channel_connection_id))
        if connection is None:
            raise ValueError(f"channel_connection_not_found:{channel_connection_id}")
        conversation_id = None
        if outbound_chat_log_id is not None:
            # Best-effort link: the active channel conversation is already tracked by correlation id.
            pass
        idem_seed = f"out:{channel_connection_id}:{external_chat_id}:{outbound_chat_log_id or uuid.uuid4().hex}:{text}"
        idem = f"reply:{hashlib.sha256(idem_seed.encode('utf-8')).hexdigest()[:40]}"
        row = ChannelMessage(
            organization_id=int(connection.organization_id),
            conversation_id=conversation_id,
            channel_connection_id=int(connection.id),
            chat_log_id=outbound_chat_log_id,
            trace_id=(trace_id or build_trace_id()).strip(),
            correlation_id=(correlation_id or f"chat_log:{outbound_chat_log_id or ''}").strip(),
            provider=connection.provider,
            direction="out",
            external_chat_id=(external_chat_id or "").strip(),
            idempotency_key=idem,
            status="pending",
            message_type="text",
            text=text or "",
            payload_json={"message": ChannelMessageContent(type="text", text=text or "").model_dump()},
            next_attempt_at=datetime.now(timezone.utc),
        )
        db.add(row)
        await db.flush()
        await db.commit()
        await db.refresh(row)
        return row


async def send_channel_text(
    *,
    channel_connection_id: int,
    external_chat_id: str,
    text: str,
    outbound_chat_log_id: int | None = None,
) -> None:
    row = await enqueue_outbound_text(
        channel_connection_id=channel_connection_id,
        external_chat_id=external_chat_id,
        text=text,
        outbound_chat_log_id=outbound_chat_log_id,
    )
    await dispatch_outbound_message(int(row.id))


async def dispatch_outbound_message(channel_message_id: int) -> None:
    async with async_session_factory() as db:
        msg = await db.get(ChannelMessage, int(channel_message_id))
        if msg is None or msg.direction != "out":
            return
        connection = await db.get(ChannelConnection, int(msg.channel_connection_id))
        if connection is None:
            return
        msg.status = "processing"
        msg.processing_at = datetime.now(timezone.utc)
        msg.attempt_count = int(msg.attempt_count or 0) + 1
        await db.commit()

    base = (settings.messaging_gateway_url or "").strip().rstrip("/")
    if not base:
        await _mark_outbound_failed(
            int(channel_message_id),
            code="gateway_url_missing",
            message="MESSAGING_GATEWAY_URL is not configured",
            retry=False,
        )
        return

    headers = {}
    if settings.messaging_gateway_secret:
        headers["X-RestoMind-Gateway-Secret"] = settings.messaging_gateway_secret
    payload = {
        "channel_message_id": int(msg.id),
        "channel_connection_id": int(msg.channel_connection_id),
        "provider": msg.provider,
        "external_chat_id": msg.external_chat_id,
        "message": {"type": msg.message_type, "text": msg.text, "payload": {}, "metadata": {}},
        "trace_id": msg.trace_id,
        "correlation_id": msg.correlation_id,
        "idempotency_key": msg.idempotency_key,
    }
    try:
        async with httpx.AsyncClient(timeout=settings.messaging_gateway_send_timeout_sec) as client:
            resp = await client.post(f"{base}/v1/send", json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json() if resp.content else {}
    except Exception as exc:
        retry = int(msg.attempt_count or 0) < 5
        await _mark_outbound_failed(
            int(channel_message_id),
            code=exc.__class__.__name__,
            message=str(exc),
            retry=retry,
        )
        return

    external_id = ""
    if isinstance(data, dict):
        external_id = str(data.get("external_message_id") or data.get("message_id") or "")
    await apply_delivery_event(
        ChannelDeliveryEvent(
            channel_message_id=int(channel_message_id),
            channel_connection_id=int(msg.channel_connection_id),
            provider=msg.provider,
            external_message_id=external_id,
            status="sent",
            raw=data if isinstance(data, dict) else {},
        )
    )


async def _mark_outbound_failed(channel_message_id: int, *, code: str, message: str, retry: bool) -> None:
    async with async_session_factory() as db:
        msg = await db.get(ChannelMessage, int(channel_message_id))
        if msg is None:
            return
        msg.error_code = code[:100]
        msg.error_message = message[:2000]
        msg.status = "retrying" if retry else "failed"
        msg.next_attempt_at = datetime.now(timezone.utc) + timedelta(seconds=_retry_delay(msg.attempt_count)) if retry else None
        if not retry:
            msg.failed_at = datetime.now(timezone.utc)
        evt = None
        if msg.chat_log_id is not None:
            evt = await finalize_outbound_delivery(
                db,
                int(msg.chat_log_id),
                send_ok=False,
                error_details={"channel": msg.provider, "code": code, "detail": message[:500]},
            )
        await db.commit()
    if evt is not None:
        await publish_event("message_status_updated", evt)


async def apply_connection_status(event: ChannelConnectionStatusEvent) -> ChannelConnectionOut:
    async with async_session_factory() as db:
        row = await db.get(ChannelConnection, int(event.channel_connection_id))
        if row is None:
            raise ValueError(f"channel_connection_not_found:{event.channel_connection_id}")
        row.provider = normalize_provider(event.provider or row.provider)
        row.status = (event.status or row.status or "error").strip().lower()
        if event.phone:
            row.phone = event.phone.strip()
            row.external_account_id = event.external_account_id.strip() or row.phone
        elif event.external_account_id:
            row.external_account_id = event.external_account_id.strip()
        if event.display_name:
            row.display_name = event.display_name.strip()
        if event.session_ref:
            row.session_ref = event.session_ref.strip()
        row.last_qr = event.qr or ""
        row.health_json = dict(event.health or {})
        row.last_error = event.error or ""
        row.last_seen_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(row)
        out = channel_connection_to_out(row)
    await publish_event(
        "channel_connection_updated",
        {
            "organization_id": out.organization_id,
            "connection": out.model_dump(mode="json"),
        },
    )
    return out


async def apply_delivery_event(event: ChannelDeliveryEvent) -> ChannelMessageOut | None:
    async with async_session_factory() as db:
        row: ChannelMessage | None = None
        if event.channel_message_id is not None:
            row = await db.get(ChannelMessage, int(event.channel_message_id))
        if row is None and event.external_message_id:
            row = await db.scalar(
                select(ChannelMessage)
                .where(
                    ChannelMessage.external_message_id == event.external_message_id,
                    ChannelMessage.direction == "out",
                )
                .order_by(ChannelMessage.id.desc())
            )
        if row is None:
            return None
        now = datetime.now(timezone.utc)
        status = (event.status or "").strip().lower()
        if event.external_message_id:
            row.external_message_id = event.external_message_id
        if status in {"sent", "delivered", "read"}:
            row.status = status
            row.error_code = ""
            row.error_message = ""
            if status == "sent":
                row.sent_at = now
            elif status == "delivered":
                row.delivered_at = now
            elif status == "read":
                row.read_at = now
        elif status == "failed":
            row.status = "failed"
            row.error_code = event.error_code[:100]
            row.error_message = event.error_message[:2000]
            row.failed_at = now
        evt = None
        if row.chat_log_id is not None:
            evt = await finalize_outbound_delivery(
                db,
                int(row.chat_log_id),
                send_ok=status in {"sent", "delivered", "read"},
                provider_message_id=row.external_message_id or None,
                error_details=None if status != "failed" else {"channel": row.provider, "raw": event.raw},
            )
        await db.commit()
        await db.refresh(row)
        out = channel_message_to_out(row)
    if evt is not None:
        await publish_event("message_status_updated", evt)
    return out


async def list_due_outbound_messages(limit: int = 50, *, connection_id: int | None = None) -> list[ChannelMessageOut]:
    async with async_session_factory() as db:
        now = datetime.now(timezone.utc)
        stale_processing_before = now - timedelta(minutes=5)
        stmt = (
            select(ChannelMessage)
            .where(
                ChannelMessage.direction == "out",
                or_(
                    (
                        ChannelMessage.status.in_(("pending", "retrying"))
                        & (ChannelMessage.next_attempt_at.is_(None) | (ChannelMessage.next_attempt_at <= now))
                    ),
                    (
                        (ChannelMessage.status == "processing")
                        & ChannelMessage.processing_at.is_not(None)
                        & (ChannelMessage.processing_at <= stale_processing_before)
                    ),
                ),
            )
            .order_by(ChannelMessage.id.asc())
            .limit(max(1, min(100, int(limit or 50))))
        )
        if connection_id is not None:
            stmt = stmt.where(ChannelMessage.channel_connection_id == int(connection_id))
        rows = (await db.execute(stmt)).scalars().all()
        return [channel_message_to_out(r) for r in rows]


async def dispatch_due_outbound_messages(limit: int = 50) -> int:
    rows = await list_due_outbound_messages(limit=limit)
    count = 0
    for row in rows:
        await dispatch_outbound_message(int(row.id))
        count += 1
    return count


async def list_due_inbound_messages(limit: int = 50) -> list[ChannelMessageOut]:
    async with async_session_factory() as db:
        now = datetime.now(timezone.utc)
        stale_processing_before = now - timedelta(minutes=5)
        stmt = (
            select(ChannelMessage)
            .where(
                ChannelMessage.direction == "in",
                or_(
                    (
                        ChannelMessage.status.in_(("received", "retrying"))
                        & (ChannelMessage.next_attempt_at.is_(None) | (ChannelMessage.next_attempt_at <= now))
                    ),
                    (
                        (ChannelMessage.status == "processing")
                        & ChannelMessage.processing_at.is_not(None)
                        & (ChannelMessage.processing_at <= stale_processing_before)
                    ),
                ),
            )
            .order_by(ChannelMessage.id.asc())
            .limit(max(1, min(100, int(limit or 50))))
        )
        rows = (await db.execute(stmt)).scalars().all()
        return [channel_message_to_out(r) for r in rows]


async def process_due_inbound_messages(limit: int = 50) -> int:
    rows = await list_due_inbound_messages(limit=limit)
    count = 0
    for row in rows:
        await process_channel_message(int(row.id))
        count += 1
    return count
