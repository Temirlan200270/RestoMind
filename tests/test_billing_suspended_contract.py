"""
E2.3 light: контракт 403 при suspended tenant.

Цель — зафиксировать стабильный публичный контракт без изменения поведения:

* `POST /api/admin/auth/login` при suspended → 403,
  `detail` содержит «биллинг», заголовок ``X-RestoMind-Suspended-Reason``
  установлен в ``tenant_suspended``.
* Защищённый эндпоинт под суспендом → тоже 403 + тот же заголовок.
* `GET /api/admin/auth/me` без авторизации не блокируется по биллингу
  (возвращает `{authenticated: false}`).
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.passwords import hash_password
from app.db.models import Organization, StaffRole, StaffUser, Tenant
from app.services.billing_guard import (
    BILLING_SUSPENDED_DETAIL,
    BILLING_SUSPENDED_HEADER,
    BILLING_SUSPENDED_HEADER_VALUE,
    billing_suspended_http_exception,
)


def test_billing_suspended_helper_uses_stable_header_and_detail() -> None:
    exc = billing_suspended_http_exception()
    assert exc.status_code == 403
    assert exc.detail == BILLING_SUSPENDED_DETAIL
    assert exc.headers is not None
    assert exc.headers.get(BILLING_SUSPENDED_HEADER) == BILLING_SUSPENDED_HEADER_VALUE


@pytest.mark.asyncio
async def test_login_suspended_returns_403_with_machine_readable_header(asgi_memory_client) -> None:
    ac, sf = asgi_memory_client
    async with sf() as db:
        t = Tenant(name="HeaderNet", plan_status="suspended")
        db.add(t)
        await db.flush()
        o = Organization(name="HBranch", slug="hbr", tenant_id=int(t.id))
        db.add(o)
        await db.flush()
        db.add(StaffUser(
            organization_id=int(o.id),
            tenant_owner_id=int(t.id),
            email="ops@h.kz",
            password_hash=hash_password("pw12345678"),
            role=StaffRole.ADMIN.value,
            is_active=True,
            is_superadmin=False,
        ))
        await db.commit()

    r = await ac.post("/api/admin/auth/login", json={"email": "ops@h.kz", "password": "pw12345678"})
    assert r.status_code == 403
    assert "биллинг" in (r.json().get("detail") or "").lower()
    assert r.headers.get(BILLING_SUSPENDED_HEADER) == BILLING_SUSPENDED_HEADER_VALUE


@pytest.mark.asyncio
async def test_protected_route_after_suspend_carries_header(asgi_memory_client) -> None:
    ac, sf = asgi_memory_client
    async with sf() as db:
        t = Tenant(name="SoonSuspended", plan_status="active")
        db.add(t)
        await db.flush()
        o = Organization(name="SsBranch", slug="ssbr", tenant_id=int(t.id))
        db.add(o)
        await db.flush()
        db.add(StaffUser(
            organization_id=int(o.id),
            tenant_owner_id=int(t.id),
            email="ss@h.kz",
            password_hash=hash_password("pw12345678"),
            role=StaffRole.ADMIN.value,
            is_active=True,
            is_superadmin=False,
        ))
        await db.commit()

    login = await ac.post("/api/admin/auth/login", json={"email": "ss@h.kz", "password": "pw12345678"})
    assert login.status_code == 200

    async with sf() as db:
        tenant = await db.scalar(select(Tenant).where(Tenant.name == "SoonSuspended"))
        assert tenant is not None
        tenant.plan_status = "suspended"
        await db.commit()

    r = await ac.get("/api/admin/bookings")
    assert r.status_code == 403
    assert r.headers.get(BILLING_SUSPENDED_HEADER) == BILLING_SUSPENDED_HEADER_VALUE


@pytest.mark.asyncio
async def test_auth_me_without_session_does_not_block(asgi_memory_client) -> None:
    ac, _sf = asgi_memory_client
    r = await ac.get("/api/admin/auth/me")
    assert r.status_code == 200
    body = r.json()
    assert body == {"authenticated": False}


@pytest.mark.asyncio
async def test_auth_me_carries_billing_blocked_field_for_clients(asgi_memory_client) -> None:
    """`/auth/me` контракт: билет успешной сессии всегда содержит `billing_blocked`."""
    ac, sf = asgi_memory_client
    async with sf() as db:
        t = Tenant(name="ActiveNet", plan_status="active")
        db.add(t)
        await db.flush()
        o = Organization(name="ABranch", slug="abr", tenant_id=int(t.id))
        db.add(o)
        await db.flush()
        db.add(StaffUser(
            organization_id=int(o.id),
            tenant_owner_id=int(t.id),
            email="active@h.kz",
            password_hash=hash_password("pw12345678"),
            role=StaffRole.ADMIN.value,
            is_active=True,
            is_superadmin=False,
        ))
        await db.commit()

    login = await ac.post("/api/admin/auth/login", json={"email": "active@h.kz", "password": "pw12345678"})
    assert login.status_code == 200

    me = await ac.get("/api/admin/auth/me")
    assert me.status_code == 200
    body = me.json()
    assert body.get("authenticated") is True
    assert "billing_blocked" in body
    assert body["billing_blocked"] is False
