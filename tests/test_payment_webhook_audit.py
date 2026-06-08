"""Аудит payment_webhook_events: запись до верификации, список superadmin."""

import hashlib
import hmac
import json

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.core.config as app_config
import app.db.session as db_session_module
from app.db.models import Order, Organization, PaymentWebhookEvent, User
from app.db.session import get_db
from app.main import app
from tests.db_helpers import install_app_db_override

PW_HOOK_BEARER = "hook-secret"
PW_HOOK_HMAC = "pw-test-hmac"


def _payment_hmac_hex(secret: str, raw: bytes) -> str:
    return hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


def _payment_body_bytes(body: dict) -> bytes:
    return json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


async def _seed_order(session_factory: async_sessionmaker[AsyncSession]) -> tuple[int, int]:
    async with session_factory() as db:
        org = Organization(name="O", slug="o")
        db.add(org)
        await db.flush()
        user = User(organization_id=org.id, phone="+77001112233")
        db.add(user)
        await db.flush()
        order = Order(
            organization_id=org.id,
            user_id=user.id,
            status="draft",
            total_price=5000,
            prepayment_status="pending",
        )
        db.add(order)
        await db.flush()
        oid, gid = order.id, org.id
        await db.commit()
    return oid, gid


@pytest_asyncio.fixture
async def pw_audit_client(monkeypatch, postgres_session_factory):
    monkeypatch.setattr(app_config.settings, "payment_webhook_bearer_token", PW_HOOK_BEARER)
    monkeypatch.setattr(app_config.settings, "payment_webhook_hmac_secret", PW_HOOK_HMAC)

    async def _noop_arq_dispatch(*_a, **_kw):
        return None

    monkeypatch.setattr(
        "app.services.task_queue.dispatch_arq_or_background",
        _noop_arq_dispatch,
    )

    session_factory = postgres_session_factory
    install_app_db_override(app, get_db, monkeypatch, db_session_module, session_factory)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac, session_factory

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_payment_webhook_audit_row_on_success(pw_audit_client):
    ac, sf = pw_audit_client
    order_id, org_id = await _seed_order(sf)
    body = {
        "order_id": order_id,
        "organization_id": org_id,
        "payment_id": "txn-audit-1",
        "status": "paid",
        "amount": 5000,
    }
    raw = _payment_body_bytes(body)
    sig = _payment_hmac_hex(PW_HOOK_HMAC, raw)
    r = await ac.post(
        "/api/webhooks/payment",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {PW_HOOK_BEARER}",
            "X-RestoMind-Payment-Signature": sig,
        },
    )
    assert r.status_code == 200

    async with sf() as db:
        n = int(
            await db.scalar(select(func.count()).select_from(PaymentWebhookEvent)) or 0,
        )
        assert n == 1
        ev = (await db.execute(select(PaymentWebhookEvent))).scalars().first()
        assert ev is not None
        assert ev.provider_slug == "restomind_json"
        assert ev.verified is True
        assert ev.applied is True
        assert ev.duplicate is False


@pytest.mark.asyncio
async def test_payment_webhook_audit_invalid_signature(pw_audit_client):
    ac, sf = pw_audit_client
    order_id, org_id = await _seed_order(sf)
    body = {
        "order_id": order_id,
        "organization_id": org_id,
        "payment_id": "txn-bad-sig",
        "status": "paid",
    }
    raw = _payment_body_bytes(body)
    r = await ac.post(
        "/api/webhooks/payment",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {PW_HOOK_BEARER}",
            "X-RestoMind-Payment-Signature": "deadbeef",
        },
    )
    assert r.status_code == 401

    async with sf() as db:
        ev = (await db.execute(select(PaymentWebhookEvent))).scalars().first()
        assert ev is not None
        assert ev.verified is False
        assert "Invalid payment webhook signature" in (ev.verify_error or "") or ev.verify_error


@pytest.mark.asyncio
async def test_payment_webhook_audit_invalid_json(pw_audit_client):
    ac, sf = pw_audit_client
    raw = b"{not-json"
    sig = _payment_hmac_hex(PW_HOOK_HMAC, raw)
    r = await ac.post(
        "/api/webhooks/payment",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {PW_HOOK_BEARER}",
            "X-RestoMind-Payment-Signature": sig,
        },
    )
    assert r.status_code == 422

    async with sf() as db:
        ev = (await db.execute(select(PaymentWebhookEvent))).scalars().first()
        assert ev is not None
        assert ev.applied is False
