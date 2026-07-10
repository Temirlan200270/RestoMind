from __future__ import annotations

import asyncio
from pathlib import Path

from app.schemas.messaging import ChannelInboundEvent
from app.services.messaging_gateway import (
    BAILEYS_PROVIDER,
    build_channel_idempotency_key,
    normalize_provider,
)
from app.services.telegram_customer import normalize_customer_channel
from app.worker import WorkerSettings


def test_baileys_channel_is_first_class_customer_channel() -> None:
    assert normalize_customer_channel("whatsapp_baileys") == "whatsapp_baileys"
    assert normalize_provider("baileys") == BAILEYS_PROVIDER
    assert normalize_provider("whatsapp-web") == BAILEYS_PROVIDER


def test_channel_inbound_contract_keeps_trace_and_metadata() -> None:
    event = ChannelInboundEvent.model_validate(
        {
            "trace_id": "trace-1",
            "correlation_id": "corr-1",
            "idempotency_key": "idem-1",
            "provider": "whatsapp_baileys",
            "channel_connection_id": 7,
            "external_chat_id": "77001234567@s.whatsapp.net",
            "external_message_id": "BAE123",
            "sender": {"external_id": "77001234567", "phone": "+77001234567"},
            "message": {
                "type": "text",
                "text": "Плов есть?",
                "payload": {},
                "metadata": {"quoted_message_id": "prev"},
            },
        }
    )

    assert event.trace_id == "trace-1"
    assert event.message.metadata["quoted_message_id"] == "prev"


def test_channel_idempotency_key_prefers_external_message_id() -> None:
    key = build_channel_idempotency_key(
        provider="whatsapp_baileys",
        channel_connection_id=5,
        external_message_id="BAE123",
        fallback_seed="ignored",
    )

    assert key == "whatsapp_baileys:5:BAE123"


def test_worker_registers_channel_gateway_jobs() -> None:
    fn_names = {getattr(fn, "__name__", "") for fn in WorkerSettings.functions}

    assert "channel_process_inbound" in fn_names
    assert "channel_dispatch_outbound" in fn_names
    assert "channel_dispatch_due_outbound" in fn_names
    assert "channel_process_due_inbound" in fn_names


def test_gateway_delegates_inbound_processing_to_conversation_service(monkeypatch) -> None:
    from app.services.conversation_service import conversation_service
    from app.services.messaging_gateway import process_channel_message

    calls: list[int] = []

    async def fake_process(channel_message_id: int) -> None:
        calls.append(channel_message_id)

    monkeypatch.setattr(conversation_service, "process_channel_message", fake_process)

    asyncio.run(process_channel_message(42))

    assert calls == [42]


def test_default_outbound_channel_policy_is_wired() -> None:
    model = Path("app/db/models.py").read_text(encoding="utf-8")
    schema = Path("app/schemas/messaging.py").read_text(encoding="utf-8")
    api = Path("app/api/channels.py").read_text(encoding="utf-8")
    service = Path("app/services/messaging_gateway.py").read_text(encoding="utf-8")
    reply = Path("app/services/customer_reply.py").read_text(encoding="utf-8")
    ui = Path("app/static/js/admin-app.js").read_text(encoding="utf-8")
    tpl = Path("app/templates/screens/_tab_settings_connections.html").read_text(encoding="utf-8")
    migration = Path("alembic/versions/20260709_default_outbound_channel.py").read_text(encoding="utf-8")

    assert "is_default_outbound" in model
    assert "is_default_outbound: bool = False" in schema
    assert '@admin_router.patch("/{connection_id}/set-default"' in api
    assert "set_default_outbound_connection" in service
    assert "resolve_default_outbound_connection" in service
    assert "_try_send_via_default_baileys" in reply
    assert "send_message(phone, text)" in reply
    assert "setDefaultChannelConnection" in ui
    assert "Для новых рассылок" in tpl
    assert "uq_default_channel_per_org" in migration
    assert "postgresql_where=sa.text(\"is_default_outbound = true\")" in migration


def test_admin_chat_outbound_uses_customer_reply_router() -> None:
    chats = Path("app/api/admin/chats.py").read_text(encoding="utf-8")
    admin_init = Path("app/api/admin/__init__.py").read_text(encoding="utf-8")

    assert "from app.services.customer_reply import send_customer_text" in chats
    assert "send_customer_text(" in chats
    assert "from app.integrations.whatsapp import send_message" not in chats
    assert "await send_message(" not in chats
    assert "send_message" not in admin_init
