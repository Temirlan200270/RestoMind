"""Staging-style e2e: Twilio voice incoming → WebSocket dispatch (realtime vs stt_fallback)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.core.config import settings
from app.core.passwords import hash_password
from app.db.models import Organization, StaffUser


async def _seed_voice_org(session_factory, *, mode: str = "realtime") -> tuple[int, str]:
    async with session_factory() as db:
        org = Organization(
            name="Voice Staging Org",
            slug="voice-staging-org",
            meta_json={
                "voice_ai_enabled": True,
                "voice_ai_mode": mode,
                "twilio_voice_number": "+77771234567",
            },
        )
        db.add(org)
        await db.flush()
        org_id = int(org.id)
        db.add(
            StaffUser(
                organization_id=org_id,
                email="voice-staging@test.kz",
                password_hash=hash_password("secret123"),
                role="admin",
                is_active=True,
            ),
        )
        await db.commit()
        return org_id, "+77771234567"


@pytest.mark.asyncio
async def test_voice_incoming_returns_stream_twiml(asgi_memory_client, monkeypatch) -> None:
    client, session_factory = asgi_memory_client
    org_id, to_number = await _seed_voice_org(session_factory, mode="stt_fallback")

    monkeypatch.setattr(settings, "twilio_auth_token", "")
    monkeypatch.setattr(settings, "public_base_url", "https://voice-staging.test")
    monkeypatch.setattr(settings, "default_organization_id", org_id)

    res = await client.post(
        "/api/whatsapp/voice/incoming",
        data={
            "CallSid": "CA-STAGING-001",
            "From": "+77051234567",
            "To": to_number,
        },
    )
    assert res.status_code == 200
    assert "application/xml" in res.headers.get("content-type", "")
    body = res.text
    assert "<Stream url=" in body
    assert "voice/stream" in body


@pytest.mark.asyncio
async def test_voice_incoming_disabled_hangup(asgi_memory_client, monkeypatch) -> None:
    client, session_factory = asgi_memory_client
    async with session_factory() as db:
        org = Organization(
            name="Voice Off",
            slug="voice-off",
            meta_json={"voice_ai_enabled": False, "twilio_voice_number": "+77770000001"},
        )
        db.add(org)
        await db.commit()

    monkeypatch.setattr(settings, "twilio_auth_token", "")
    monkeypatch.setattr(settings, "public_base_url", "https://voice-staging.test")

    res = await client.post(
        "/api/whatsapp/voice/incoming",
        data={
            "CallSid": "CA-OFF",
            "From": "+77051111111",
            "To": "+77770000001",
        },
    )
    assert res.status_code == 200
    assert "<Hangup/>" in res.text


@pytest.mark.asyncio
async def test_voice_stream_dispatches_realtime_mode(asgi_memory_client, monkeypatch) -> None:
    from unittest.mock import AsyncMock, MagicMock

    from app.api import webhooks

    client, session_factory = asgi_memory_client
    org_id, to_number = await _seed_voice_org(session_factory, mode="realtime")

    monkeypatch.setattr(settings, "twilio_auth_token", "")
    monkeypatch.setattr(settings, "public_base_url", "https://voice-staging.test")
    monkeypatch.setattr(settings, "openai_api_key", "sk-test-staging")

    await client.post(
        "/api/whatsapp/voice/incoming",
        data={
            "CallSid": "CA-RT-001",
            "From": "+77052222222",
            "To": to_number,
        },
    )

    ws = MagicMock()
    ws.accept = AsyncMock()
    bridge = AsyncMock()
    with patch("app.services.voice_realtime.run_realtime_voice_bridge", bridge):
        with patch.object(
            webhooks,
            "_await_twilio_stream_start",
            AsyncMock(return_value=(
                "CA-RT-001",
                "+77052222222",
                org_id,
                "realtime",
                "Voice Staging Org",
                "MZ-RT-001",
            )),
        ):
            await webhooks.twilio_voice_stream(ws)

    bridge.assert_awaited_once()
    kwargs = bridge.await_args.kwargs
    assert kwargs["org_id"] == org_id
    assert kwargs["call_sid"] == "CA-RT-001"
    assert kwargs["phone"] == "+77052222222"


@pytest.mark.asyncio
async def test_voice_stream_dispatches_stt_fallback(asgi_memory_client, monkeypatch) -> None:
    from unittest.mock import AsyncMock, MagicMock

    from app.api import webhooks

    client, session_factory = asgi_memory_client
    org_id, to_number = await _seed_voice_org(session_factory, mode="stt_fallback")

    monkeypatch.setattr(settings, "twilio_auth_token", "")
    monkeypatch.setattr(settings, "public_base_url", "https://voice-staging.test")

    await client.post(
        "/api/whatsapp/voice/incoming",
        data={
            "CallSid": "CA-STT-001",
            "From": "+77053333333",
            "To": to_number,
        },
    )

    ws = MagicMock()
    ws.accept = AsyncMock()
    stt = AsyncMock()
    with patch.object(webhooks, "_run_stt_fallback_voice_stream", stt):
        with patch.object(
            webhooks,
            "_await_twilio_stream_start",
            AsyncMock(return_value=(
                "CA-STT-001",
                "+77053333333",
                org_id,
                "stt_fallback",
                "Voice Staging Org",
                "MZ-STT-001",
            )),
        ):
            await webhooks.twilio_voice_stream(ws)

    stt.assert_awaited_once()
    assert stt.await_args.kwargs["org_id"] == org_id
    assert stt.await_args.kwargs["call_sid"] == "CA-STT-001"


@pytest.mark.asyncio
async def test_voice_status_api_realtime_ready(asgi_memory_client, monkeypatch) -> None:
    client, session_factory = asgi_memory_client
    org_id, _ = await _seed_voice_org(session_factory, mode="realtime")

    monkeypatch.setattr(settings, "openai_api_key", "sk-test")
    monkeypatch.setattr(settings, "public_base_url", "https://voice-staging.test")

    async with session_factory() as db:
        from app.db.models import StaffUser as SU
        from sqlalchemy import select

        staff = await db.scalar(select(SU).where(SU.email == "voice-staging@test.kz"))
        assert staff is not None

    login = await client.post(
        "/api/admin/auth/login",
        json={"username": "voice-staging@test.kz", "password": "secret123"},
    )
    assert login.status_code == 200

    status = await client.get("/api/admin/intelligence/voice/status")
    assert status.status_code == 200
    item = status.json()["item"]
    assert item["enabled"] is True
    assert item["mode"] == "realtime"
    assert item["realtime_ready"] is True


@pytest.mark.asyncio
async def test_voice_config_requires_admin_role(asgi_memory_client, monkeypatch) -> None:
    client, session_factory = asgi_memory_client
    org_id, _ = await _seed_voice_org(session_factory, mode="stt_fallback")

    async with session_factory() as db:
        from app.core.passwords import hash_password
        from app.db.models import StaffUser

        db.add(
            StaffUser(
                organization_id=org_id,
                email="voice-operator@test.kz",
                password_hash=hash_password("secret123"),
                role="operator",
                is_active=True,
            )
        )
        await db.commit()

    monkeypatch.setattr(settings, "default_organization_id", org_id)

    login = await client.post(
        "/api/admin/auth/login",
        json={"username": "voice-operator@test.kz", "password": "secret123"},
    )
    assert login.status_code == 200

    denied = await client.post(
        "/api/admin/intelligence/voice/config",
        json={"enabled": True, "mode": "realtime"},
    )
    assert denied.status_code == 403

    admin_login = await client.post(
        "/api/admin/auth/login",
        json={"username": "voice-staging@test.kz", "password": "secret123"},
    )
    assert admin_login.status_code == 200

    ok = await client.post(
        "/api/admin/intelligence/voice/config",
        json={"enabled": True, "mode": "realtime"},
    )
    assert ok.status_code == 200
    assert ok.json()["item"]["mode"] == "realtime"
