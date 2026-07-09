from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from app.db.models import ChannelMessage, Organization
from app.schemas.messaging import ChannelInboundEvent
from app.services.messaging_gateway import (
    enqueue_outbound_text,
    ensure_channel_connection,
    record_inbound_event,
)


@pytest.mark.asyncio
async def test_record_inbound_event_is_idempotent_and_creates_conversation(db_session) -> None:
    org = Organization(name="Gateway Org", slug="gateway-org")
    db_session.add(org)
    await db_session.flush()
    conn = await ensure_channel_connection(
        db_session,
        organization_id=int(org.id),
        provider="whatsapp_baileys",
        phone="+77001234567",
    )
    event = ChannelInboundEvent.model_validate(
        {
            "provider": "whatsapp_baileys",
            "channel_connection_id": int(conn.id),
            "external_chat_id": "77001234567@s.whatsapp.net",
            "external_message_id": "BAE123",
            "sender": {"external_id": "77001234567", "phone": "+77001234567"},
            "message": {"type": "text", "text": "Здравствуйте", "payload": {}, "metadata": {}},
        }
    )

    first, created_first = await record_inbound_event(db_session, event)
    second, created_second = await record_inbound_event(db_session, event)

    assert created_first is True
    assert created_second is False
    assert second.id == first.id
    assert first.conversation_id is not None
    assert first.status == "received"
    assert first.idempotency_key == "whatsapp_baileys:%d:BAE123" % int(conn.id)


@pytest.mark.asyncio
async def test_enqueue_outbound_text_creates_pending_channel_message(db_session, monkeypatch) -> None:
    org = Organization(name="Gateway Out", slug="gateway-out")
    db_session.add(org)
    await db_session.flush()
    conn = await ensure_channel_connection(
        db_session,
        organization_id=int(org.id),
        provider="whatsapp_baileys",
        phone="+77007654321",
    )
    await db_session.commit()

    from app.services import messaging_gateway as mg

    @asynccontextmanager
    async def _session_factory():
        yield db_session

    monkeypatch.setattr(mg, "async_session_factory", _session_factory)
    msg = await enqueue_outbound_text(
        channel_connection_id=int(conn.id),
        external_chat_id="77007654321@s.whatsapp.net",
        text="Да, плов есть.",
        outbound_chat_log_id=None,
        trace_id="trace-out",
        correlation_id="corr-out",
    )

    row = await db_session.get(ChannelMessage, int(msg.id))
    assert row is not None
    assert row.direction == "out"
    assert row.status == "pending"
    assert row.provider == "whatsapp_baileys"
    assert row.text == "Да, плов есть."
