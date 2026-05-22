"""Тесты ETL KPI офiciантов из iiko."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import select

from app.db.models import IikoSyncRun, Organization, WaiterKpiDaily, WaiterRegistry
from app.integrations.iiko_office_client import parse_waiter_sales_payload
from app.services.iiko_waiter_kpi_sync import (
    SOURCE_CLOUD,
    SOURCE_OFFICE,
    aggregate_cloud_deliveries,
    sync_waiter_kpi_for_org,
)
from app.services.org_iiko import OrgIikoCredentials

CLOUD_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "iiko_cloud" / "deliveries_waiter_sample.json"
OFFICE_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "iiko_office" / "waiter_sales.json"


def test_parse_waiter_sales_fixture() -> None:
    data = json.loads(OFFICE_FIXTURE.read_text(encoding="utf-8"))
    rows = parse_waiter_sales_payload(data)
    assert len(rows) == 2
    assert rows[0].waiter_id == "waiter-001"
    assert rows[0].orders_count == 12


def test_aggregate_cloud_deliveries_fixture() -> None:
    from zoneinfo import ZoneInfo

    payload = json.loads(CLOUD_FIXTURE.read_text(encoding="utf-8"))
    aggs = aggregate_cloud_deliveries(
        payload,
        tz=ZoneInfo("Asia/Almaty"),
        default_date=date(2026, 5, 21),
    )
    assert ("op-77", date(2026, 5, 21)) in aggs
    madina = aggs[("op-77", date(2026, 5, 21))]
    assert madina.orders_served == 1
    assert madina.cancelled_orders == 1
    assert madina.total_revenue_kzt == 12500
    assert ("courier-5", date(2026, 5, 21)) in aggs
    assert aggs[("courier-5", date(2026, 5, 21))].source == SOURCE_CLOUD


@pytest.mark.asyncio
async def test_sync_waiter_kpi_cloud_fixture_idempotent(db_session, monkeypatch) -> None:
    org = Organization(name="Waiter KPI Org", slug="waiter-kpi-org", timezone="Asia/Almaty")
    db_session.add(org)
    await db_session.flush()
    org_id = int(org.id)

    async def fake_resolve(_db, _oid):
        return OrgIikoCredentials(
            api_login="login",
            iiko_organization_id="uuid",
            terminal_group_id="",
        )

    monkeypatch.setattr(
        "app.services.iiko_waiter_kpi_sync.resolve_org_iiko_credentials",
        fake_resolve,
    )

    result1 = await sync_waiter_kpi_for_org(
        db_session,
        org_id,
        days=1,
        cloud_fixture_path=str(CLOUD_FIXTURE),
    )
    assert result1["ok"] is True
    assert result1["rows_upserted"] >= 2

    result2 = await sync_waiter_kpi_for_org(
        db_session,
        org_id,
        days=1,
        cloud_fixture_path=str(CLOUD_FIXTURE),
    )
    assert result2["ok"] is True

    rows = (
        await db_session.execute(
            select(WaiterKpiDaily).where(WaiterKpiDaily.organization_id == org_id)
        )
    ).scalars().all()
    assert len(rows) >= 2
    assert len({(r.kpi_date, r.waiter_iiko_id) for r in rows}) == len(rows)


@pytest.mark.asyncio
async def test_sync_waiter_kpi_office_fixture(db_session) -> None:
    org = Organization(
        name="Waiter Office Org",
        slug="waiter-office-org",
        timezone="Asia/Almaty",
        integration_config_json={
            "iiko_office": {
                "host": "https://office.test.local",
                "login": "u",
                "password": "p",
                "store_id": "store-1",
            },
        },
    )
    db_session.add(org)
    await db_session.flush()

    result = await sync_waiter_kpi_for_org(
        db_session,
        int(org.id),
        days=1,
        office_waiter_fixture_path=str(OFFICE_FIXTURE),
    )
    assert result["ok"] is True
    assert SOURCE_OFFICE in result["sources"]

    row = await db_session.scalar(
        select(WaiterKpiDaily).where(
            WaiterKpiDaily.organization_id == org.id,
            WaiterKpiDaily.waiter_iiko_id == "waiter-001",
        )
    )
    assert row is not None
    assert int(row.orders_served) == 12
    assert float(row.total_revenue_kzt) == 185000

    reg = await db_session.scalar(
        select(WaiterRegistry).where(
            WaiterRegistry.organization_id == org.id,
            WaiterRegistry.waiter_iiko_id == "waiter-001",
        )
    )
    assert reg is not None
    assert reg.source == SOURCE_OFFICE
    assert reg.waiter_name == "Айгуль"


@pytest.mark.asyncio
async def test_sync_waiter_kpi_tenant_isolation(db_session, monkeypatch) -> None:
    org_a = Organization(name="Org A", slug="org-a-wkpi", timezone="UTC")
    org_b = Organization(name="Org B", slug="org-b-wkpi", timezone="UTC")
    db_session.add_all([org_a, org_b])
    await db_session.flush()

    async def fake_resolve(_db, oid):
        if int(oid) == int(org_a.id):
            return OrgIikoCredentials(api_login="l", iiko_organization_id="u", terminal_group_id="")
        return None

    monkeypatch.setattr(
        "app.services.iiko_waiter_kpi_sync.resolve_org_iiko_credentials",
        fake_resolve,
    )

    await sync_waiter_kpi_for_org(
        db_session,
        int(org_a.id),
        days=1,
        cloud_fixture_path=str(CLOUD_FIXTURE),
    )

    org_b_rows = (
        await db_session.execute(
            select(WaiterKpiDaily).where(WaiterKpiDaily.organization_id == org_b.id)
        )
    ).scalars().all()
    assert org_b_rows == []


@pytest.mark.asyncio
async def test_sync_records_iiko_sync_run(db_session, monkeypatch) -> None:
    org = Organization(name="Audit Org", slug="audit-wkpi", timezone="UTC")
    db_session.add(org)
    await db_session.flush()

    async def fake_resolve(_db, _oid):
        return OrgIikoCredentials(api_login="l", iiko_organization_id="u", terminal_group_id="")

    monkeypatch.setattr(
        "app.services.iiko_waiter_kpi_sync.resolve_org_iiko_credentials",
        fake_resolve,
    )

    await sync_waiter_kpi_for_org(
        db_session,
        int(org.id),
        days=1,
        cloud_fixture_path=str(CLOUD_FIXTURE),
    )

    run = await db_session.scalar(
        select(IikoSyncRun).where(IikoSyncRun.organization_id == org.id)
    )
    assert run is not None
    assert run.status == "ok"
    assert run.rows_upserted >= 1
