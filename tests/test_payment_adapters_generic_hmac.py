"""Адаптер generic_hmac: X-Signature-256 hex."""

import hashlib
import hmac
import json

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.core.config as app_config
import app.db.session as db_session_module
from app.db.models import Order, Organization, User
from app.db.session import get_db
from app.main import app
from tests.db_helpers import install_app_db_override

PW_HOOK_HMAC = "adapter-hmac-secret"


def _ghmac_hex(secret: str, raw: bytes) -> str:
    return hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


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
async def gh_client(monkeypatch, postgres_session_factory):
    monkeypatch.setattr(app_config.settings, "payment_webhook_bearer_token", "")
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
async def test_generic_hmac_adapter_success(gh_client):
    ac, sf = gh_client
    order_id, org_id = await _seed_order(sf)
    body = {
        "order_id": order_id,
        "organization_id": org_id,
        "payment_id": "gh-001",
        "status": "paid",
        "amount": 5000,
    }
    raw = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    sig = _ghmac_hex(PW_HOOK_HMAC, raw)
    r = await ac.post(
        "/api/webhooks/payment/providers/generic_hmac",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-Signature-256": sig,
        },
    )
    assert r.status_code == 200
    assert r.json().get("ok") is True


@pytest.mark.asyncio
async def test_generic_hmac_adapter_bad_sig(gh_client):
    ac, sf = gh_client
    order_id, org_id = await _seed_order(sf)
    body = {
        "order_id": order_id,
        "organization_id": org_id,
        "payment_id": "gh-002",
        "status": "paid",
    }
    raw = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    r = await ac.post(
        "/api/webhooks/payment/providers/generic_hmac",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-Signature-256": "bad",
        },
    )
    assert r.status_code == 401
