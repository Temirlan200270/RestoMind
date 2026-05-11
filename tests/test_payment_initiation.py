"""Payment Initiation Service: initiate_payment, NoPaymentConfigError, provider error path."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models import (
    Base,
    Order,
    Organization,
    OrganizationPaymentConfig,
    PaymentTxStatus,
    User,
)
from app.services.payment_initiation import (
    NoPaymentConfigError,
    PaymentInitiationError,
    initiate_payment,
    mark_payment_waived,
)
from app.services.payment_providers.base import InitiatedPayment


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


async def _seed(db: AsyncSession, *, with_payment_config: bool = True, provider: str = "freedom_pay"):
    org = Organization(name="Resto", slug="resto", currency="KZT")
    db.add(org)
    await db.flush()

    user = User(organization_id=org.id, phone="+77001234567")
    db.add(user)
    await db.flush()

    order = Order(
        organization_id=org.id,
        user_id=user.id,
        status="confirmed",
        total_price=5000,
        prepayment_status="not_required",
    )
    db.add(order)
    await db.flush()

    if with_payment_config:
        cfg = OrganizationPaymentConfig(
            organization_id=org.id,
            provider=provider,
            is_enabled=True,
            is_primary=True,
            merchant_id="MERCH123",
            encrypted_api_key=None,
            encrypted_secret_key=None,
            extra_config_json={
                "callback_url": "https://example.com/cb",
                "success_url": "https://example.com/ok",
            },
        )
        db.add(cfg)
        await db.flush()

    return org, order


_FAKE_URL = "https://pay.example.com/link/abc123"
_FAKE_PROVIDER_ID = "FP_TXN_42"


def _mock_initiated_payment() -> InitiatedPayment:
    return InitiatedPayment(
        payment_url=_FAKE_URL,
        provider_payment_id=_FAKE_PROVIDER_ID,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        raw={"pg_payment_id": _FAKE_PROVIDER_ID},
    )


@pytest.mark.asyncio
async def test_initiate_payment_success(db):
    org, order = await _seed(db)
    mock_initiator = AsyncMock()
    mock_initiator.create_payment = AsyncMock(return_value=_mock_initiated_payment())

    with patch("app.services.payment_initiation._decrypt_credentials", return_value={
        "api_key": "key", "secret_key": "sec", "public_key": "",
        "merchant_id": "MERCH123", "environment": "production",
        "callback_url": "https://example.com/cb",
        "success_url": "https://example.com/ok",
    }), patch("app.services.payment_providers.get_initiator", return_value=mock_initiator):
        tx = await initiate_payment(db, order=order, org=org)

    assert tx.status == PaymentTxStatus.PENDING.value
    assert tx.payment_url == _FAKE_URL
    assert tx.provider_payment_id == _FAKE_PROVIDER_ID
    assert tx.order_id == order.id
    assert tx.organization_id == org.id
    assert order.payment_link_url == _FAKE_URL
    assert order.prepayment_status == "pending"


@pytest.mark.asyncio
async def test_initiate_payment_no_config_raises(db):
    org, order = await _seed(db, with_payment_config=False)
    with pytest.raises(NoPaymentConfigError):
        await initiate_payment(db, order=order, org=org)


@pytest.mark.asyncio
async def test_initiate_payment_no_initiator_raises(db):
    org, order = await _seed(db, provider="unknown_provider")
    with patch("app.services.payment_initiation._decrypt_credentials", return_value={
        "api_key": "", "secret_key": "", "public_key": "",
        "merchant_id": "", "environment": "production",
        "callback_url": "", "success_url": "",
    }), patch("app.services.payment_providers.get_initiator", return_value=None):
        with pytest.raises(PaymentInitiationError, match="не поддерживает инициацию"):
            await initiate_payment(db, order=order, org=org)


@pytest.mark.asyncio
async def test_initiate_payment_provider_error_saves_failed_tx(db):
    org, order = await _seed(db)
    mock_initiator = AsyncMock()
    mock_initiator.create_payment = AsyncMock(side_effect=RuntimeError("gateway timeout"))

    with patch("app.services.payment_initiation._decrypt_credentials", return_value={
        "api_key": "k", "secret_key": "s", "public_key": "",
        "merchant_id": "M", "environment": "production",
        "callback_url": "", "success_url": "",
    }), patch("app.services.payment_providers.get_initiator", return_value=mock_initiator):
        with pytest.raises(PaymentInitiationError):
            await initiate_payment(db, order=order, org=org)

    # TX must be saved in failed state
    from sqlalchemy import select
    from app.db.models import PaymentTransaction
    txs = list(await db.scalars(select(PaymentTransaction).where(PaymentTransaction.order_id == order.id)))
    assert len(txs) == 1
    assert txs[0].status == PaymentTxStatus.FAILED.value
    assert "gateway timeout" in (txs[0].failure_reason or "")


@pytest.mark.asyncio
async def test_mark_payment_waived(db):
    org, order = await _seed(db, with_payment_config=False)
    tx = await mark_payment_waived(db, order=order, actor="operator")
    assert tx is not None
    assert tx.status == PaymentTxStatus.WAIVED.value
    assert order.prepayment_status == "waived"
    assert tx.idempotency_key.startswith("waived:")


@pytest.mark.asyncio
async def test_initiate_sets_currency_from_org(db):
    org, order = await _seed(db)
    org.currency = "USD"
    await db.flush()

    mock_initiator = AsyncMock()
    mock_initiator.create_payment = AsyncMock(return_value=_mock_initiated_payment())

    with patch("app.services.payment_initiation._decrypt_credentials", return_value={
        "api_key": "", "secret_key": "", "public_key": "",
        "merchant_id": "M", "environment": "production",
        "callback_url": "", "success_url": "",
    }), patch("app.services.payment_providers.get_initiator", return_value=mock_initiator):
        tx = await initiate_payment(db, order=order, org=org)

    assert tx.currency == "USD"
