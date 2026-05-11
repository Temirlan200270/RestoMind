"""POST /api/admin/menu/bulk-stoplist — скоуп по филиалу и частичные ошибки."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.passwords import hash_password
from app.db.models import MenuItem, Organization, StaffRole, StaffUser


@pytest.mark.asyncio
async def test_bulk_stoplist_stop_and_unstop(asgi_memory_client) -> None:
    ac, sf = asgi_memory_client
    async with sf() as db:
        org = Organization(name="Bulk Org", slug="bulk-org")
        db.add(org)
        await db.flush()
        db.add_all(
            [
                MenuItem(
                    organization_id=int(org.id),
                    name="A",
                    category="T",
                    price=100,
                    is_available=True,
                    iiko_id="uuid-a",
                ),
                MenuItem(
                    organization_id=int(org.id),
                    name="B",
                    category="T",
                    price=200,
                    is_available=True,
                    iiko_id="uuid-b",
                ),
            ],
        )
        db.add(
            StaffUser(
                organization_id=int(org.id),
                tenant_owner_id=None,
                email="bulk@test.kz",
                password_hash=hash_password("secret123"),
                role=StaffRole.ADMIN.value,
                is_active=True,
                is_superadmin=False,
            ),
        )
        await db.commit()
        ids = (
            await db.execute(
                select(MenuItem.id).where(MenuItem.organization_id == int(org.id)).order_by(MenuItem.id),
            )
        ).scalars().all()
    id_a, id_b = int(ids[0]), int(ids[1])

    login = await ac.post(
        "/api/admin/auth/login",
        json={"email": "bulk@test.kz", "password": "secret123"},
    )
    assert login.status_code == 200

    r1 = await ac.post(
        "/api/admin/menu/bulk-stoplist",
        json={"action": "stop", "item_ids": [id_a, id_b]},
    )
    assert r1.status_code == 200
    body1 = r1.json()
    assert body1["ok"] is True
    assert body1["updated"] == 2
    assert body1["failed"] == []

    async with sf() as db:
        a = await db.get(MenuItem, id_a)
        assert a is not None and a.is_available is False

    r2 = await ac.post(
        "/api/admin/menu/bulk-stoplist",
        json={"action": "unstop", "item_ids": [id_a]},
    )
    assert r2.status_code == 200
    assert r2.json()["updated"] == 1

    r3 = await ac.post(
        "/api/admin/menu/bulk-stoplist",
        json={"action": "set_category", "item_ids": [id_b], "category": "Новое"},
    )
    assert r3.status_code == 200
    async with sf() as db:
        b = await db.get(MenuItem, id_b)
        assert b is not None and b.category == "Новое"


@pytest.mark.asyncio
async def test_bulk_stoplist_foreign_id_reported_not_found(asgi_memory_client) -> None:
    ac, sf = asgi_memory_client
    async with sf() as db:
        o1 = Organization(name="O1", slug="o1bulk")
        db.add(o1)
        await db.flush()
        db.add(
            MenuItem(
                organization_id=int(o1.id),
                name="Only1",
                category="T",
                price=1,
                is_available=True,
                iiko_id="u1",
            ),
        )
        db.add(
            StaffUser(
                organization_id=int(o1.id),
                tenant_owner_id=None,
                email="u1@test.kz",
                password_hash=hash_password("secret123"),
                role=StaffRole.ADMIN.value,
                is_active=True,
                is_superadmin=False,
            ),
        )
        await db.commit()
        gid = (
            await db.execute(select(MenuItem.id).where(MenuItem.organization_id == int(o1.id)).limit(1))
        ).scalar_one()
    gid = int(gid)
    bad_id = 999_999

    await ac.post(
        "/api/admin/auth/login",
        json={"email": "u1@test.kz", "password": "secret123"},
    )

    r = await ac.post(
        "/api/admin/menu/bulk-stoplist",
        json={"action": "stop", "item_ids": [gid, bad_id]},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["updated"] == 1
    assert len(data["failed"]) == 1
    assert data["failed"][0]["error"] == "not_found"


@pytest.mark.asyncio
async def test_bulk_stoplist_deduplicates_item_ids(asgi_memory_client) -> None:
    ac, sf = asgi_memory_client
    async with sf() as db:
        org = Organization(name="Bulk Dedupe Org", slug="bulk-dedupe-org")
        db.add(org)
        await db.flush()
        item = MenuItem(
            organization_id=int(org.id),
            name="Only one",
            category="T",
            price=100,
            is_available=True,
            iiko_id="uuid-dedupe",
        )
        db.add(item)
        db.add(
            StaffUser(
                organization_id=int(org.id),
                tenant_owner_id=None,
                email="dedupe@test.kz",
                password_hash=hash_password("secret123"),
                role=StaffRole.ADMIN.value,
                is_active=True,
                is_superadmin=False,
            ),
        )
        await db.commit()
        item_id = int(item.id)

    await ac.post(
        "/api/admin/auth/login",
        json={"email": "dedupe@test.kz", "password": "secret123"},
    )

    r = await ac.post(
        "/api/admin/menu/bulk-stoplist",
        json={"action": "stop", "item_ids": [item_id, item_id, item_id]},
    )
    assert r.status_code == 200
    assert r.json()["updated"] == 1
