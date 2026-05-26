"""Wave 4 foundation: Telegram customer channel + POS adapter layer."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.db.models import ChatLog, Organization, User
from app.services.pos.adapters.base import ADAPTER_REGISTRY, get_pos_adapter
from app.services.telegram_customer import (
    normalize_customer_channel,
    telegram_synthetic_phone,
)


def test_telegram_synthetic_phone_stable():
    assert telegram_synthetic_phone(123456789) == "tg:123456789"


def test_normalize_customer_channel_defaults_whatsapp():
    assert normalize_customer_channel(None) == "whatsapp"
    assert normalize_customer_channel("TELEGRAM") == "telegram"
    assert normalize_customer_channel("unknown") == "whatsapp"


def test_chat_message_payload_keeps_channel_context():
    from app.services.chat_serializer import ChatMessagePayload

    payload = ChatMessagePayload(
        phone="tg:42",
        message_text="menu",
        whatsapp_message_id="",
        organization_id=1,
        channel="telegram",
        telegram_chat_id=42,
    )

    restored = ChatMessagePayload.from_json(payload.to_json())

    assert restored.channel == "telegram"
    assert restored.telegram_chat_id == 42


@pytest.mark.asyncio
async def test_chat_log_channel_persisted(db_session):
    org = Organization(name="TG Org", slug="tg-org", pos_provider="iiko")
    db_session.add(org)
    await db_session.flush()
    user = User(organization_id=int(org.id), phone="tg:42", telegram_user_id=42)
    db_session.add(user)
    await db_session.flush()

    db_session.add(
        ChatLog(
            organization_id=int(org.id),
            user_id=int(user.id),
            role="user",
            content="Привет",
            channel="telegram",
        )
    )
    await db_session.commit()

    row = await db_session.get(ChatLog, 1)
    assert row is not None
    assert row.channel == "telegram"


@pytest.mark.asyncio
async def test_organization_pos_provider_default_iiko(db_session):
    org = Organization(name="POS Org", slug="pos-org")
    db_session.add(org)
    await db_session.flush()
    assert org.pos_provider == "iiko"


@pytest.mark.asyncio
async def test_pos_adapter_registry_iiko(db_session):
    import app.services.pos.adapters  # noqa: F401

    org = Organization(name="POS2", slug="pos2", pos_provider="iiko")
    db_session.add(org)
    await db_session.flush()

    adapter = await get_pos_adapter(db_session, int(org.id))
    assert adapter.provider_slug == "iiko"
    assert "iiko" in ADAPTER_REGISTRY


@pytest.mark.asyncio
async def test_process_with_retry_delegates_to_process_inbound_message():
    from app.api import webhooks

    with patch.object(webhooks, "process_inbound_message", new_callable=AsyncMock) as mock_inbound:
        await webhooks.process_with_retry(
            "+77001234567",
            "test",
            whatsapp_message_id="wmid-1",
            organization_id=1,
            trace_id="trace-1",
        )
        mock_inbound.assert_awaited_once()
        kwargs = mock_inbound.await_args.kwargs
        assert kwargs["channel"] == "whatsapp"
        assert kwargs["inbound_message_id"] == "wmid-1"
        assert kwargs["organization_id"] == 1


@pytest.mark.asyncio
async def test_handle_telegram_customer_message_routes_private_text():
    from app.services import telegram_customer as tc

    msg = {
        "message_id": 99,
        "from": {"id": 555, "first_name": "Ali"},
        "chat": {"id": 555, "type": "private"},
        "text": "Меню",
    }

    with (
        patch.object(tc, "ensure_telegram_user", new_callable=AsyncMock, return_value="tg:555"),
        patch("app.api.webhooks.process_inbound_message", new_callable=AsyncMock) as mock_pipeline,
    ):
        handled = await tc.handle_telegram_customer_message(msg)

    assert handled is True
    mock_pipeline.assert_awaited_once()
    assert mock_pipeline.await_args.kwargs["channel"] == "telegram"
    assert mock_pipeline.await_args.args[0] == "tg:555"


@pytest.mark.asyncio
async def test_send_telegram_customer_text_no_token():
    from app.services.telegram_customer import send_telegram_customer_text

    with patch("app.services.telegram_customer.settings") as mock_settings:
        mock_settings.telegram_bot_token = ""
        out = await send_telegram_customer_text(123, "hi")
    assert out.get("ok") is False
