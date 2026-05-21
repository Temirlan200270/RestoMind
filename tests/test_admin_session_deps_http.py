"""HTTP regression: async session deps must be awaited in admin list/detail routes."""

from __future__ import annotations

from datetime import date, datetime, time, timezone

import pytest

from app.core.passwords import hash_password
from app.db.models import Booking, ChatLog, Organization, StaffUser, User


async def _seed_org_with_chat_and_booking(session_factory) -> tuple[str, str]:
    async with session_factory() as db:
        org = Organization(name="Deps HTTP Org", slug="deps-http-org")
        db.add(org)
        await db.flush()
        user = User(organization_id=int(org.id), phone="77051310837", name="Guest")
        db.add(user)
        await db.flush()
        db.add(
            ChatLog(
                organization_id=int(org.id),
                user_id=int(user.id),
                role="user",
                content="Привет",
                created_at=datetime.now(timezone.utc),
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
                organization_id=int(org.id),
                email="deps-http@test.kz",
                password_hash=hash_password("secret123"),
                role="admin",
                is_active=True,
            )
        )
        await db.commit()
        return "77051310837", "deps-http@test.kz"


@pytest.mark.asyncio
async def test_chat_log_http_does_not_500_with_staff_session(asgi_memory_client) -> None:
    client, session_factory = asgi_memory_client
    phone, username = await _seed_org_with_chat_and_booking(session_factory)

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
    phone, username = await _seed_org_with_chat_and_booking(session_factory)

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
