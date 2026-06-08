"""Платёжный webhook: Bearer, филиал, идемпотентность по provider:payment_id."""

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
from app.db.models import Order, Organization, PaymentEvent, User
from app.db.session import get_db
from app.main import app


PW_HOOK_BEARER = "hook-secret"
PW_HOOK_HMAC = "pw-test-hmac"


def _payment_hmac_hex(secret: str, raw: bytes) -> str:
    return hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


def _payment_body_bytes(body: dict) -> bytes:
    return json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


async def _payment_post(
    ac: AsyncClient,
    path: str,
    body: dict,
    *,
    bearer: str | None = PW_HOOK_BEARER,
    hmac_secret: str = PW_HOOK_HMAC,
    extra_headers: dict[str, str] | None = None,
):
    raw = _payment_body_bytes(body)
    sig = _payment_hmac_hex(hmac_secret, raw)
    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "X-RestoMind-Payment-Signature": sig,
    }
    if bearer is not None:
        headers["Authorization"] = f"Bearer {bearer}"
    if extra_headers:
        headers.update(extra_headers)
    return await ac.post(path, content=raw, headers=headers)


def _install_db_override(
    monkeypatch,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
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


@pytest_asyncio.fixture
async def pw_client(monkeypatch, postgres_session_factory):
    monkeypatch.setattr(app_config.settings, "payment_webhook_bearer_token", PW_HOOK_BEARER)
    monkeypatch.setattr(app_config.settings, "payment_webhook_hmac_secret", PW_HOOK_HMAC)

    async def _noop_enqueue_job(*_a, **_kw):
        return None

    monkeypatch.setattr("app.services.task_queue.enqueue_job", _noop_enqueue_job)

    session_factory = postgres_session_factory
    _install_db_override(monkeypatch, session_factory)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac, session_factory

    app.dependency_overrides.clear()


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


@pytest.mark.asyncio
async def test_payment_webhook_not_configured_returns_503(monkeypatch, postgres_session_factory):
    monkeypatch.setattr(app_config.settings, "payment_webhook_bearer_token", "")
    _install_db_override(monkeypatch, postgres_session_factory)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.post(
                "/api/webhooks/payment",
                json={
                    "order_id": 1,
                    "organization_id": 1,
                    "payment_id": "x1234",
                    "status": "paid",
                },
                headers={"Authorization": "Bearer x"},
            )
        assert r.status_code == 503
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_payment_webhook_paid_and_idempotent(pw_client):
    ac, sf = pw_client
    order_id, org_id = await _seed_order(sf)
    body = {
        "order_id": order_id,
        "organization_id": org_id,
        "payment_id": "txn-kaspi-001",
        "status": "paid",
        "amount": 5000,
        "provider": "kaspi",
    }
    r1 = await _payment_post(ac, "/api/webhooks/payment", body)
    assert r1.status_code == 200
    d1 = r1.json()
    assert d1["ok"] is True
    assert d1["duplicate"] is False
    assert d1["prepayment_status"] == "paid"

    r2 = await _payment_post(ac, "/api/webhooks/payment", body)
    assert r2.status_code == 200
    d2 = r2.json()
    assert d2["duplicate"] is True

    async with sf() as db:
        n = int(
            await db.scalar(
                select(func.count()).select_from(PaymentEvent).where(PaymentEvent.order_id == order_id),
            )
            or 0,
        )
        order_row = await db.get(Order, order_id)
        assert order_row is not None
        assert order_row.payment_provider == "kaspi"
        assert order_row.external_payment_id == "txn-kaspi-001"
        assert float(order_row.payment_amount_captured or 0) == 5000.0
    assert n == 1


@pytest.mark.asyncio
async def test_payment_webhook_providers_generic_path(pw_client):
    ac, sf = pw_client
    order_id, org_id = await _seed_order(sf)
    r = await _payment_post(
        ac,
        "/api/webhooks/payment/providers/generic",
        {
            "order_id": order_id,
            "organization_id": org_id,
            "payment_id": "ext-pay-slug-99",
            "status": "paid",
            "amount": 5000,
        },
    )
    assert r.status_code == 200
    assert r.json().get("duplicate") is False
    async with sf() as db:
        order_row = await db.get(Order, order_id)
        assert order_row is not None
        assert order_row.prepayment_status == "paid"
        assert order_row.external_payment_id == "ext-pay-slug-99"


@pytest.mark.asyncio
async def test_payment_webhook_wrong_org_forbidden(pw_client):
    ac, sf = pw_client
    order_id, org_id = await _seed_order(sf)
    r = await _payment_post(
        ac,
        "/api/webhooks/payment",
        {
            "order_id": order_id,
            "organization_id": org_id + 9999,
            "payment_id": "pay-9999",
            "status": "paid",
        },
    )
    assert r.status_code == 403
    async with sf() as db:
        ev = (
            await db.execute(
                select(PaymentEvent).where(
                    PaymentEvent.order_id == order_id,
                    PaymentEvent.event_type == "webhook_failed",
                    PaymentEvent.note.like("%org_mismatch%"),
                ),
            )
        ).scalars().first()
        assert ev is not None


@pytest.mark.asyncio
async def test_payment_webhook_invalid_token(pw_client):
    ac, sf = pw_client
    order_id, org_id = await _seed_order(sf)
    body = {
        "order_id": order_id,
        "organization_id": org_id,
        "payment_id": "pay-0001",
        "status": "paid",
    }
    raw = _payment_body_bytes(body)
    sig = _payment_hmac_hex(PW_HOOK_HMAC, raw)
    r = await ac.post(
        "/api/webhooks/payment",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer wrong",
            "X-RestoMind-Payment-Signature": sig,
        },
    )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_payment_webhook_failed_records_event_only(pw_client):
    ac, sf = pw_client
    order_id, org_id = await _seed_order(sf)
    r = await _payment_post(
        ac,
        "/api/webhooks/payment",
        {
            "order_id": order_id,
            "organization_id": org_id,
            "payment_id": "pay-fail-01",
            "status": "failed",
        },
    )
    assert r.status_code == 200
    assert r.json()["duplicate"] is False

    async with sf() as db:
        order = await db.get(Order, order_id)
        assert order is not None
        assert order.prepayment_status == "pending"
        ev = (
            await db.execute(
                select(PaymentEvent).where(
                    PaymentEvent.order_id == order_id,
                    PaymentEvent.event_type == "webhook_failed",
                ),
            )
        ).scalars().first()
        assert ev is not None


