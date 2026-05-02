"""Авто-отправка в iiko после оплаты: legacy-заказы без Order.organization_id."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.db.session as db_session_module
from app.db.models import Base, Order, OrderStatus, Organization, User
from app.services.payment_autoprint_iiko import run_auto_send_to_iiko_after_payment


def _memory_sqlite_engine():
    return create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


@pytest_asyncio.fixture
async def autoprint_db(monkeypatch):
    engine = _memory_sqlite_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(db_session_module, "async_session_factory", factory)
    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_auto_iiko_legacy_order_without_organization_id(autoprint_db: async_sessionmaker[AsyncSession]):
    """Заказ с organization_id=NULL, но user в филиале — claim и этап SENDING_TO_IIKO проходят."""
    async with autoprint_db() as db:
        org = Organization(
            name="O",
            slug="o",
            auto_send_to_iiko_after_payment=True,
        )
        db.add(org)
        await db.flush()
        oid = int(org.id)
        user = User(organization_id=oid, phone="+77001112233")
        db.add(user)
        await db.flush()
        order = Order(
            organization_id=None,
            user_id=int(user.id),
            status=OrderStatus.CONFIRMED.value,
            prepayment_status="paid",
            total_price=5000.0,
            items_json={"items": []},
            row_version=0,
        )
        db.add(order)
        await db.commit()
        oid_order = int(order.id)

    send_mock = AsyncMock(return_value=(True, None, {}))
    pub_mock = AsyncMock()
    clear_mock = AsyncMock()

    with (
        patch("app.api.webhooks._send_order_to_iiko", send_mock),
        patch("app.services.payment_autoprint_iiko.publish_event", pub_mock),
        patch("app.services.payment_autoprint_iiko.clear_pending_order", clear_mock),
    ):
        await run_auto_send_to_iiko_after_payment(oid_order)

    send_mock.assert_awaited_once()
    assert send_mock.await_args is not None
    assert int(send_mock.await_args.kwargs["restaurant_organization_id"]) == oid

    async with autoprint_db() as db2:
        reloaded = await db2.get(Order, oid_order)
        assert reloaded is not None
        assert reloaded.status == OrderStatus.SENT_TO_IIKO.value
