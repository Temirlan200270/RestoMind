"""Platform polish tails: StaffMind meta, location rollup, tour, frozen replay."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Location, Organization, StaffRole, StaffUser
from app.services.context_engine import build_menu_context_from_prices_snapshot


def test_build_menu_context_from_prices_snapshot_edge_case() -> None:
    """G3: пустой menu_context_text — synthetic context из menu_prices_snapshot."""
    ctx = build_menu_context_from_prices_snapshot(
        [
            {"iiko_id": "uuid-plov", "price": 2790.0, "is_available": True},
            {"iiko_id": "uuid-soup", "price": 990.0, "is_available": False},
        ],
    )
    assert ctx is not None
    assert "2790" in ctx
    assert "uuid-plov" in ctx
    assert "СТОП" in ctx


def test_build_menu_context_from_prices_snapshot_empty() -> None:
    assert build_menu_context_from_prices_snapshot([]) is None
    assert build_menu_context_from_prices_snapshot(None) is None


@pytest.mark.asyncio
async def test_rollup_location_event_stats_filters_by_location(db_session: AsyncSession) -> None:
    from app.services.owner_dashboard import rollup_location_event_stats
    from app.services.system_events import BusinessEvent, emit_event

    org = Organization(name="Rollup Org", slug="rollup-org")
    db_session.add(org)
    await db_session.flush()
    loc_a = Location(organization_id=int(org.id), name="A", slug="a")
    loc_b = Location(organization_id=int(org.id), name="B", slug="b")
    db_session.add_all([loc_a, loc_b])
    await db_session.flush()

    await emit_event(
        db_session,
        BusinessEvent(
            org_id=int(org.id),
            type="order.confirmed",
            actor="system",
            location_id=int(loc_a.id),
        ),
    )
    await emit_event(
        db_session,
        BusinessEvent(
            org_id=int(org.id),
            type="order.confirmed",
            actor="system",
            location_id=int(loc_b.id),
        ),
    )
    await db_session.commit()

    rows = await rollup_location_event_stats(
        db_session,
        int(org.id),
        days=1,
        location_id=int(loc_a.id),
    )
    today = rows[0] if rows else {}
    confirmed = int(today.get("orders_confirmed") or 0) if today else 0
    assert confirmed == 1


@pytest.mark.asyncio
async def test_staff_patch_role_metadata_and_locations(asgi_memory_client) -> None:
    from app.core.passwords import hash_password

    client, session_factory = asgi_memory_client
    async with session_factory() as db:
        org = Organization(name="Staff Meta Org", slug="staff-meta-org")
        db.add(org)
        await db.flush()
        loc = Location(organization_id=int(org.id), name="Main", slug="main")
        db.add(loc)
        admin = StaffUser(
            organization_id=int(org.id),
            email="admin@staff-meta.kz",
            password_hash=hash_password("secret123"),
            role=StaffRole.ADMIN.value,
            is_active=True,
        )
        op = StaffUser(
            organization_id=int(org.id),
            email="op@staff-meta.kz",
            password_hash=hash_password("secret123"),
            role=StaffRole.OPERATOR.value,
            is_active=True,
        )
        db.add_all([admin, op])
        await db.commit()
        op_id = int(op.id)
        loc_id = int(loc.id)
    await client.post("/api/admin/auth/login", json={"username": "admin@staff-meta.kz", "password": "secret123"})
    patch = await client.patch(
        f"/api/admin/staff/{op_id}",
        json={
            "assigned_location_ids": [loc_id],
            "role_metadata": {"title": "Кассир", "department": "Зал"},
        },
    )
    assert patch.status_code == 200
    body = patch.json()
    assert body["user"]["role_metadata"]["title"] == "Кассир"
    assert body["user"]["assigned_location_ids"] == [loc_id]


@pytest.mark.asyncio
async def test_auth_tour_complete_persists_meta(asgi_memory_client) -> None:
    from app.core.passwords import hash_password

    client, session_factory = asgi_memory_client
    async with session_factory() as db:
        org = Organization(name="Tour Org", slug="tour-org")
        db.add(org)
        await db.flush()
        db.add(
            StaffUser(
                organization_id=int(org.id),
                email="tour@tour.kz",
                password_hash=hash_password("secret123"),
                role=StaffRole.ADMIN.value,
                is_active=True,
            ),
        )
        await db.commit()
    await client.post("/api/admin/auth/login", json={"username": "tour@tour.kz", "password": "secret123"})
    resp = await client.post(
        "/api/admin/auth/tour-complete",
        json={"completed_at": "2026-05-21T10:00:00+00:00"},
    )
    assert resp.status_code == 200
    assert resp.json()["persisted"] is True
    me = await client.get("/api/admin/auth/me")
    assert me.json().get("tour_completed_at") == "2026-05-21T10:00:00+00:00"
