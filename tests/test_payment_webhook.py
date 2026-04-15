"""Платёжный webhook: Bearer, филиал, идемпотентность по provider:payment_id."""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.core.config as app_config
import app.db.session as db_session_module
from app.db.models import Base, Order, Organization, PaymentEvent, User
from app.db.session import get_db
from app.main import app


@pytest_asyncio.fixture
async def pw_client(monkeypatch):
    monkeypatch.setattr(app_config.settings, "payment_webhook_bearer_token", "hook-secret")

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
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
async def test_payment_webhook_not_configured_returns_503(monkeypatch):
    monkeypatch.setattr(app_config.settings, "payment_webhook_bearer_token", "")
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async def _override_db():
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = _override_db
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
        await engine.dispose()


@pytest.mark.asyncio
async def test_payment_webhook_paid_and_idempotent(pw_client):
    ac, sf = pw_client
    order_id, org_id = await _seed_order(sf)
    headers = {"Authorization": "Bearer hook-secret"}
    body = {
        "order_id": order_id,
        "organization_id": org_id,
        "payment_id": "txn-kaspi-001",
        "status": "paid",
        "amount": 5000,
        "provider": "kaspi",
    }
    r1 = await ac.post("/api/webhooks/payment", json=body, headers=headers)
    assert r1.status_code == 200
    d1 = r1.json()
    assert d1["ok"] is True
    assert d1["duplicate"] is False
    assert d1["prepayment_status"] == "paid"

    r2 = await ac.post("/api/webhooks/payment", json=body, headers=headers)
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
    r = await ac.post(
        "/api/webhooks/payment/providers/generic",
        json={
            "order_id": order_id,
            "organization_id": org_id,
            "payment_id": "ext-pay-slug-99",
            "status": "paid",
            "amount": 5000,
        },
        headers={"Authorization": "Bearer hook-secret"},
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
    r = await ac.post(
        "/api/webhooks/payment",
        json={
            "order_id": order_id,
            "organization_id": org_id + 9999,
            "payment_id": "pay-9999",
            "status": "paid",
        },
        headers={"Authorization": "Bearer hook-secret"},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_payment_webhook_invalid_token(pw_client):
    ac, sf = pw_client
    order_id, org_id = await _seed_order(sf)
    r = await ac.post(
        "/api/webhooks/payment",
        json={
            "order_id": order_id,
            "organization_id": org_id,
            "payment_id": "pay-0001",
            "status": "paid",
        },
        headers={"Authorization": "Bearer wrong"},
    )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_payment_webhook_failed_records_event_only(pw_client):
    ac, sf = pw_client
    order_id, org_id = await _seed_order(sf)
    r = await ac.post(
        "/api/webhooks/payment",
        json={
            "order_id": order_id,
            "organization_id": org_id,
            "payment_id": "pay-fail-01",
            "status": "failed",
        },
        headers={"Authorization": "Bearer hook-secret"},
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
