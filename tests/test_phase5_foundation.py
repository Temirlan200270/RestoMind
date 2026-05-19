"""Фундамент Phases 1–4: RBAC manager, location_id, event-driven forecast."""

from __future__ import annotations

import pytest

from app.core.passwords import hash_password
from app.db.models import Organization, StaffRole, StaffUser, Tenant
from app.services.owner_dashboard import event_revenue_history_usable
from app.services.system_events import BusinessEvent, emit_event
from app.services.tenant_scope import (
    available_organizations_for_admin_session,
    staff_assigned_org_ids,
)


@pytest.mark.asyncio
async def test_manager_assigned_org_ids_filters_branches(db_session):
    t = Tenant(name="Net")
    db_session.add(t)
    await db_session.flush()
    o_a = Organization(name="A", slug="pa", tenant_id=int(t.id))
    o_b = Organization(name="B", slug="pb", tenant_id=int(t.id))
    o_c = Organization(name="C", slug="pc", tenant_id=int(t.id))
    db_session.add_all([o_a, o_b, o_c])
    await db_session.flush()

    staff = StaffUser(
        organization_id=int(o_a.id),
        tenant_owner_id=int(t.id),
        email="mgr@t.kz",
        password_hash=hash_password("x"),
        role=StaffRole.MANAGER.value,
        is_active=True,
        meta_json={"assigned_org_ids": [int(o_a.id), int(o_b.id)]},
    )
    db_session.add(staff)
    await db_session.flush()

    assert staff_assigned_org_ids(staff) == [int(o_a.id), int(o_b.id)]

    av = await available_organizations_for_admin_session(
        db_session,
        staff=staff,
        is_superadmin=False,
        is_demo=False,
        session_organization_id=int(o_a.id),
    )
    ids = {int(x["id"]) for x in av}
    assert ids == {int(o_a.id), int(o_b.id)}
    assert int(o_c.id) not in ids


@pytest.mark.asyncio
async def test_operator_sees_single_branch_only(db_session):
    t = Tenant(name="Net2")
    db_session.add(t)
    await db_session.flush()
    o_a = Organization(name="A2", slug="pa2", tenant_id=int(t.id))
    o_b = Organization(name="B2", slug="pb2", tenant_id=int(t.id))
    db_session.add_all([o_a, o_b])
    await db_session.flush()

    staff = StaffUser(
        organization_id=int(o_a.id),
        tenant_owner_id=int(t.id),
        email="op@t.kz",
        password_hash=hash_password("x"),
        role=StaffRole.OPERATOR.value,
        is_active=True,
    )
    db_session.add(staff)
    await db_session.flush()

    av = await available_organizations_for_admin_session(
        db_session,
        staff=staff,
        is_superadmin=False,
        is_demo=False,
        session_organization_id=int(o_a.id),
    )
    assert len(av) == 1
    assert av[0]["id"] == int(o_a.id)


@pytest.mark.asyncio
async def test_emit_event_defaults_location_id_to_org(db_session):
    org = Organization(name="Loc", slug="loc")
    db_session.add(org)
    await db_session.flush()

    ev = BusinessEvent(
        org_id=int(org.id),
        type="order.created",
        actor="ai",
        payload={"order_id": 1},
        id="test-loc-1",
    )
    row = await emit_event(db_session, ev)
    assert row is not None
    assert row.payload_json.get("_location_id") == int(org.id)


def test_event_revenue_history_usable_threshold():
    assert event_revenue_history_usable({"2026-05-01": 0, "2026-05-02": 100, "2026-05-03": 50})
    assert not event_revenue_history_usable({"2026-05-01": 0, "2026-05-02": 0})
