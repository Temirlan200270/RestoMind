"""Payment expiry job: expire_stale_payment_transactions."""

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models import Base, Order, Organization, PaymentTransaction, PaymentTxStatus, User
from app.services.payment_expiry import expire_stale_payment_transactions


def _engine():
    return create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


@pytest_asyncio.fixture
async def db():
    engine = _engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _seed(db: AsyncSession) -> tuple[int, int, int]:
    """Returns (org_id, order_id, user_id)."""
    org = Organization(name="TestOrg", slug="testorg")
    db.add(org)
    await db.flush()
    user = User(organization_id=org.id, phone="+77000000001")
    db.add(user)
    await db.flush()
    order = Order(
        organization_id=org.id,
        user_id=user.id,
        status="confirmed",
        total_price=3000,
        prepayment_status="pending",
    )
    db.add(order)
    await db.flush()
    return org.id, order.id, user.id


def _tx(org_id: int, order_id: int, *, status: str, expires_at: datetime | None) -> PaymentTransaction:
    from uuid import uuid4
    return PaymentTransaction(
        organization_id=org_id,
        order_id=order_id,
        provider="kaspi",
        status=status,
        amount=3000.0,
        currency="KZT",
        idempotency_key=f"pay:kaspi:{order_id}:{uuid4().hex[:8]}",
        expires_at=expires_at,
    )


@pytest.mark.asyncio
async def test_expires_overdue_pending(db, monkeypatch):
    monkeypatch.setattr(
        "app.services.payment_expiry.emit_system_event",
        lambda *a, **kw: _noop_coro(),
    )
    org_id, order_id, _ = await _seed(db)
    past = datetime.now(timezone.utc) - timedelta(minutes=5)
    tx = _tx(org_id, order_id, status=PaymentTxStatus.PENDING.value, expires_at=past)
    db.add(tx)
    await db.flush()

    result = await expire_stale_payment_transactions(db)
    assert len(result) == 1
    assert result[0].id == tx.id


@pytest.mark.asyncio
async def test_does_not_expire_future(db, monkeypatch):
    monkeypatch.setattr(
        "app.services.payment_expiry.emit_system_event",
        lambda *a, **kw: _noop_coro(),
    )
    org_id, order_id, _ = await _seed(db)
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    tx = _tx(org_id, order_id, status=PaymentTxStatus.PENDING.value, expires_at=future)
    db.add(tx)
    await db.flush()

    result = await expire_stale_payment_transactions(db)
    assert result == []


@pytest.mark.asyncio
async def test_does_not_expire_already_paid(db, monkeypatch):
    monkeypatch.setattr(
        "app.services.payment_expiry.emit_system_event",
        lambda *a, **kw: _noop_coro(),
    )
    org_id, order_id, _ = await _seed(db)
    past = datetime.now(timezone.utc) - timedelta(hours=2)
    tx = _tx(org_id, order_id, status=PaymentTxStatus.PAID.value, expires_at=past)
    db.add(tx)
    await db.flush()

    result = await expire_stale_payment_transactions(db)
    assert result == []


@pytest.mark.asyncio
async def test_does_not_expire_no_expires_at(db, monkeypatch):
    monkeypatch.setattr(
        "app.services.payment_expiry.emit_system_event",
        lambda *a, **kw: _noop_coro(),
    )
    org_id, order_id, _ = await _seed(db)
    tx = _tx(org_id, order_id, status=PaymentTxStatus.PENDING.value, expires_at=None)
    db.add(tx)
    await db.flush()

    result = await expire_stale_payment_transactions(db)
    assert result == []


@pytest.mark.asyncio
async def test_returns_empty_when_nothing_pending(db, monkeypatch):
    monkeypatch.setattr(
        "app.services.payment_expiry.emit_system_event",
        lambda *a, **kw: _noop_coro(),
    )
    result = await expire_stale_payment_transactions(db)
    assert result == []


@pytest.mark.asyncio
async def test_expires_multiple(db, monkeypatch):
    monkeypatch.setattr(
        "app.services.payment_expiry.emit_system_event",
        lambda *a, **kw: _noop_coro(),
    )
    org_id, order_id, _ = await _seed(db)
    past = datetime.now(timezone.utc) - timedelta(minutes=30)

    tx1 = _tx(org_id, order_id, status=PaymentTxStatus.PENDING.value, expires_at=past)
    tx2 = _tx(org_id, order_id, status=PaymentTxStatus.PENDING.value, expires_at=past)
    db.add(tx1)
    db.add(tx2)
    await db.flush()

    result = await expire_stale_payment_transactions(db)
    assert len(result) == 2


async def _noop_coro(*args, **kwargs):
    pass
