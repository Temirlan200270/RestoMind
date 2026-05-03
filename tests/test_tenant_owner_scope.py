"""tenant_owner и изоляция данных по активному филиалу (E2.1)."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.passwords import hash_password
from app.db.models import MenuItem, Organization, StaffRole, StaffUser, Tenant
from app.services.tenant_scope import available_organizations_for_admin_session


@pytest.mark.asyncio
async def test_available_orgs_regular_staff_single_branch(db_session):
    org = Organization(name="Solo", slug="solo")
    db_session.add(org)
    await db_session.flush()
    staff = StaffUser(
        organization_id=int(org.id),
        tenant_owner_id=None,
        email="solo@solo.kz",
        password_hash=hash_password("x"),
        role=StaffRole.ADMIN.value,
        is_active=True,
        is_superadmin=False,
    )
    db_session.add(staff)
    await db_session.flush()

    av = await available_organizations_for_admin_session(
        db_session,
        staff=staff,
        is_superadmin=False,
        is_demo=False,
        session_organization_id=int(org.id),
    )
    assert len(av) == 1
    assert av[0]["id"] == int(org.id)


@pytest.mark.asyncio
async def test_menu_respects_active_organization_after_select_org(asgi_memory_client):
    ac, sf = asgi_memory_client
    async with sf() as db:
        t1 = Tenant(name="Chain")
        db.add(t1)
        await db.flush()
        o_a = Organization(name="O_A", slug="oa", tenant_id=int(t1.id))
        o_b = Organization(name="O_B", slug="ob", tenant_id=int(t1.id))
        db.add_all([o_a, o_b])
        await db.flush()
        db.add_all(
            [
                MenuItem(
                    organization_id=int(o_a.id),
                    name="OnlyA",
                    category="T",
                    price=100,
                    is_available=True,
                    iiko_id="uuid-a",
                ),
                MenuItem(
                    organization_id=int(o_b.id),
                    name="OnlyB",
                    category="T",
                    price=200,
                    is_available=True,
                    iiko_id="uuid-b",
                ),
            ],
        )
        db.add(
            StaffUser(
                organization_id=int(o_a.id),
                tenant_owner_id=int(t1.id),
                email="chain@t.kz",
                password_hash=hash_password("secret123"),
                role=StaffRole.ADMIN.value,
                is_active=True,
                is_superadmin=False,
            ),
        )
        await db.commit()

    login = await ac.post(
        "/api/admin/auth/login",
        json={"email": "chain@t.kz", "password": "secret123"},
    )
    assert login.status_code == 200

    r1 = await ac.get("/api/admin/menu")
    assert r1.status_code == 200
    names1 = {it["name"] for it in r1.json()["items"]}
    assert "OnlyA" in names1
    assert "OnlyB" not in names1

    async with sf() as db:
        o_b = (await db.execute(select(Organization).where(Organization.slug == "ob"))).scalar_one()
        b_id = int(o_b.id)

    sw = await ac.post("/api/admin/auth/select-org", json={"organization_id": b_id})
    assert sw.status_code == 200

    r2 = await ac.get("/api/admin/menu")
    assert r2.status_code == 200
    names2 = {it["name"] for it in r2.json()["items"]}
    assert "OnlyB" in names2
    assert "OnlyA" not in names2
