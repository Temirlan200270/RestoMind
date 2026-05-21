"""HTTP regression: async session deps must be awaited in admin list/detail routes."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import pytest

from app.core.passwords import hash_password
from app.db.models import Booking, ChatLog, Location, Organization, StaffUser, User


async def _seed_org_with_chat_and_booking(session_factory) -> tuple[str, str, int, int]:
    async with session_factory() as db:
        org = Organization(name="Deps HTTP Org", slug="deps-http-org")
        db.add(org)
        await db.flush()
        org_id = int(org.id)
        loc = Location(organization_id=org_id, name="Main", slug="main", is_active=True)
        db.add(loc)
        await db.flush()
        user = User(organization_id=org_id, phone="77051310837", name="Guest")
        db.add(user)
        await db.flush()
        db.add(
            ChatLog(
                organization_id=org_id,
                user_id=int(user.id),
                role="user",
                content="Привет",
                created_at=datetime.now(timezone.utc),
                location_id=int(loc.id),
            )
        )
        db.add(
            Booking(
                user_id=int(user.id),
                booking_date=date.today(),
                booking_time=time(19, 0),
                guests=2,
                status="confirmed",
            )
        )
        db.add(
            StaffUser(
                organization_id=org_id,
                email="deps-http@test.kz",
                password_hash=hash_password("secret123"),
                role="admin",
                is_active=True,
            )
        )
        await db.commit()
        return "77051310837", "deps-http@test.kz", int(loc.id), org_id


@pytest.mark.asyncio
async def test_chat_log_http_does_not_500_with_staff_session(asgi_memory_client) -> None:
    client, session_factory = asgi_memory_client
    phone, username, _loc_id, _org_id = await _seed_org_with_chat_and_booking(session_factory)

    login = await client.post(
        "/api/admin/auth/login",
        json={"username": username, "password": "secret123"},
    )
    assert login.status_code == 200

    res = await client.get(f"/api/admin/chats/{phone}?limit=50")
    assert res.status_code == 200
    body = res.json()
    assert isinstance(body.get("messages"), list)
    assert body.get("count", 0) >= 1


@pytest.mark.asyncio
async def test_bookings_list_http_does_not_500_with_staff_session(asgi_memory_client) -> None:
    client, session_factory = asgi_memory_client
    phone, username, _loc_id, _org_id = await _seed_org_with_chat_and_booking(session_factory)

    login = await client.post(
        "/api/admin/auth/login",
        json={"username": username, "password": "secret123"},
    )
    assert login.status_code == 200

    res = await client.get(f"/api/admin/bookings?q={phone}&limit=40")
    assert res.status_code == 200
    body = res.json()
    assert isinstance(body.get("bookings"), list)
    assert body.get("count", 0) >= 1


@pytest.mark.asyncio
async def test_checksession_endpoints_http_smoke(asgi_memory_client) -> None:
    """Routes called from admin checkSession() must not 500 after login."""
    client, session_factory = asgi_memory_client
    _phone, username, loc_id, org_id = await _seed_org_with_chat_and_booking(session_factory)

    async with session_factory() as db:
        org = await db.get(Organization, org_id)
        assert org is not None
        org.force_closed_until = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)
        org.force_closed_reason = "Smoke test"
        await db.commit()

    login = await client.post(
        "/api/admin/auth/login",
        json={"username": username, "password": "secret123"},
    )
    assert login.status_code == 200

    endpoints = [
        "/api/admin/demo/status",
        "/api/admin/organization/profile",
        f"/api/admin/intelligence/revenue-leak?location_id={loc_id}",
        f"/api/admin/chats?limit=60&mode=active&location_id={loc_id}",
        f"/api/admin/inbox/money-queue?location_id={loc_id}",
        "/api/admin/integrations/status",
        "/api/admin/failed-tasks?resolved=false",
        f"/api/admin/shift/state?location_id={loc_id}",
    ]
    for path in endpoints:
        res = await client.get(path)
        assert res.status_code == 200, f"GET {path} -> {res.status_code}: {res.text[:300]}"
