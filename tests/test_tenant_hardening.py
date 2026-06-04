"""Extended multitenant isolation + tenant backfill tests."""

from __future__ import annotations

from datetime import date, datetime, timedelta, time, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.db.models import (
    AIContextSnapshot,
    Base,
    Booking,
    ChatLog,
    Organization,
    SystemEvent,
    User,
)
from app.services.tenant_backfill import backfill_null_organization_ids, run_tenant_scope_backfill
from app.services.tenant_scope import legacy_null_org_visible, orders_tenant_clause


def _make_engine():
    return create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


@pytest_asyncio.fixture
async def iso_db():
    engine = _make_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False,
    )
    yield session_factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_legacy_null_only_for_default_org():
    default_id = int(settings.default_organization_id)
    assert legacy_null_org_visible(default_id) is True
    assert legacy_null_org_visible(default_id + 999) is False


@pytest.mark.asyncio
async def test_bookings_isolated_by_org(iso_db):
    org_a_id = org_b_id = 0
    async with iso_db() as db:
        org_a = Organization(name="A", slug="a", is_active=True)
        org_b = Organization(name="B", slug="b", is_active=True)
        db.add_all([org_a, org_b])
        await db.commit()
        await db.refresh(org_a)
        await db.refresh(org_b)
        org_a_id, org_b_id = org_a.id, org_b.id
        ua = User(phone="+77001110001", organization_id=org_a_id)
        ub = User(phone="+77001110002", organization_id=org_b_id)
        db.add_all([ua, ub])
        await db.commit()
        await db.refresh(ua)
        await db.refresh(ub)
        db.add(Booking(
            user_id=ua.id,
            organization_id=org_a_id,
            status="draft",
            guests=2,
            booking_date=date(2026, 5, 25),
            booking_time=time(19, 0),
        ))
        db.add(Booking(
            user_id=ub.id,
            organization_id=org_b_id,
            status="draft",
            guests=2,
            booking_date=date(2026, 5, 25),
            booking_time=time(20, 0),
        ))
        await db.commit()

    async with iso_db() as db:
        from app.api.admin.deps import _bookings_tenant_clause

        rows_a = (
            await db.execute(select(Booking.id).where(_bookings_tenant_clause(org_a_id)))
        ).scalars().all()
        rows_b = (
            await db.execute(select(Booking.id).where(_bookings_tenant_clause(org_b_id)))
        ).scalars().all()
        assert len(rows_a) == 1
        assert len(rows_b) == 1
        assert rows_a != rows_b


@pytest.mark.asyncio
async def test_system_events_isolated_by_org(iso_db):
    async with iso_db() as db:
        org_a = Organization(name="A", slug="a2", is_active=True)
        org_b = Organization(name="B", slug="b2", is_active=True)
        db.add_all([org_a, org_b])
        await db.commit()
        await db.refresh(org_a)
        await db.refresh(org_b)
        db.add(SystemEvent(organization_id=org_a.id, event_type="order.created", source="test"))
        db.add(SystemEvent(organization_id=org_b.id, event_type="order.created", source="test"))
        await db.commit()

    async with iso_db() as db:
        a = (await db.execute(
            select(SystemEvent).where(SystemEvent.organization_id == org_a.id),
        )).scalars().all()
        b = (await db.execute(
            select(SystemEvent).where(SystemEvent.organization_id == org_b.id),
        )).scalars().all()
        assert len(a) == 1
        assert len(b) == 1


@pytest.mark.asyncio
async def test_backfill_null_order_organization_id(iso_db):
    async with iso_db() as db:
        org = Organization(name="Org", slug="org-bf", is_active=True)
        db.add(org)
        await db.commit()
        await db.refresh(org)
        user = User(phone="+77009998877", organization_id=org.id)
        db.add(user)
        await db.commit()
        await db.refresh(user)
        from app.db.models import Order

        db.add(Order(user_id=user.id, organization_id=None, status="draft", total_price=100))
        await db.commit()

    async with iso_db() as db:
        await backfill_null_organization_ids(db)
        await db.commit()
        from app.db.models import Order

        row = (await db.execute(select(Order))).scalar_one()
        assert row.organization_id == org.id


@pytest.mark.asyncio
async def test_ai_context_snapshot_isolated(iso_db):
    async with iso_db() as db:
        org_a = Organization(name="A", slug="a3", is_active=True)
        org_b = Organization(name="B", slug="b3", is_active=True)
        db.add_all([org_a, org_b])
        await db.commit()
        await db.refresh(org_a)
        await db.refresh(org_b)
        db.add(AIContextSnapshot(id="s1", organization_id=org_a.id, phone="+1"))
        db.add(AIContextSnapshot(id="s2", organization_id=org_b.id, phone="+2"))
        await db.commit()

    async with iso_db() as db:
        a = (await db.execute(
            select(AIContextSnapshot).where(AIContextSnapshot.organization_id == org_a.id),
        )).scalars().all()
        assert len(a) == 1
        assert a[0].id == "s1"


@pytest.mark.asyncio
async def test_chat_log_retention_is_scoped_by_org(iso_db, monkeypatch):
    from app.services.chat_log_retention import purge_old_chat_logs

    monkeypatch.setattr(settings, "chat_log_retention_days", 1)
    old = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=2)
    async with iso_db() as db:
        org_a = Organization(name="A", slug="ret-a", is_active=True)
        org_b = Organization(name="B", slug="ret-b", is_active=True)
        db.add_all([org_a, org_b])
        await db.commit()
        await db.refresh(org_a)
        await db.refresh(org_b)
        user_a = User(phone="+77005550101", organization_id=org_a.id)
        user_b = User(phone="+77005550102", organization_id=org_b.id)
        db.add_all([user_a, user_b])
        await db.commit()
        await db.refresh(user_a)
        await db.refresh(user_b)
        db.add_all(
            [
                ChatLog(
                    organization_id=org_a.id,
                    user_id=user_a.id,
                    role="user",
                    content="old a",
                    created_at=old,
                ),
                ChatLog(
                    organization_id=org_b.id,
                    user_id=user_b.id,
                    role="user",
                    content="old b",
                    created_at=old,
                ),
            ],
        )
        await db.commit()

    async with iso_db() as db:
        deleted = await purge_old_chat_logs(db, organization_id=org_a.id)
        await db.commit()
        assert deleted == 1
        remaining = (await db.execute(select(ChatLog))).scalars().all()
        assert len(remaining) == 1
        assert remaining[0].organization_id == org_b.id


@pytest.mark.asyncio
async def test_build_llm_prompt_bundle_from_read_ctx():
    from app.services.context_engine import AIReadContext, build_llm_prompt_bundle

    ctx = AIReadContext(
        menu_items=[],
        user=None,
        org=None,
        kb_context="kb",
        draft_row=None,
        customer_ctx="cust",
        user_preferences={},
        tenant=None,
    )
    bundle = await build_llm_prompt_bundle(ctx, organization_id=1, message_text="привет")
    assert bundle.kb_context == "kb"
    assert bundle.customer_ctx == "cust"
