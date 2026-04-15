"""Уведомление клиента после оплаты: chat_logs + идемпотентность."""

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.db.session as db_session_module
from app.db.models import Base, ChatLog, Order, Organization, User
from app.services.payment_notify import run_payment_received_customer_notify


@pytest_asyncio.fixture
async def notify_session_factory(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(db_session_module, "async_session_factory", sf)
    yield sf
    await engine.dispose()


@pytest.mark.asyncio
async def test_payment_notify_system_chat_log_idempotent(notify_session_factory) -> None:
    sf = notify_session_factory
    async with sf() as db:
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
            prepayment_status="paid",
        )
        db.add(order)
        await db.flush()
        oid = order.id
        await db.commit()

    await run_payment_received_customer_notify(oid)
    await run_payment_received_customer_notify(oid)

    async with sf() as db:
        n = int(
            await db.scalar(select(func.count()).select_from(ChatLog).where(ChatLog.role == "system"))
            or 0,
        )
        assert n == 1
        row = (await db.execute(select(ChatLog).where(ChatLog.role == "system"))).scalars().first()
        assert row is not None
        assert isinstance(row.meta_json, dict)
        assert row.meta_json.get("kind") == "payment_confirmed"
        assert int(row.meta_json.get("order_id") or 0) == oid
