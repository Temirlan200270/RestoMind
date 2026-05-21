"""Тесты синхронизации остатков iiko Office."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from app.core.passwords import hash_password
from app.db.models import InventoryStockSnapshot, Organization, StaffUser
from app.integrations.iiko_office_client import IikoOfficeClient, parse_stock_balances_payload
from app.services.iiko_inventory_sync import SOURCE_IIKO_OFFICE, sync_inventory_from_iiko_office
from app.db.models import Location
from app.services.org_iiko_office import (
    OrgIikoOfficeCredentials,
    resolve_location_id_for_iiko_office_store,
)
from app.services.secrets_crypto import encrypt_secret

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "iiko_office" / "stock_balances.json"


def test_parse_stock_balances_fixture_shape() -> None:
    import json

    data = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    rows = parse_stock_balances_payload(data)
    assert len(rows) == 2
    assert rows[0].sku == "RICE-01"
    assert rows[0].product_id == "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
    assert rows[0].quantity == 2.5


@pytest.mark.asyncio
async def test_resolve_location_id_from_store_location_map(db_session) -> None:
    org = Organization(
        name="Multi Loc Org",
        slug="multi-loc-org",
        integration_config_json={
            "iiko_office": {
                "host": "https://office.test",
                "login": "u",
                "password": "p",
                "store_id": "warehouse-a",
                "store_location_map": {"warehouse-a": 42, "warehouse-b": 99},
                "location_id": 7,
            },
        },
    )
    db_session.add(org)
    await db_session.flush()
    assert resolve_location_id_for_iiko_office_store(org, "warehouse-a") == 42
    assert resolve_location_id_for_iiko_office_store(org, "warehouse-b") == 99
    assert resolve_location_id_for_iiko_office_store(org, "unknown") == 7


@pytest.mark.asyncio
async def test_sync_inventory_assigns_location_id(db_session) -> None:
    org = Organization(
        name="Loc Sync Org",
        slug="loc-sync-org",
        integration_config_json={
            "iiko_office": {
                "host": "https://office.test.local",
                "login": "user",
                "password": "secret",
                "store_id": "store-uuid",
                "location_id": None,
            },
        },
    )
    db_session.add(org)
    await db_session.flush()
    loc = Location(organization_id=org.id, name="Kitchen", slug="kitchen")
    db_session.add(loc)
    await db_session.flush()
    org.integration_config_json = {
        "iiko_office": {
            "host": "https://office.test.local",
            "login": "user",
            "password": "secret",
            "store_id": "store-uuid",
            "location_id": int(loc.id),
        },
    }
    await db_session.flush()

    creds = OrgIikoOfficeCredentials(
        host="https://office.test.local",
        login="user",
        password="secret",
        store_id="store-uuid",
        department_id="",
    )
    await sync_inventory_from_iiko_office(
        db_session,
        int(org.id),
        creds=creds,
        fixture_path=str(FIXTURE_PATH),
    )
    await db_session.commit()

    rows = (
        await db_session.execute(
            select(InventoryStockSnapshot).where(
                InventoryStockSnapshot.organization_id == org.id,
            )
        )
    ).scalars().all()
    assert len(rows) == 2
    assert all(r.location_id == int(loc.id) for r in rows)


@pytest.mark.asyncio
async def test_sync_inventory_from_fixture(db_session) -> None:
    org = Organization(name="Iiko Office Org", slug="iiko-office-org", integration_config_json={})
    db_session.add(org)
    await db_session.flush()

    creds = OrgIikoOfficeCredentials(
        host="https://office.test.local",
        login="user",
        password="secret",
        store_id="store-uuid",
        department_id="",
    )
    stats = await sync_inventory_from_iiko_office(
        db_session,
        int(org.id),
        creds=creds,
        fixture_path=str(FIXTURE_PATH),
    )
    await db_session.commit()

    assert stats["total"] == 2
    assert stats["updated"] == 2
    rows = (
        await db_session.execute(
            select(InventoryStockSnapshot).where(
                InventoryStockSnapshot.organization_id == org.id,
                InventoryStockSnapshot.source == SOURCE_IIKO_OFFICE,
            )
        )
    ).scalars().all()
    assert len(rows) == 2
    rice = next(r for r in rows if r.sku == "RICE-01")
    assert rice.ingredient == "Рис басмати"
    assert float(rice.quantity) == 2.5
    assert rice.external_id == "a1b2c3d4-e5f6-7890-abcd-ef1234567890"


@pytest.mark.asyncio
async def test_iiko_office_client_accepts_plain_text_auth_token() -> None:
    creds = OrgIikoOfficeCredentials(
        host="https://office.test.local",
        login="user",
        password="secret",
        store_id="store-uuid",
        department_id="",
    )
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/resto/api/auth":
            assert request.url.params["login"] == "user"
            assert request.url.params["pass"] == "secret"
            return httpx.Response(200, text="session-key-123")
        if request.url.path == "/resto/api/v2/reports/balance/stores":
            assert request.url.params["key"] == "session-key-123"
            assert request.url.params["store"] == "store-uuid"
            return httpx.Response(
                200,
                json={"items": [{"productId": "p1", "productNum": "SKU-1", "productName": "Rice", "amount": 1}]},
            )
        return httpx.Response(404)

    async with IikoOfficeClient(creds, transport=httpx.MockTransport(handler)) as client:
        rows = await client.fetch_stock_balances()

    assert [r.sku for r in rows] == ["SKU-1"]
    assert seen[0].method == "POST"


@pytest.mark.asyncio
async def test_iiko_office_client_falls_back_to_json_auth_shape() -> None:
    creds = OrgIikoOfficeCredentials(
        host="https://office.test.local",
        login="user",
        password="secret",
        store_id="store-uuid",
        department_id="",
    )
    attempts: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request)
        if request.url.path == "/resto/api/auth":
            if len(attempts) < 3:
                return httpx.Response(415)
            return httpx.Response(200, json={"key": "json-key"})
        if request.url.path == "/resto/api/v2/reports/balance/stores":
            assert request.url.params["key"] == "json-key"
            return httpx.Response(200, json={"items": []})
        return httpx.Response(404)

    async with IikoOfficeClient(creds, transport=httpx.MockTransport(handler)) as client:
        rows = await client.fetch_stock_balances()

    assert rows == []
    assert len([r for r in attempts if r.url.path == "/resto/api/auth"]) == 3


@pytest.mark.asyncio
async def test_admin_inventory_sync_endpoints(asgi_memory_client, monkeypatch) -> None:
    from cryptography.fernet import Fernet

    from app.core.config import settings
    import app.services.secrets_crypto as secrets_crypto_mod

    monkeypatch.setattr(
        settings,
        "app_secrets_fernet_key",
        Fernet.generate_key().decode("ascii"),
    )
    secrets_crypto_mod._fernet = None

    client, session_factory = asgi_memory_client
    password_enc = encrypt_secret("office-pass")

    async with session_factory() as db:
        org = Organization(
            name="Sync API Org",
            slug="sync-api-org",
            integration_config_json={
                "iiko_office": {
                    "host": "https://office.test.local",
                    "login": "api",
                    "password_enc": password_enc,
                    "store_id": "warehouse-1",
                },
            },
        )
        db.add(org)
        await db.flush()
        db.add(
            StaffUser(
                organization_id=org.id,
                email="iiko-inv@test.kz",
                password_hash=hash_password("secret123"),
                role="admin",
                is_active=True,
            ),
        )
        await db.commit()
        org_id = int(org.id)

    login = await client.post(
        "/api/admin/auth/login",
        json={"username": "iiko-inv@test.kz", "password": "secret123"},
    )
    assert login.status_code == 200

    status_before = await client.get("/api/admin/inventory/sync-status")
    assert status_before.status_code == 200
    body = status_before.json()
    assert body["iiko_office_configured"] is True
    assert body["last_inventory_sync"]["at"] is None

    async def _run_with_fixture(org_id: int) -> dict:
        from app.services.integration_health import record_inventory_sync
        from app.services.iiko_inventory_sync import sync_inventory_from_iiko_office

        async with session_factory() as db:
            stats = await sync_inventory_from_iiko_office(
                db,
                org_id,
                fixture_path=str(FIXTURE_PATH),
            )
            await record_inventory_sync(
                db, True, organization_id=org_id, detail=str(stats),
            )
            await db.commit()
        return {"ok": True, "stats": stats, "org_id": org_id}

    monkeypatch.setattr(
        "app.api.admin.inventory_sync.run_inventory_sync",
        _run_with_fixture,
    )

    sync_res = await client.post("/api/admin/inventory/sync-iiko")
    assert sync_res.status_code == 200
    assert sync_res.json()["updated"] == 2

    status_after = await client.get("/api/admin/inventory/sync-status")
    assert status_after.status_code == 200
    assert status_after.json()["last_inventory_sync"]["ok"] is True

    async with session_factory() as db:
        count = len(
            (
                await db.execute(
                    select(InventoryStockSnapshot).where(
                        InventoryStockSnapshot.organization_id == org_id,
                        InventoryStockSnapshot.source == SOURCE_IIKO_OFFICE,
                    )
                )
            ).scalars().all()
        )
        assert count == 2


@pytest.mark.asyncio
async def test_run_inventory_sync_task_records_health(db_session) -> None:
    from app.db.models import OrganizationIntegrationSync
    from app.services.iiko_inventory_sync import sync_inventory_from_iiko_office
    from app.services.integration_health import record_inventory_sync
    from app.services.org_iiko_office import resolve_org_iiko_office_credentials

    org = Organization(
        name="Task Org",
        slug="task-org",
        integration_config_json={
            "iiko_office": {
                "host": "https://office.test.local",
                "login": "u",
                "password": "p",
                "store_id": "s1",
            },
        },
    )
    db_session.add(org)
    await db_session.flush()
    org_id = int(org.id)

    creds = await resolve_org_iiko_office_credentials(db_session, org_id)
    assert creds is not None
    stats = await sync_inventory_from_iiko_office(
        db_session,
        org_id,
        creds=creds,
        fixture_path=str(FIXTURE_PATH),
    )
    await record_inventory_sync(db_session, True, organization_id=org_id, detail=str(stats))
    await db_session.commit()

    assert stats["total"] == 2
    row = await db_session.get(OrganizationIntegrationSync, org_id)
    assert row is not None
    assert row.last_inventory_sync_ok is True
    assert row.last_inventory_sync_at is not None


@pytest.mark.asyncio
async def test_iiko_office_client_http_mock_transport(db_session) -> None:
    """Simulated live API: auth + balance без реального iiko Office."""
    import json

    import httpx

    from app.integrations.iiko_office_client import AUTH_PATH, BALANCE_PATH, IikoOfficeClient

    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(AUTH_PATH):
            return httpx.Response(200, text="mock-session-key-abc")
        if BALANCE_PATH in request.url.path:
            assert request.url.params.get("store") == "warehouse-1"
            assert request.url.params.get("key") == "mock-session-key-abc"
            return httpx.Response(200, json=fixture)
        return httpx.Response(404, text="not found")

    creds = OrgIikoOfficeCredentials(
        host="https://office.mock.iiko.it",
        login="api",
        password="secret",
        store_id="warehouse-1",
        department_id="",
    )
    transport = httpx.MockTransport(handler)
    async with IikoOfficeClient(creds, transport=transport) as client:
        rows = await client.fetch_stock_balances()

    assert len(rows) == 2
    assert rows[0].sku == "RICE-01"


@pytest.mark.asyncio
async def test_sync_inventory_via_http_mock_transport(db_session) -> None:
    """Полный sync path через MockTransport (без fixture_path)."""
    import json

    import httpx

    from app.integrations.iiko_office_client import AUTH_PATH, BALANCE_PATH

    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(AUTH_PATH):
            return httpx.Response(200, text="live-mock-key")
        if BALANCE_PATH in request.url.path:
            return httpx.Response(200, json=fixture)
        return httpx.Response(404)

    org = Organization(
        name="HTTP Mock Org",
        slug="http-mock-org",
        integration_config_json={
            "iiko_office": {
                "host": "https://office.mock.iiko.it",
                "login": "u",
                "password": "p",
                "store_id": "warehouse-1",
            },
        },
    )
    db_session.add(org)
    await db_session.flush()

    stats = await sync_inventory_from_iiko_office(
        db_session,
        int(org.id),
        transport=httpx.MockTransport(handler),
    )
    await db_session.commit()

    assert stats["total"] == 2
    assert stats["updated"] == 2
    rows = (
        await db_session.execute(
            select(InventoryStockSnapshot).where(
                InventoryStockSnapshot.organization_id == org.id,
                InventoryStockSnapshot.source == SOURCE_IIKO_OFFICE,
            )
        )
    ).scalars().all()
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_organization_iiko_office_patch_and_get(asgi_memory_client, monkeypatch) -> None:
    from cryptography.fernet import Fernet

    from app.core.config import settings
    import app.services.secrets_crypto as secrets_crypto_mod

    monkeypatch.setattr(
        settings,
        "app_secrets_fernet_key",
        Fernet.generate_key().decode("ascii"),
    )
    secrets_crypto_mod._fernet = None

    client, session_factory = asgi_memory_client

    async with session_factory() as db:
        org = Organization(name="Office API Org", slug="office-api-org", integration_config_json={})
        db.add(org)
        await db.flush()
        db.add(
            StaffUser(
                organization_id=org.id,
                email="office-admin@test.kz",
                password_hash=hash_password("secret123"),
                role="admin",
                is_active=True,
            ),
        )
        await db.commit()

    login = await client.post(
        "/api/admin/auth/login",
        json={"username": "office-admin@test.kz", "password": "secret123"},
    )
    assert login.status_code == 200

    get_before = await client.get("/api/admin/organization/iiko-office")
    assert get_before.status_code == 200
    assert get_before.json()["configured"] is False

    patch_res = await client.patch(
        "/api/admin/organization/iiko-office",
        json={
            "host": "https://office.test.local",
            "login": "api",
            "password": "office-pass",
            "store_id": "warehouse-1",
            "department_id": "dept-1",
        },
    )
    assert patch_res.status_code == 200
    body = patch_res.json()
    assert body["configured"] is True
    assert body["password_set"] is True
    assert body["host"] == "https://office.test.local"
    assert "office-pass" not in str(body)

    get_after = await client.get("/api/admin/organization/iiko-office")
    assert get_after.status_code == 200
    assert get_after.json()["configured"] is True


@pytest.mark.asyncio
async def test_inventory_sync_forbidden_for_operator(asgi_memory_client) -> None:
    client, session_factory = asgi_memory_client

    async with session_factory() as db:
        org = Organization(
            name="Op Org",
            slug="op-org",
            integration_config_json={
                "iiko_office": {
                    "host": "https://office.test",
                    "login": "u",
                    "password": "p",
                    "store_id": "s1",
                },
            },
        )
        db.add(org)
        await db.flush()
        db.add(
            StaffUser(
                organization_id=org.id,
                email="op@test.kz",
                password_hash=hash_password("secret123"),
                role="operator",
                is_active=True,
            ),
        )
        await db.commit()

    login = await client.post(
        "/api/admin/auth/login",
        json={"username": "op@test.kz", "password": "secret123"},
    )
    assert login.status_code == 200

    sync_res = await client.post("/api/admin/inventory/sync-iiko")
    assert sync_res.status_code == 403

    status_res = await client.get("/api/admin/inventory/sync-status")
    assert status_res.status_code == 200
