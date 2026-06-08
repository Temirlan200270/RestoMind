"""Идемпотентность: второй webhook paid не должен снова ставить auto_send в очередь."""

import hashlib
import hmac
import json
from unittest.mock import AsyncMock

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

PW_HOOK_BEARER = "hook-secret"
PW_HOOK_HMAC = "pw-test-hmac"


def _payment_hmac_hex(secret: str, raw: bytes) -> str:
    return hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


def _payment_body_bytes(body: dict) -> bytes:
    return json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


async def _seed_order_with_auto_iiko(session_factory: async_sessionmaker[AsyncSession]) -> tuple[int, int]:
    async with session_factory() as db:
        org = Organization(name="O", slug="o", auto_send_to_iiko_after_payment=True)
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
async def autoprint_client(monkeypatch, postgres_session_factory):
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
async def test_duplicate_webhook_does_not_enqueue_autoprint_twice(autoprint_client, monkeypatch):
    ac, sf = autoprint_client
    order_id, org_id = await _seed_order_with_auto_iiko(sf)

    monkeypatch.setattr(
        "app.services.payment_notify.run_payment_received_customer_notify",
        AsyncMock(return_value=None),
    )

    autoprint_calls: list[int] = []

    def _fake_autoprint(oid: int) -> None:
        autoprint_calls.append(oid)

    monkeypatch.setattr(
        "app.api.payment_webhook.run_auto_send_to_iiko_after_payment",
        _fake_autoprint,
    )

    body = {
        "order_id": order_id,
        "organization_id": org_id,
        "payment_id": "txn-auto-idem",
        "status": "paid",
        "amount": 5000,
    }
    raw = _payment_body_bytes(body)
    sig = _payment_hmac_hex(PW_HOOK_HMAC, raw)
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {PW_HOOK_BEARER}",
        "X-RestoMind-Payment-Signature": sig,
    }

    r1 = await ac.post("/api/webhooks/payment", content=raw, headers=headers)
    assert r1.status_code == 200
    assert r1.json().get("duplicate") is False

    r2 = await ac.post("/api/webhooks/payment", content=raw, headers=headers)
    assert r2.status_code == 200
    assert r2.json().get("duplicate") is True

    assert autoprint_calls.count(order_id) == 1
