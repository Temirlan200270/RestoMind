"""E2.3 — биллинг: suspended tenant, rollup billing_usage_daily."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select

from app.core.passwords import hash_password
from app.db.models import AiUsageLog, BillingUsageDaily, Organization, StaffRole, StaffUser, Tenant
from app.services.billing_guard import tenant_billing_blocks_inbound
from app.services.billing_rollup import run_billing_usage_daily_rollup_for_day


@pytest.mark.asyncio
async def test_login_rejects_suspended_tenant(asgi_memory_client) -> None:
    ac, sf = asgi_memory_client
    async with sf() as db:
        t = Tenant(name="SuspendedNet", plan_status="suspended")
        db.add(t)
        await db.flush()
        o = Organization(name="Branch", slug="br", tenant_id=int(t.id))
        db.add(o)
        await db.flush()
        db.add(
            StaffUser(
                organization_id=int(o.id),
                tenant_owner_id=int(t.id),
                email="staff@sus.kz",
                password_hash=hash_password("pw12345678"),
                role=StaffRole.ADMIN.value,
                is_active=True,
                is_superadmin=False,
            ),
        )
        await db.commit()

    r = await ac.post(
        "/api/admin/auth/login",
        json={"email": "staff@sus.kz", "password": "pw12345678"},
    )
    assert r.status_code == 403
    assert "биллинг" in (r.json().get("detail") or "").lower()


@pytest.mark.asyncio
async def test_rollup_billing_usage_daily(db_session) -> None:
    t = Tenant(name="RollT")
    db_session.add(t)
    await db_session.flush()
    o = Organization(name="RollO", slug="ro", tenant_id=t.id)
    db_session.add(o)
    await db_session.flush()
    d = date(2026, 5, 2)
    db_session.add(
        AiUsageLog(
            organization_id=o.id,
            day=d,
            total_tokens=150,
            call_count=3,
        ),
    )
    await db_session.flush()

    n = await run_billing_usage_daily_rollup_for_day(db_session, d)
    assert n == 1

    row = await db_session.scalar(select(BillingUsageDaily).where(BillingUsageDaily.day == d))
    assert row is not None
    assert row.tenant_id == t.id
    assert row.total_tokens == 150
    assert row.ai_calls == 3

    n2 = await run_billing_usage_daily_rollup_for_day(db_session, d)
    assert n2 == 1
    row2 = await db_session.scalar(select(BillingUsageDaily).where(BillingUsageDaily.day == d))
    assert row2 is not None
    assert row2.total_tokens == 150


@pytest.mark.asyncio
async def test_tenant_billing_blocks_inbound(db_session) -> None:
    t = Tenant(name="BlockT", plan_status="suspended")
    db_session.add(t)
    await db_session.flush()
    o = Organization(name="BlockO", slug="bo", tenant_id=t.id)
    db_session.add(o)
    await db_session.flush()
    assert await tenant_billing_blocks_inbound(db_session, o) is True

    t2 = Tenant(name="OkT", plan_status="active")
    db_session.add(t2)
    await db_session.flush()
    o2 = Organization(name="OkO", slug="ok", tenant_id=t2.id)
    db_session.add(o2)
    await db_session.flush()
    assert await tenant_billing_blocks_inbound(db_session, o2) is False
