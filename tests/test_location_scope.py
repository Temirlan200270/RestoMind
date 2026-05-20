"""Phase 1.1: Location resource scope."""

from __future__ import annotations

import pytest
from datetime import datetime, timezone
from fastapi import HTTPException

from app.core.passwords import hash_password
from app.db.models import Location, Organization, Order, StaffRole, StaffUser, User
from app.api.admin.analytics import dashboard_stats
from app.services.tenant_scope import (
    allowed_location_ids_for_staff,
    ensure_default_location,
    location_id_allowed_for_staff,
)


class DummyRequest:
    def __init__(self, organization_id: int, staff_id: int | None = None) -> None:
        self.session = {"admin_ok": True, "organization_id": organization_id}
        if staff_id is not None:
            self.session["staff_id"] = staff_id


@pytest.mark.asyncio
async def test_ensure_default_location_creates_one(db_session):
    org = Organization(name="LocOrg", slug="loc-org")
    db_session.add(org)
    await db_session.flush()
    loc = await ensure_default_location(db_session, int(org.id))
    assert loc.id is not None
    loc2 = await ensure_default_location(db_session, int(org.id))
    assert int(loc2.id) == int(loc.id)


@pytest.mark.asyncio
async def test_operator_restricted_to_assigned_location(db_session):
    org = Organization(name="Multi", slug="multi")
    db_session.add(org)
    await db_session.flush()
    a = Location(organization_id=int(org.id), name="A", slug="a", is_active=True)
    b = Location(organization_id=int(org.id), name="B", slug="b", is_active=True)
    db_session.add_all([a, b])
    await db_session.flush()

    staff = StaffUser(
        organization_id=int(org.id),
        email="op@multi.kz",
        password_hash=hash_password("x"),
        role=StaffRole.OPERATOR.value,
        is_active=True,
        meta_json={"assigned_location_ids": [int(a.id)]},
    )
    db_session.add(staff)
    await db_session.flush()

    allowed = await allowed_location_ids_for_staff(
        db_session, staff=staff, org_id=int(org.id), is_superadmin=False,
    )
    assert allowed == {int(a.id)}
    assert await location_id_allowed_for_staff(
        db_session,
        staff=staff,
        org_id=int(org.id),
        location_id=int(b.id),
        is_superadmin=False,
    ) is False


@pytest.mark.asyncio
async def test_dashboard_stats_filters_revenue_by_location(db_session):
    org = Organization(name="Metrics", slug="metrics")
    db_session.add(org)
    await db_session.flush()
    loc_a = Location(organization_id=int(org.id), name="A", slug="a", is_active=True)
    loc_b = Location(organization_id=int(org.id), name="B", slug="b", is_active=True)
    db_session.add_all([loc_a, loc_b])
    await db_session.flush()
    user = User(organization_id=int(org.id), phone="+77000000001")
    db_session.add(user)
    await db_session.flush()
    now = datetime.now(timezone.utc)
    db_session.add_all([
        Order(
            organization_id=int(org.id),
            user_id=int(user.id),
            location_id=int(loc_a.id),
            status="confirmed",
            total_price=1000,
            created_at=now,
        ),
        Order(
            organization_id=int(org.id),
            user_id=int(user.id),
            location_id=int(loc_b.id),
            status="confirmed",
            total_price=3000,
            created_at=now,
        ),
    ])
    await db_session.flush()

    out = await dashboard_stats(DummyRequest(int(org.id)), db_session, location_id=int(loc_a.id))

    assert out["today_orders"] == 1
    assert out["today_revenue"] == 1000.0
    assert out["location_scope"]["location_id"] == int(loc_a.id)
    assert out["location_scope"]["source"] == "sql_location"


@pytest.mark.asyncio
async def test_dashboard_stats_forbids_unassigned_location(db_session):
    org = Organization(name="ForbiddenMetrics", slug="forbidden-metrics")
    db_session.add(org)
    await db_session.flush()
    loc_a = Location(organization_id=int(org.id), name="A", slug="a", is_active=True)
    loc_b = Location(organization_id=int(org.id), name="B", slug="b", is_active=True)
    db_session.add_all([loc_a, loc_b])
    await db_session.flush()
    staff = StaffUser(
        organization_id=int(org.id),
        email="op-metrics@example.kz",
        password_hash=hash_password("x"),
        role=StaffRole.OPERATOR.value,
        is_active=True,
        meta_json={"assigned_location_ids": [int(loc_a.id)]},
    )
    db_session.add(staff)
    await db_session.flush()

    with pytest.raises(HTTPException) as exc:
        await dashboard_stats(DummyRequest(int(org.id), int(staff.id)), db_session, location_id=int(loc_b.id))

    assert exc.value.status_code == 403
