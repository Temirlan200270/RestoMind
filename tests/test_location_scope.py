"""Phase 1.1: Location resource scope."""

from __future__ import annotations

import pytest

from app.core.passwords import hash_password
from app.db.models import Location, Organization, Order, StaffRole, StaffUser, User
from app.services.tenant_scope import (
    allowed_location_ids_for_staff,
    ensure_default_location,
    location_id_allowed_for_staff,
)


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
