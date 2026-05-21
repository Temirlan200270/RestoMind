"""HTTP smoke for Final Mile admin API (complements manual browser checklist)."""

from __future__ import annotations

import pytest

from app.core.passwords import hash_password
from app.db.models import Organization, StaffUser


@pytest.mark.asyncio
async def test_final_mile_http_smoke_endpoints(asgi_memory_client) -> None:
    """Key Final Mile routes respond for authenticated admin (no 5xx)."""
    client, session_factory = asgi_memory_client

    async with session_factory() as db:
        org = Organization(name="FM Smoke Org", slug="fm-smoke-org", integration_config_json={})
        db.add(org)
        await db.flush()
        db.add(
            StaffUser(
                organization_id=org.id,
                email="fm-admin@test.kz",
                password_hash=hash_password("secret123"),
                role="admin",
                is_active=True,
            ),
        )
        await db.commit()

    login = await client.post(
        "/api/admin/auth/login",
        json={"username": "fm-admin@test.kz", "password": "secret123"},
    )
    assert login.status_code == 200

    endpoints: list[tuple[str, str]] = [
        ("GET", "/api/admin/intelligence/daily-os-digest/preview"),
        ("GET", "/api/admin/intelligence/inventory/stock-alerts"),
        ("GET", "/api/admin/intelligence/supplymind/drafts"),
        ("GET", "/api/admin/inventory/sync-status"),
        ("GET", "/api/admin/intelligence/voice/status"),
        ("GET", "/api/admin/intelligence/voice/calls"),
        ("GET", "/api/admin/intelligence/reviews/external"),
        ("GET", "/api/admin/shift/state"),
        ("GET", "/api/admin/organization/iiko-office"),
    ]
    for method, path in endpoints:
        if method == "GET":
            res = await client.get(path)
        else:
            res = await client.request(method, path)
        assert res.status_code in (200, 404), f"{method} {path} -> {res.status_code}"


@pytest.mark.asyncio
async def test_final_mile_operator_iiko_office_patch_forbidden(asgi_memory_client) -> None:
    client, session_factory = asgi_memory_client

    async with session_factory() as db:
        org = Organization(name="FM Op Org", slug="fm-op-org", integration_config_json={})
        db.add(org)
        await db.flush()
        db.add(
            StaffUser(
                organization_id=org.id,
                email="fm-op@test.kz",
                password_hash=hash_password("secret123"),
                role="operator",
                is_active=True,
            ),
        )
        await db.commit()

    login = await client.post(
        "/api/admin/auth/login",
        json={"username": "fm-op@test.kz", "password": "secret123"},
    )
    assert login.status_code == 200

    patch_res = await client.patch(
        "/api/admin/organization/iiko-office",
        json={"host": "https://x", "login": "u", "password": "p", "store_id": "s"},
    )
    assert patch_res.status_code == 403
