"""Postgres RLS isolation — requires policies on test DB."""

from __future__ import annotations

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Order, OrderStatus, Organization, User
from app.db.tenant_rls import (
    apply_tenant_rls,
    reset_tenant_rls_bypass,
    reset_tenant_rls_context,
    set_tenant_rls_bypass,
    set_tenant_rls_context,
)


RLS_POLICY_SQL = """
CREATE POLICY tenant_isolation_{table} ON {table}
FOR ALL
USING (
    coalesce(current_setting('app.bypass_rls', true), '') = 'true'
    OR organization_id = NULLIF(current_setting('app.organization_id', true), '')::integer
)
WITH CHECK (
    coalesce(current_setting('app.bypass_rls', true), '') = 'true'
    OR organization_id = NULLIF(current_setting('app.organization_id', true), '')::integer
);
"""

RLS_TABLES = ("orders", "users")


async def _ensure_rls_policies(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as db:
        bind = db.get_bind()
        if bind.dialect.name != "postgresql":
            pytest.skip("RLS tests require PostgreSQL")
        for table in RLS_TABLES:
            await db.execute(text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
            await db.execute(text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
            await db.execute(text(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}"))
            await db.execute(text(RLS_POLICY_SQL.format(table=table)))
        await db.commit()


@pytest.mark.asyncio
async def test_rls_hides_foreign_org_orders(postgres_session_factory):
    await _ensure_rls_policies(postgres_session_factory)

    async with postgres_session_factory() as db:
        db.add_all(
            [
                Organization(id=101, name="RLS A", slug="rls-a"),
                Organization(id=102, name="RLS B", slug="rls-b"),
                User(id=201, organization_id=101, phone="+77001110001"),
                User(id=202, organization_id=102, phone="+77001110002"),
                Order(
                    organization_id=101,
                    user_id=201,
                    status=OrderStatus.COMPLETED.value,
                    total_price=1000,
                    items_json={"items": []},
                ),
                Order(
                    organization_id=102,
                    user_id=202,
                    status=OrderStatus.COMPLETED.value,
                    total_price=9000,
                    items_json={"items": []},
                ),
            ],
        )
        await db.commit()

    bypass_token = set_tenant_rls_bypass(False)
    org_token = set_tenant_rls_context(101)
    try:
        async with postgres_session_factory() as db:
            is_super = bool(
                await db.scalar(
                    text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user"),
                ),
            )
            await apply_tenant_rls(db)
            org_setting = await db.scalar(text("SELECT current_setting('app.organization_id', true)"))
            assert org_setting == "101"

            if is_super:
                pytest.skip("current DB role is superuser — Postgres bypasses RLS row filters")

            rows = (await db.execute(select(Order))).scalars().all()
            assert len(rows) == 1
            assert rows[0].organization_id == 101
    finally:
        reset_tenant_rls_bypass(bypass_token)
        reset_tenant_rls_context(org_token)
