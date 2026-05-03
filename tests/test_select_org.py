"""POST /api/admin/auth/select-org: доступ и сессия (E2.1)."""

from __future__ import annotations

import pytest

from app.core.passwords import hash_password
from app.db.models import Organization, StaffRole, StaffUser, Tenant


@pytest.mark.asyncio
async def test_select_org_rejects_foreign_tenant(asgi_memory_client):
    ac, sf = asgi_memory_client
    async with sf() as db:
        t1 = Tenant(name="HoldOne")
        t2 = Tenant(name="HoldTwo")
        db.add_all([t1, t2])
        await db.flush()
        o_a = Organization(name="A", slug="a", tenant_id=int(t1.id))
        o_other = Organization(name="X", slug="x", tenant_id=int(t2.id))
        db.add_all([o_a, o_other])
        await db.flush()
        db.add(
            StaffUser(
                organization_id=int(o_a.id),
                tenant_owner_id=int(t1.id),
                email="owner@t.kz",
                password_hash=hash_password("secret123"),
                role=StaffRole.ADMIN.value,
                is_active=True,
                is_superadmin=False,
            ),
        )
        await db.commit()
        other_id = int(o_other.id)

    login = await ac.post(
        "/api/admin/auth/login",
        json={"email": "owner@t.kz", "password": "secret123"},
    )
    assert login.status_code == 200

    bad = await ac.post("/api/admin/auth/select-org", json={"organization_id": other_id})
    assert bad.status_code == 403


@pytest.mark.asyncio
async def test_select_org_updates_active_and_me_contract(asgi_memory_client):
    ac, sf = asgi_memory_client
    async with sf() as db:
        t1 = Tenant(name="Net")
        db.add(t1)
        await db.flush()
        o_a = Organization(name="Branch A", slug="ba", tenant_id=int(t1.id))
        o_b = Organization(name="Branch B", slug="bb", tenant_id=int(t1.id))
        db.add_all([o_a, o_b])
        await db.flush()
        db.add(
            StaffUser(
                organization_id=int(o_a.id),
                tenant_owner_id=int(t1.id),
                email="multi@t.kz",
                password_hash=hash_password("pw99999999"),
                role=StaffRole.ADMIN.value,
                is_active=True,
                is_superadmin=False,
            ),
        )
        await db.commit()
        b_id = int(o_b.id)
        tenant_id = int(t1.id)

    await ac.post(
        "/api/admin/auth/login",
        json={"email": "multi@t.kz", "password": "pw99999999"},
    )
    switch = await ac.post("/api/admin/auth/select-org", json={"organization_id": b_id})
    assert switch.status_code == 200
    body = switch.json()
    assert body.get("active_organization_id") == b_id
    assert body.get("organization_id") == b_id
    assert body.get("tenant_owner_id") == tenant_id
    assert isinstance(body.get("available_organizations"), list)
    assert len(body["available_organizations"]) == 2
    assert body.get("tenant") is not None
    assert int(body["tenant"]["id"]) == tenant_id
    assert body.get("branding") is not None
    assert "brand_logo_url" in body["branding"]

    me = await ac.get("/api/admin/auth/me")
    assert me.status_code == 200
    assert me.json().get("active_organization_id") == b_id
