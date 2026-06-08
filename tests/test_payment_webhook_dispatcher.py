"""Маршрутизация /webhooks/payment/providers/{slug}: неизвестный slug → 404 + аудит."""

import hashlib
import hmac
import json

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.core.config as app_config
import app.db.session as db_session_module
from app.db.models import PaymentWebhookEvent
from app.db.session import get_db
from app.main import app
from tests.db_helpers import install_app_db_override

PW_HOOK_BEARER = "hook-secret"
PW_HOOK_HMAC = "pw-test-hmac"


def _payment_hmac_hex(secret: str, raw: bytes) -> str:
    return hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


@pytest_asyncio.fixture
async def disp_client(monkeypatch, postgres_session_factory):
    monkeypatch.setattr(app_config.settings, "payment_webhook_bearer_token", PW_HOOK_BEARER)
    monkeypatch.setattr(app_config.settings, "payment_webhook_hmac_secret", PW_HOOK_HMAC)

    session_factory = postgres_session_factory
    install_app_db_override(app, get_db, monkeypatch, db_session_module, session_factory)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac, session_factory

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_unknown_provider_slug_404_and_audit(disp_client):
    ac, sf = disp_client
    body = {"order_id": 1, "organization_id": 1, "payment_id": "x12345678", "status": "paid"}
    raw = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    sig = _payment_hmac_hex(PW_HOOK_HMAC, raw)
    r = await ac.post(
        "/api/webhooks/payment/providers/no_such_provider_xyz",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {PW_HOOK_BEARER}",
            "X-RestoMind-Payment-Signature": sig,
        },
    )
    assert r.status_code == 404

    async with sf() as db:
        ev = (await db.execute(select(PaymentWebhookEvent))).scalars().first()
        assert ev is not None
        assert ev.provider_slug == "no_such_provider_xyz"
        assert "unknown_provider" in (ev.verify_error or "")
