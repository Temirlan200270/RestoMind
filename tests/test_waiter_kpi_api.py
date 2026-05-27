"""Admin API: KPI офiciантов из iiko."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from app.core.passwords import hash_password
from app.db.models import Organization, StaffUser, WaiterKpiDaily, WaiterRegistry

CLOUD_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "iiko_cloud" / "deliveries_waiter_sample.json"


@pytest.mark.asyncio
async def test_waiter_kpi_api_ranking_and_export(asgi_memory_client, monkeypatch) -> None:
    client, session_factory = asgi_memory_client

    async with session_factory() as db:
        org = Organization(
            name="KPI API Org",
            slug="kpi-api-org",
            timezone="UTC",
            iiko_api_login="test-login",
            iiko_organization_id="test-org-uuid",
        )
        db.add(org)
        await db.flush()
        db.add(
            StaffUser(
                organization_id=org.id,
                email="kpi-admin@test.kz",
                password_hash=hash_password("secret123"),
                role="admin",
                is_active=True,
            ),
        )
        db.add(
            WaiterRegistry(
                organization_id=org.id,
                waiter_iiko_id="w-1",
                waiter_name="Тест Официант",
                source="cloud_delivery",
            ),
        )
        db.add(
            WaiterKpiDaily(
                organization_id=org.id,
                kpi_date=date(2026, 5, 20),
                waiter_iiko_id="w-1",
                orders_served=5,
                total_revenue_kzt=50000,
                avg_check_kzt=10000,
                guests_count=10,
                cancelled_orders=1,
            ),
        )
        await db.commit()
        org_id = int(org.id)

    login = await client.post(
        "/api/admin/auth/login",
        json={"username": "kpi-admin@test.kz", "password": "secret123"},
    )
    assert login.status_code == 200

    list_res = await client.get("/api/admin/analytics/waiter-kpi?date_from=2026-05-20&date_to=2026-05-20")
    assert list_res.status_code == 200
    body = list_res.json()
    assert body["ok"] is True
    assert len(body["items"]) >= 1
    assert body["items"][0]["waiter_name"] == "Тест Официант"
    assert body["items"][0]["orders_served"] == 5

    status_res = await client.get("/api/admin/analytics/waiter-kpi/sync-status")
    assert status_res.status_code == 200
    assert status_res.json()["ok"] is True

    csv_res = await client.get("/api/admin/analytics/waiter-kpi/export.csv?date_from=2026-05-20&date_to=2026-05-20")
    assert csv_res.status_code == 200
    assert "Тест Официант" in csv_res.text

    async with session_factory() as db:
        other = Organization(name="Other Org", slug="other-kpi-org", timezone="UTC")
        db.add(other)
        await db.flush()
        other_id = int(other.id)
        db.add(
            WaiterKpiDaily(
                organization_id=other_id,
                kpi_date=date(2026, 5, 20),
                waiter_iiko_id="w-other",
                orders_served=99,
                total_revenue_kzt=999999,
                avg_check_kzt=10000,
            ),
        )
        await db.commit()

    list_res2 = await client.get("/api/admin/analytics/waiter-kpi?date_from=2026-05-20&date_to=2026-05-20")
    names = [i["waiter_name"] for i in list_res2.json()["items"]]
    assert "Тест Официант" in names
    assert not any(i.get("orders_served") == 99 for i in list_res2.json()["items"])

    async def _fake_sync(db, organization_id, **kwargs):
        from app.services.iiko_waiter_kpi_sync import record_waiter_kpi_sync_run

        assert int(organization_id) == org_id
        await record_waiter_kpi_sync_run(db, int(organization_id), ok=True, rows_upserted=3)
        await db.commit()
        return {"ok": True, "rows_upserted": 3, "sources": ["cloud_delivery"]}

    monkeypatch.setattr("app.api.admin.waiter_kpi.sync_waiter_kpi_for_org", _fake_sync)

    sync_res = await client.post("/api/admin/analytics/waiter-kpi/sync?days=1")
    assert sync_res.status_code == 200
    assert sync_res.json()["rows_upserted"] == 3


@pytest.mark.asyncio
async def test_waiter_kpi_sync_forbidden_for_operator(asgi_memory_client) -> None:
    client, session_factory = asgi_memory_client

    async with session_factory() as db:
        org = Organization(name="Op KPI Org", slug="op-kpi-org", timezone="UTC")
        db.add(org)
        await db.flush()
        db.add(
            StaffUser(
                organization_id=org.id,
                email="kpi-op@test.kz",
                password_hash=hash_password("secret123"),
                role="operator",
                is_active=True,
            ),
        )
        await db.commit()

    login = await client.post(
        "/api/admin/auth/login",
        json={"username": "kpi-op@test.kz", "password": "secret123"},
    )
    assert login.status_code == 200

    sync_res = await client.post("/api/admin/analytics/waiter-kpi/sync?days=1")
    assert sync_res.status_code == 403

    list_res = await client.get("/api/admin/analytics/waiter-kpi")
    assert list_res.status_code == 200
