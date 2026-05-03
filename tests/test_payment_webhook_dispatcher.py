"""Маршрутизация /webhooks/payment/providers/{slug}: неизвестный slug → 404 + аудит."""

import hashlib
import hmac
import json

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.core.config as app_config
import app.db.session as db_session_module
from app.db.models import Base, PaymentWebhookEvent
from app.db.session import get_db
from app.main import app

PW_HOOK_BEARER = "hook-secret"
PW_HOOK_HMAC = "pw-test-hmac"


def _memory_sqlite_engine():
    return create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def _payment_hmac_hex(secret: str, raw: bytes) -> str:
    return hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


@pytest_asyncio.fixture
async def disp_client(monkeypatch):
    monkeypatch.setattr(app_config.settings, "payment_webhook_bearer_token", PW_HOOK_BEARER)
    monkeypatch.setattr(app_config.settings, "payment_webhook_hmac_secret", PW_HOOK_HMAC)

    engine = _memory_sqlite_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(db_session_module, "async_session_factory", session_factory)

    async def _override_db():
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = _override_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac, session_factory

    app.dependency_overrides.clear()
    await engine.dispose()


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
