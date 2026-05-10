"""
E2.2 — GET/PATCH /api/admin/branding и POST /api/admin/branding/logo.

Покрываем:
- доступ через cookie-сессию администратора (логин фикстуры);
- что бренд читается/обновляется на уровне ``Tenant`` (а не одного филиала);
- валидацию HEX и размера/MIME логотипа;
- что результат отражается в ``GET /api/admin/auth/me → branding``.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from app.core.passwords import hash_password
from app.db.models import Organization, StaffRole, StaffUser, Tenant


async def _seed_admin_with_tenant(sf, *, tenant_name: str = "Brand Tenant", email: str = "brand@test.kz") -> tuple[int, int]:
    """Создать tenant + org + staff-админа; вернуть (tenant_id, organization_id)."""
    async with sf() as db:
        tenant = Tenant(name=tenant_name, plan="standard")
        db.add(tenant)
        await db.flush()
        org = Organization(
            tenant_id=int(tenant.id),
            name=f"{tenant_name} — branch",
            slug=f"{tenant_name.lower().replace(' ', '-')}-branch",
        )
        db.add(org)
        await db.flush()
        db.add(
            StaffUser(
                organization_id=int(org.id),
                tenant_owner_id=int(tenant.id),
                email=email,
                password_hash=hash_password("secret123"),
                role=StaffRole.ADMIN.value,
                is_active=True,
                is_superadmin=False,
            ),
        )
        await db.commit()
        return int(tenant.id), int(org.id)


@pytest.mark.asyncio
async def test_branding_get_returns_empty_payload_for_fresh_tenant(asgi_memory_client) -> None:
    ac, sf = asgi_memory_client
    tenant_id, _ = await _seed_admin_with_tenant(sf, email="get@test.kz")
    login = await ac.post("/api/admin/auth/login", json={"email": "get@test.kz", "password": "secret123"})
    assert login.status_code == 200

    r = await ac.get("/api/admin/branding")
    assert r.status_code == 200
    data = r.json()
    assert data["tenant_id"] == tenant_id
    assert data["brand_name"] is None
    assert data["brand_color_hex"] is None
    assert data["brand_logo_url"] is None


@pytest.mark.asyncio
async def test_branding_patch_updates_tenant_and_auth_me(asgi_memory_client) -> None:
    ac, sf = asgi_memory_client
    tenant_id, _ = await _seed_admin_with_tenant(sf, email="patch@test.kz", tenant_name="ColorNet")
    login = await ac.post("/api/admin/auth/login", json={"email": "patch@test.kz", "password": "secret123"})
    assert login.status_code == 200

    r = await ac.patch(
        "/api/admin/branding",
        json={"brand_name": "  Plov House  ", "brand_color_hex": "#7B3F00"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["brand_name"] == "Plov House"  # trimmed
    assert body["brand_color_hex"] == "#7b3f00"  # lower-cased

    me = await ac.get("/api/admin/auth/me")
    assert me.status_code == 200
    me_data = me.json()
    assert me_data["branding"]["brand_name"] == "Plov House"
    assert me_data["branding"]["brand_color_hex"] == "#7b3f00"
    assert me_data["branding"]["brand_logo_url"] is None
    # tenant payload остаётся прежним
    assert me_data["tenant"]["id"] == tenant_id

    async with sf() as db:
        tenant = await db.get(Tenant, tenant_id)
        assert tenant is not None
        assert tenant.brand_name == "Plov House"
        assert tenant.brand_color_hex == "#7b3f00"


@pytest.mark.asyncio
async def test_branding_patch_rejects_invalid_color(asgi_memory_client) -> None:
    ac, sf = asgi_memory_client
    await _seed_admin_with_tenant(sf, email="bad@test.kz")
    login = await ac.post("/api/admin/auth/login", json={"email": "bad@test.kz", "password": "secret123"})
    assert login.status_code == 200

    r = await ac.patch("/api/admin/branding", json={"brand_color_hex": "blue"})
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "RRGGBB" in str(detail)


@pytest.mark.asyncio
async def test_branding_patch_blank_name_clears_field(asgi_memory_client) -> None:
    ac, sf = asgi_memory_client
    tenant_id, _ = await _seed_admin_with_tenant(sf, email="blank@test.kz", tenant_name="ClearMe")
    login = await ac.post("/api/admin/auth/login", json={"email": "blank@test.kz", "password": "secret123"})
    assert login.status_code == 200

    await ac.patch("/api/admin/branding", json={"brand_name": "Hello"})
    r = await ac.patch("/api/admin/branding", json={"brand_name": "   "})
    assert r.status_code == 200
    assert r.json()["brand_name"] is None

    async with sf() as db:
        tenant = await db.get(Tenant, tenant_id)
        assert tenant is not None
        assert tenant.brand_name is None


@pytest.mark.asyncio
async def test_branding_logo_upload_writes_file_and_updates_url(asgi_memory_client, tmp_path, monkeypatch) -> None:
    ac, sf = asgi_memory_client
    tenant_id, _ = await _seed_admin_with_tenant(sf, email="logo@test.kz")
    login = await ac.post("/api/admin/auth/login", json={"email": "logo@test.kz", "password": "secret123"})
    assert login.status_code == 200

    target_dir: Path = tmp_path / "logos"
    monkeypatch.setattr("app.api.admin.branding._LOGO_DIR", target_dir)

    png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    files = {"file": ("logo.png", io.BytesIO(png_bytes), "image/png")}
    r = await ac.post("/api/admin/branding/logo", files=files)
    assert r.status_code == 200
    payload = r.json()
    assert payload["tenant_id"] == tenant_id
    assert payload["brand_logo_url"] is not None
    assert payload["brand_logo_url"].startswith(f"/static/uploads/branding/tenant-{tenant_id}.png")

    on_disk = target_dir / f"tenant-{tenant_id}.png"
    assert on_disk.exists()
    assert on_disk.read_bytes() == png_bytes


@pytest.mark.asyncio
async def test_branding_logo_rejects_unsupported_mime(asgi_memory_client, tmp_path, monkeypatch) -> None:
    ac, sf = asgi_memory_client
    await _seed_admin_with_tenant(sf, email="mime@test.kz")
    login = await ac.post("/api/admin/auth/login", json={"email": "mime@test.kz", "password": "secret123"})
    assert login.status_code == 200

    monkeypatch.setattr("app.api.admin.branding._LOGO_DIR", tmp_path / "logos")
    files = {"file": ("logo.gif", io.BytesIO(b"GIF89a"), "image/gif")}
    r = await ac.post("/api/admin/branding/logo", files=files)
    assert r.status_code == 415


@pytest.mark.asyncio
async def test_branding_logo_rejects_too_large_file(asgi_memory_client, tmp_path, monkeypatch) -> None:
    ac, sf = asgi_memory_client
    await _seed_admin_with_tenant(sf, email="big@test.kz")
    login = await ac.post("/api/admin/auth/login", json={"email": "big@test.kz", "password": "secret123"})
    assert login.status_code == 200

    monkeypatch.setattr("app.api.admin.branding._LOGO_DIR", tmp_path / "logos")
    blob = b"\xff" * (1024 * 1024 + 64)
    files = {"file": ("big.png", io.BytesIO(blob), "image/png")}
    r = await ac.post("/api/admin/branding/logo", files=files)
    assert r.status_code == 413


@pytest.mark.asyncio
async def test_branding_routes_require_tenant(asgi_memory_client) -> None:
    """Org без tenant_id → 409 (контракт E2.2: бренд только для арендатора)."""
    ac, sf = asgi_memory_client
    async with sf() as db:
        org = Organization(name="Lonely", slug="lonely")
        db.add(org)
        await db.flush()
        db.add(
            StaffUser(
                organization_id=int(org.id),
                tenant_owner_id=None,
                email="solo@test.kz",
                password_hash=hash_password("secret123"),
                role=StaffRole.ADMIN.value,
                is_active=True,
                is_superadmin=False,
            ),
        )
        await db.commit()

    login = await ac.post("/api/admin/auth/login", json={"email": "solo@test.kz", "password": "secret123"})
    assert login.status_code == 200

    r = await ac.get("/api/admin/branding")
    assert r.status_code == 409
