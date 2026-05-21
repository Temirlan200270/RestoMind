"""Tests for OpenAI Realtime voice bridge (mocked / unit)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.config import settings
from app.db.models import MenuItem, Organization
from app.services.voice_ai import get_voice_mode, realtime_ready_for_org, voice_status_for_org
from app.services.voice_realtime.session import RealtimeVoiceSession, _realtime_ws_url
from app.services.voice_realtime.tools import dispatch_realtime_tool
from app.services.voice_realtime.twilio_bridge import (
    mulaw_8k_to_pcm16_24k,
    pcm16_24k_to_mulaw_8k,
    run_realtime_voice_bridge,
)

try:
    import audioop
except ImportError:
    audioop = None


@pytest.mark.skipif(audioop is None, reason="audioop required for audio conversion tests")
def test_mulaw_pcm_roundtrip_non_empty() -> None:
    mulaw = bytes([0xFF] * 160)
    pcm24 = mulaw_8k_to_pcm16_24k(mulaw)
    assert len(pcm24) > 0
    back = pcm16_24k_to_mulaw_8k(pcm24)
    assert len(back) == len(mulaw)


def test_realtime_ws_url_default() -> None:
    url = _realtime_ws_url()
    assert url.startswith("wss://")
    assert "realtime" in url
    assert settings.openai_realtime_model in url


async def _seed_menu(session_factory, org_id: int = 1) -> None:
    async with session_factory() as db:
        org = await db.get(Organization, org_id)
        if org is None:
            db.add(Organization(id=org_id, name="Test Org", slug="test-org"))
            await db.flush()
        db.add(
            MenuItem(
                organization_id=org_id,
                name="Плов",
                category="Горячее",
                price=2790.0,
                is_available=True,
            )
        )
        db.add(
            MenuItem(
                organization_id=org_id,
                name="Лагман",
                category="Первое",
                price=1990.0,
                is_available=True,
            )
        )
        await db.commit()


@pytest.mark.asyncio
async def test_dispatch_lookup_menu_from_db(asgi_memory_client) -> None:
    _, session_factory = asgi_memory_client
    await _seed_menu(session_factory)

    out = await dispatch_realtime_tool("lookup_menu", '{"query":"плов"}', org_id=1, phone="+7705")
    data = json.loads(out)
    assert data["ok"] is True
    assert "2790" in data["message"]
    assert any(i["name"] == "Плов" for i in data["items"])


@pytest.mark.asyncio
async def test_dispatch_lookup_menu_empty_query(asgi_memory_client) -> None:
    _, session_factory = asgi_memory_client
    await _seed_menu(session_factory)

    out = await dispatch_realtime_tool("lookup_menu", "{}", org_id=1, phone="+7705")
    data = json.loads(out)
    assert data["ok"] is True
    assert len(data["items"]) >= 1


@pytest.mark.asyncio
async def test_dispatch_lookup_menu_tenant_isolation(asgi_memory_client) -> None:
    _, session_factory = asgi_memory_client
    async with session_factory() as db:
        db.add(Organization(id=2, name="Other", slug="other"))
        db.add(
            MenuItem(
                organization_id=2,
                name="Секретное блюдо",
                category="X",
                price=9999.0,
                is_available=True,
            )
        )
        await db.commit()

    out = await dispatch_realtime_tool("lookup_menu", '{"query":"секрет"}', org_id=1, phone="+7705")
    data = json.loads(out)
    assert data["ok"] is True
    assert "9999" not in data["message"]
    assert data["items"] == []


@pytest.mark.asyncio
async def test_dispatch_escalate_sends_whatsapp(asgi_memory_client, monkeypatch) -> None:
    _, session_factory = asgi_memory_client
    async with session_factory() as db:
        db.add(Organization(id=1, name="Voice Org", slug="voice-org"))
        await db.commit()

    send_mock = AsyncMock(return_value=MagicMock(ok=True, message_id="wamid.voice", error=None))
    log_mock = MagicMock()
    monkeypatch.setattr("app.services.voice_realtime.tools.send_message", send_mock)
    monkeypatch.setattr("app.services.voice_realtime.tools.schedule_log_message", log_mock)

    out = await dispatch_realtime_tool(
        "escalate_to_whatsapp",
        '{"reason":"заказ"}',
        org_id=1,
        phone="+77051234567",
    )
    data = json.loads(out)
    assert data["ok"] is True
    assert data["whatsapp_sent"] is True
    assert "WhatsApp" in data["message"]
    send_mock.assert_awaited_once()
    log_mock.assert_called_once_with(1, "outbound", "ai", "text")


@pytest.mark.asyncio
async def test_dispatch_escalate_without_phone(asgi_memory_client, monkeypatch) -> None:
    _, session_factory = asgi_memory_client
    async with session_factory() as db:
        db.add(Organization(id=1, name="Voice Org", slug="voice-org"))
        await db.commit()

    send_mock = AsyncMock()
    monkeypatch.setattr("app.services.voice_realtime.tools.send_message", send_mock)

    out = await dispatch_realtime_tool(
        "escalate_to_whatsapp",
        '{"reason":"бронь"}',
        org_id=1,
        phone="",
    )
    data = json.loads(out)
    assert data["ok"] is True
    assert data["whatsapp_sent"] is False
    send_mock.assert_not_awaited()


def test_get_voice_mode() -> None:
    org = Organization(id=1, name="T", slug="t", meta_json={"voice_ai_mode": "realtime"})
    assert get_voice_mode(org) == "realtime"
    org.meta_json = {}
    assert get_voice_mode(org) == "stt_fallback"


def test_realtime_ready_requires_key_and_url(monkeypatch) -> None:
    org = Organization(
        id=1,
        name="T",
        slug="t",
        meta_json={"voice_ai_mode": "realtime"},
    )
    monkeypatch.setattr(settings, "openai_api_key", "")
    monkeypatch.setattr(settings, "public_base_url", "https://example.com")
    assert realtime_ready_for_org(org) is False

    monkeypatch.setattr(settings, "openai_api_key", "sk-test")
    assert realtime_ready_for_org(org) is True

    status = voice_status_for_org(org)
    assert status["realtime_ready"] is False  # enabled flag off

    org.meta_json = {"voice_ai_mode": "realtime", "voice_ai_enabled": True}
    status = voice_status_for_org(org)
    assert status["realtime_ready"] is True


@pytest.mark.asyncio
async def test_realtime_session_connect_mock() -> None:
    mock_ws = AsyncMock()
    mock_ws.__aiter__ = MagicMock(return_value=iter([]))

    with patch("websockets.connect", new_callable=AsyncMock, return_value=mock_ws):
        session = RealtimeVoiceSession(
            org_id=1,
            phone="+7705",
            call_sid="CA123",
            instructions="test",
        )
        with patch.object(settings, "openai_api_key", "sk-test"):
            await session.connect()
        assert session.connected
        mock_ws.send.assert_called()
        await session.close()


@pytest.mark.asyncio
async def test_run_realtime_bridge_connect_failure(monkeypatch) -> None:
    ws = AsyncMock()
    ws.receive_text = AsyncMock(side_effect=Exception("stop"))

    with patch(
        "app.services.voice_realtime.twilio_bridge.RealtimeVoiceSession.connect",
        new_callable=AsyncMock,
        side_effect=RuntimeError("no api key"),
    ):
        with patch(
            "app.services.voice_realtime.twilio_bridge._graceful_voice_fallback",
            new_callable=AsyncMock,
        ) as hangup:
            await run_realtime_voice_bridge(
                ws,
                org_id=1,
                phone="+7705",
                call_sid="CA1",
                stream_sid="MZ1",
            )
            hangup.assert_awaited_once_with("CA1")
