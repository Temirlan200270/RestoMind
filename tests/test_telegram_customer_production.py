"""Telegram customer channel production: org mapping + inbound/outbound E2E."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.db.models import ChatLog, Organization, User
from app.services.telegram_customer import (
    customer_channel_for_user,
    handle_telegram_customer_message,
    resolve_org_for_telegram_webhook,
    telegram_bot_token_fingerprint,
    telegram_chat_id_for_user,
    telegram_webhook_authorized,
)


@pytest.mark.asyncio
async def test_resolve_org_by_webhook_secret(db_session):
    org_a = Organization(name="A", slug="tg-a", telegram_webhook_secret="sec-a")
    org_b = Organization(name="B", slug="tg-b", telegram_webhook_secret="sec-b")
    db_session.add_all([org_a, org_b])
    await db_session.flush()

    org, oid = await resolve_org_for_telegram_webhook(db_session, "sec-b")
    assert org is not None
    assert oid == int(org_b.id)


@pytest.mark.asyncio
async def test_resolve_org_by_bot_token_fingerprint(db_session, monkeypatch):
    monkeypatch.setattr("app.services.telegram_customer.settings.telegram_bot_token", "123:ABC")
    fp = telegram_bot_token_fingerprint("123:ABC")
    org = Organization(name="FP", slug="tg-fp", telegram_webhook_secret=fp)
    db_session.add(org)
    await db_session.flush()

    resolved, oid = await resolve_org_for_telegram_webhook(db_session, "", bot_token="123:ABC")
    assert resolved is not None
    assert oid == int(org.id)


def test_telegram_webhook_authorized_org_secret():
    org = Organization(name="X", slug="x", telegram_webhook_secret="org-secret")
    assert telegram_webhook_authorized("org-secret", org) is True
    assert telegram_webhook_authorized("wrong", org) is False


def test_customer_channel_for_telegram_user():
    user = User(organization_id=1, phone="tg:777", telegram_user_id=777)
    assert customer_channel_for_user(user) == "telegram"
    assert telegram_chat_id_for_user(user) == 777


@pytest.mark.asyncio
async def test_handle_telegram_customer_message_uses_resolved_org():
    from app.services import telegram_customer as tc

    msg = {
        "message_id": 11,
        "from": {"id": 9001, "first_name": "Test"},
        "chat": {"id": 9001, "type": "private"},
        "text": "Меню",
    }

    with (
        patch.object(tc, "ensure_telegram_user", new_callable=AsyncMock, return_value="tg:9001"),
        patch("app.api.webhooks.process_inbound_message", new_callable=AsyncMock) as mock_pipeline,
    ):
        handled = await tc.handle_telegram_customer_message(msg, organization_id=42)

    assert handled is True
    assert mock_pipeline.await_args.kwargs["organization_id"] == 42
    assert mock_pipeline.await_args.kwargs["channel"] == "telegram"


@pytest.mark.asyncio
async def test_e2e_telegram_inbound_process_inbound_send_and_chat_log(db_session, monkeypatch):
    """inbound telegram -> process_inbound_message -> send_telegram_customer_text -> ChatLog.channel"""
    org = Organization(name="TG Prod", slug="tg-prod", telegram_webhook_secret="prod-sec")
    db_session.add(org)
    await db_session.flush()
    org_id = int(org.id)

    captured: dict = {}

    async def fake_process_message(
        phone: str,
        message_text: str,
        *,
        organization_id: int,
        **kwargs,
    ) -> None:
        from app.api.webhooks import _save_chat_log
        from app.services.customer_reply import send_customer_text
        from app.services.telegram_customer import current_customer_channel

        captured["channel"] = current_customer_channel()
        outbound_id = await _save_chat_log(
            db_session,
            phone,
            message_text,
            "Ответ бота",
            organization_id=organization_id,
            channel="telegram",
        )
        await db_session.commit()
        await send_customer_text(phone, "Ответ бота", outbound_chat_log_id=outbound_id)

    async def fake_serialized(_org_id, _phone, initial, *, process_one):
        await process_one(initial)
        return True

    monkeypatch.setattr("app.api.webhooks.process_message", fake_process_message)
    monkeypatch.setattr("app.services.chat_serializer.run_serialized_chat_pipeline", fake_serialized)

    with patch(
        "app.services.telegram_customer.send_telegram_customer_text",
        new_callable=AsyncMock,
        return_value={"ok": True, "result": {"message_id": 555}},
    ) as mock_tg_send:
        from app.api.webhooks import process_inbound_message
        from app.services.telegram_customer import customer_channel_context, reset_customer_channel_context

        phone = "tg:9001"
        tokens = customer_channel_context("telegram", telegram_chat_id=9001)
        try:
            await process_inbound_message(
                phone,
                "Привет",
                organization_id=org_id,
                channel="telegram",
                inbound_message_id="11",
            )
        finally:
            reset_customer_channel_context(tokens)

    mock_tg_send.assert_awaited_once()
    assert mock_tg_send.await_args.args[0] == 9001
    assert captured.get("channel") == "telegram"

    rows = (
        await db_session.scalars(
            select(ChatLog).where(ChatLog.organization_id == org_id).order_by(ChatLog.id.asc())
        )
    ).all()
    assert len(rows) >= 2
    assert all(r.channel == "telegram" for r in rows)


def test_canonical_user_phone_preserves_telegram_prefix():
    from app.services.phone_normalize import canonical_user_phone

    assert canonical_user_phone("tg:42") == "tg:42"
    assert canonical_user_phone("TG:99") == "tg:99"


@pytest.mark.asyncio
async def test_customer_reply_routes_telegram_when_channel_active():
    from app.services.customer_reply import send_customer_text
    from app.services.telegram_customer import customer_channel_context, reset_customer_channel_context

    with patch(
        "app.services.telegram_customer.send_telegram_customer_text",
        new_callable=AsyncMock,
        return_value={"ok": True, "result": {"message_id": 1}},
    ) as mock_tg:
        tokens = customer_channel_context("telegram", telegram_chat_id=42)
        try:
            await send_customer_text("tg:42", "operator reply")
        finally:
            reset_customer_channel_context(tokens)

    mock_tg.assert_awaited_once_with(42, "operator reply")