@pytest.mark.asyncio
async def test_payment_webhook_hmac_only_success(monkeypatch, postgres_session_factory):
    monkeypatch.setattr(app_config.settings, "payment_webhook_bearer_token", "")
    monkeypatch.setattr(app_config.settings, "payment_webhook_hmac_secret", "hm-shared")

    async def _noop_enqueue_job(*_a, **_kw):
        return None

    monkeypatch.setattr("app.services.task_queue.enqueue_job", _noop_enqueue_job)

    session_factory = postgres_session_factory
    _install_db_override(monkeypatch, session_factory)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
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
                    total_price=100,
                    prepayment_status="pending",
                )
                db.add(order)
                await db.flush()
                oid, gid = order.id, org.id
                await db.commit()

            payload = {
                "order_id": oid,
                "organization_id": gid,
                "payment_id": "hmac-pay-01",
                "status": "paid",
                "amount": 100,
            }
            raw = _payment_body_bytes(payload)
            sig = _payment_hmac_hex("hm-shared", raw)
            r = await ac.post(
                "/api/webhooks/payment",
                content=raw,
                headers={
                    "Content-Type": "application/json",
                    "X-RestoMind-Payment-Signature": sig,
                },
            )
            assert r.status_code == 200
            assert r.json().get("prepayment_status") == "paid"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_payment_webhook_hmac_invalid_when_configured(monkeypatch, postgres_session_factory):
    monkeypatch.setattr(app_config.settings, "payment_webhook_bearer_token", "")
    monkeypatch.setattr(app_config.settings, "payment_webhook_hmac_secret", "hm-shared")

    _install_db_override(monkeypatch, postgres_session_factory)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            payload = {"order_id": 1, "organization_id": 1, "payment_id": "x", "status": "paid"}
            raw = _payment_body_bytes(payload)
            r = await ac.post(
                "/api/webhooks/payment",
                content=raw,
                headers={
                    "Content-Type": "application/json",
                    "X-RestoMind-Payment-Signature": "deadbeef",
                },
            )
            assert r.status_code == 401
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_payment_webhook_bearer_and_hmac_both_required(pw_client, monkeypatch):
    monkeypatch.setattr(app_config.settings, "payment_webhook_hmac_secret", "both-secret")
    ac, sf = pw_client
    order_id, org_id = await _seed_order(sf)

    body = {
        "order_id": order_id,
        "organization_id": org_id,
        "payment_id": "both-pay-01",
        "status": "paid",
        "amount": 5000,
    }
    raw = _payment_body_bytes(body)
    sig = _payment_hmac_hex("both-secret", raw)
    r_bad = await ac.post(
        "/api/webhooks/payment",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer hook-secret",
            "X-RestoMind-Payment-Signature": "bad",
        },
    )
    assert r_bad.status_code == 401

    r_ok = await ac.post(
        "/api/webhooks/payment",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer hook-secret",
            "X-RestoMind-Payment-Signature": sig,
        },
    )
    assert r_ok.status_code == 200
    assert r_ok.json().get("prepayment_status") == "paid"


@pytest.mark.asyncio
async def test_payment_webhook_hmac_required_when_prod_like(monkeypatch, postgres_session_factory):
    monkeypatch.setattr(app_config.settings, "payment_webhook_bearer_token", "only-bearer")
    monkeypatch.setattr(app_config.settings, "payment_webhook_hmac_secret", "")
    monkeypatch.setattr(app_config.settings, "app_environment", "production")

    _install_db_override(monkeypatch, postgres_session_factory)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            body = {"order_id": 1, "organization_id": 1, "payment_id": "x1234", "status": "paid"}
            raw = _payment_body_bytes(body)
            r = await ac.post(
                "/api/webhooks/payment",
                content=raw,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": "Bearer only-bearer",
                },
            )
            assert r.status_code == 503
    finally:
        app.dependency_overrides.clear()
