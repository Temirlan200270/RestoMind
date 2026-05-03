"""CloudPayments: Content-HMAC base64(HMAC-SHA256(body))."""

import base64
import hashlib
import hmac
import json

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.core.config as app_config
import app.db.session as db_session_module
from app.db.models import Base, Order, Organization, User
from app.db.session import get_db
from app.main import app

CP_SECRET = "cloudpayments-api-secret"


def _memory_sqlite_engine():
    return create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
)


def _cp_hmac_b64(secret: str, raw: bytes) -> str:
    digest = hmac.new(secret.encode(), raw, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


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
async def cp_client(monkeypatch):
    monkeypatch.setattr(app_config.settings, "payment_webhook_bearer_token", "")
    monkeypatch.setattr(app_config.settings, "payment_webhook_hmac_secret", "")
    monkeypatch.setattr(app_config.settings, "cloudpayments_api_secret", CP_SECRET)

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
async def test_cloudpayments_webhook_success(cp_client):
    ac, sf = cp_client
    order_id, org_id = await _seed_order(sf)
    data_inner = json.dumps({"order_id": order_id, "organization_id": org_id})
    payload = {
        "TransactionId": 999001,
        "Amount": 5000,
        "Currency": "KZT",
        "Status": "Completed",
        "Data": data_inner,
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    hmac_b64 = _cp_hmac_b64(CP_SECRET, raw)
    r = await ac.post(
        "/api/webhooks/payment/providers/cloudpayments",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "Content-HMAC": hmac_b64,
        },
    )
    assert r.status_code == 200
    assert r.json().get("ok") is True


@pytest.mark.asyncio
async def test_cloudpayments_webhook_bad_hmac(cp_client):
    ac, sf = cp_client
    order_id, org_id = await _seed_order(sf)
    data_inner = json.dumps({"order_id": order_id, "organization_id": org_id})
    payload = {
        "TransactionId": 999002,
        "Amount": 5000,
        "Status": "Completed",
        "Data": data_inner,
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    r = await ac.post(
        "/api/webhooks/payment/providers/cloudpayments",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "Content-HMAC": "wrong",
        },
    )
    assert r.status_code == 401
