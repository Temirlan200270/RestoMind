"""ETL KPI офiciантов из iiko Cloud (доставки) и iiko Office (зал)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import IikoSyncRun, Organization, WaiterKpiDaily, WaiterRegistry
from app.integrations.iiko_client import IikoClient
from app.integrations.iiko_office_client import IikoOfficeClient
from app.services.org_iiko import org_has_iiko_in_db, resolve_org_iiko_credentials
from app.services.org_iiko_office import (
    OrgIikoOfficeCredentials,
    org_has_iiko_office_in_db,
    resolve_location_id_for_iiko_office_store,
    resolve_org_iiko_office_credentials,
)

if TYPE_CHECKING:
    import httpx

logger = logging.getLogger(__name__)

SYNC_KIND_WAITER_KPI = "waiter_kpi"
SOURCE_CLOUD = "cloud_delivery"
SOURCE_OFFICE = "office_report"

_CLOUD_STATUSES = (
    "Delivered",
    "Closed",
    "OnWay",
    "Waiting",
    "CookingCompleted",
    "CookingStarted",
    "ReadyForCooking",
    "WaitCooking",
    "Unconfirmed",
    "Cancelled",
)

_CANCELLED_STATUSES = frozenset({"Cancelled", "Canceled", "cancelled", "canceled"})


@dataclass
class _WaiterDayAgg:
    waiter_iiko_id: str
    waiter_name: str
    source: str
    kpi_date: date
    location_id: int | None = None
    orders_served: int = 0
    total_revenue_kzt: float = 0.0
    guests_count: int = 0
    cancelled_orders: int = 0
    service_times: list[float] = field(default_factory=list)

    @property
    def avg_check_kzt(self) -> float:
        if self.orders_served <= 0:
            return 0.0
        return round(self.total_revenue_kzt / self.orders_served, 2)

    @property
    def avg_service_time_min(self) -> float | None:
        if not self.service_times:
            return None
        return round(sum(self.service_times) / len(self.service_times), 2)


def _org_tz(org: Organization | None) -> ZoneInfo:
    name = (org.timezone if org else None) or "UTC"
    try:
        return ZoneInfo(str(name).strip() or "UTC")
    except Exception:
        return ZoneInfo("UTC")


def _parse_iso_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _local_date_from_order(order: dict[str, Any], tz: ZoneInfo) -> date | None:
    for key in ("deliveryDate", "whenCreated", "whenDelivered", "completeBefore"):
        dt = _parse_iso_datetime(order.get(key))
        if dt is not None:
            return dt.astimezone(tz).date()
    return None


def _extract_waiter_from_order(order: dict[str, Any]) -> tuple[str, str, str] | None:
    for block_key, source in (("operator", SOURCE_CLOUD), ("waiter", SOURCE_OFFICE)):
        block = order.get(block_key)
        if isinstance(block, dict):
            wid = str(block.get("id") or block.get("Id") or "").strip()
            name = str(block.get("name") or block.get("Name") or wid).strip()
            if wid:
                return wid, name, source
    courier = order.get("courierInfo")
    if isinstance(courier, dict):
        person = courier.get("courier") if isinstance(courier.get("courier"), dict) else courier
        if isinstance(person, dict):
            wid = str(person.get("id") or person.get("Id") or "").strip()
            name = str(person.get("name") or person.get("Name") or wid).strip()
            if wid:
                return wid, name, SOURCE_CLOUD
    return None


def _order_revenue(order: dict[str, Any]) -> float:
    for key in ("sum", "total"):
        val = order.get(key)
        if val is not None:
            try:
                return float(val)
            except (TypeError, ValueError):
                pass
    payment = order.get("payment")
    if isinstance(payment, dict) and payment.get("sum") is not None:
        try:
            return float(payment.get("sum"))
        except (TypeError, ValueError):
            pass
    return 0.0


def _order_guests(order: dict[str, Any]) -> int:
    for key in ("numberOfPersons", "guestsCount", "guestCount"):
        val = order.get(key)
        if val is not None:
            try:
                return max(0, int(val))
            except (TypeError, ValueError):
                pass
    return 0


def _service_time_minutes(order: dict[str, Any]) -> float | None:
    start = _parse_iso_datetime(order.get("whenCreated"))
    end = _parse_iso_datetime(order.get("completeBefore") or order.get("whenDelivered"))
    if start is None or end is None:
        return None
    delta = (end - start).total_seconds() / 60.0
    if delta < 0:
        return None
    return round(delta, 2)


def _is_cancelled(status: Any) -> bool:
    return str(status or "").strip() in _CANCELLED_STATUSES


def aggregate_cloud_deliveries(
    payload: dict[str, Any],
    *,
    tz: ZoneInfo,
    default_date: date | None = None,
) -> dict[tuple[str, date], _WaiterDayAgg]:
    """Агрегация Cloud deliveries по (waiter_id, local_date)."""
    out: dict[tuple[str, date], _WaiterDayAgg] = {}
    blocks = payload.get("ordersByOrganizations") or payload.get("ordersByOrganization") or []
    if isinstance(blocks, dict):
        blocks = [blocks]

    for block in blocks:
        if not isinstance(block, dict):
            continue
        orders = block.get("orders") or []
        for entry in orders:
            if not isinstance(entry, dict):
                continue
            order = entry.get("order") if isinstance(entry.get("order"), dict) else entry
            if not isinstance(order, dict):
                continue
            waiter = _extract_waiter_from_order(order)
            if waiter is None:
                continue
            waiter_id, waiter_name, source = waiter
            kpi_date = _local_date_from_order(order, tz) or default_date
            if kpi_date is None:
                continue
            key = (waiter_id, kpi_date)
            agg = out.get(key)
            if agg is None:
                agg = _WaiterDayAgg(
                    waiter_iiko_id=waiter_id,
                    waiter_name=waiter_name,
                    source=source,
                    kpi_date=kpi_date,
                )
                out[key] = agg
            elif waiter_name and agg.waiter_name != waiter_name:
                agg.waiter_name = waiter_name

            status = order.get("status")
            if _is_cancelled(status):
                agg.cancelled_orders += 1
                continue

            agg.orders_served += 1
            agg.total_revenue_kzt += _order_revenue(order)
            agg.guests_count += _order_guests(order)
            svc = _service_time_minutes(order)
            if svc is not None:
                agg.service_times.append(svc)

    return out


def aggregate_office_waiter_rows(
    rows: list[Any],
    *,
    kpi_date: date,
    location_id: int | None = None,
) -> dict[tuple[str, date], _WaiterDayAgg]:
    out: dict[tuple[str, date], _WaiterDayAgg] = {}
    for row in rows:
        waiter_id = str(getattr(row, "waiter_id", "") or "").strip()
        if not waiter_id:
            continue
        key = (waiter_id, kpi_date)
        orders = int(getattr(row, "orders_count", 0) or 0)
        revenue = float(getattr(row, "total_revenue", 0) or 0)
        out[key] = _WaiterDayAgg(
            waiter_iiko_id=waiter_id,
            waiter_name=str(getattr(row, "waiter_name", "") or waiter_id),
            source=SOURCE_OFFICE,
            kpi_date=kpi_date,
            location_id=location_id,
            orders_served=orders,
            total_revenue_kzt=revenue,
            guests_count=int(getattr(row, "guests_count", 0) or 0),
            cancelled_orders=int(getattr(row, "cancelled_orders", 0) or 0),
            service_times=(
                [float(getattr(row, "avg_service_time_min"))]
                if getattr(row, "avg_service_time_min", None) is not None
                else []
            ),
        )
    return out


def _merge_aggs(
    base: dict[tuple[str, date], _WaiterDayAgg],
    extra: dict[tuple[str, date], _WaiterDayAgg],
) -> dict[tuple[str, date], _WaiterDayAgg]:
    for key, row in extra.items():
        existing = base.get(key)
        if existing is None:
            base[key] = row
            continue
        existing.orders_served += row.orders_served
        existing.total_revenue_kzt += row.total_revenue_kzt
        existing.guests_count += row.guests_count
        existing.cancelled_orders += row.cancelled_orders
        existing.service_times.extend(row.service_times)
        if row.waiter_name and row.waiter_name != existing.waiter_name:
            existing.waiter_name = row.waiter_name
        if existing.location_id is None and row.location_id is not None:
            existing.location_id = row.location_id
    return base


async def _upsert_waiter_registry(
    db: AsyncSession,
    organization_id: int,
    agg: _WaiterDayAgg,
) -> None:
    row = await db.scalar(
        select(WaiterRegistry).where(
            WaiterRegistry.organization_id == organization_id,
            WaiterRegistry.waiter_iiko_id == agg.waiter_iiko_id,
        )
    )
    if row is None:
        row = WaiterRegistry(
            organization_id=organization_id,
            waiter_iiko_id=agg.waiter_iiko_id,
            waiter_name=agg.waiter_name,
            source=agg.source,
        )
        db.add(row)
    else:
        if agg.waiter_name:
            row.waiter_name = agg.waiter_name
        row.source = agg.source


async def _upsert_waiter_kpi_daily(
    db: AsyncSession,
    organization_id: int,
    agg: _WaiterDayAgg,
) -> None:
    row = await db.scalar(
        select(WaiterKpiDaily).where(
            WaiterKpiDaily.organization_id == organization_id,
            WaiterKpiDaily.kpi_date == agg.kpi_date,
            WaiterKpiDaily.waiter_iiko_id == agg.waiter_iiko_id,
        )
    )
    if row is None:
        row = WaiterKpiDaily(
            organization_id=organization_id,
            location_id=agg.location_id,
            kpi_date=agg.kpi_date,
            waiter_iiko_id=agg.waiter_iiko_id,
        )
        db.add(row)
    else:
        row.location_id = agg.location_id or row.location_id

    row.orders_served = agg.orders_served
    row.total_revenue_kzt = agg.total_revenue_kzt
    row.avg_check_kzt = agg.avg_check_kzt
    row.guests_count = agg.guests_count
    row.cancelled_orders = agg.cancelled_orders
    row.avg_service_time_min = agg.avg_service_time_min


async def record_waiter_kpi_sync_run(
    db: AsyncSession,
    organization_id: int,
    *,
    ok: bool,
    rows_upserted: int = 0,
    error_text: str | None = None,
) -> None:
    db.add(
        IikoSyncRun(
            organization_id=organization_id,
            sync_kind=SYNC_KIND_WAITER_KPI,
            status="ok" if ok else "error",
            rows_upserted=rows_upserted,
            error_text=(error_text or "")[:4000] or None,
        )
    )


def _local_yesterday(tz: ZoneInfo) -> date:
    now_local = datetime.now(tz=tz)
    return (now_local - timedelta(days=1)).date()


def _day_iso_range(local_day: date) -> tuple[str, str]:
    return (
        f"{local_day.isoformat()}T00:00:00.000",
        f"{local_day.isoformat()}T23:59:59.999",
    )


async def sync_waiter_kpi_for_org(
    db: AsyncSession,
    organization_id: int,
    *,
    days: int = 1,
    cloud_fixture_path: str | None = None,
    office_waiter_fixture_path: str | None = None,
    transport: "httpx.AsyncBaseTransport | None" = None,
) -> dict[str, Any]:
    """
    Синхронизация KPI офiciантов за последние ``days`` локальных суток org TZ.
    ``cloud_fixture_path`` / ``office_waiter_fixture_path`` — только для тестов.
    """
    org = await db.get(Organization, int(organization_id))
    if org is None:
        return {"ok": False, "error": "org_not_found"}

    tz = _org_tz(org)
    cloud_creds = await resolve_org_iiko_credentials(db, organization_id)
    office_creds = await resolve_org_iiko_office_credentials(db, organization_id)
    has_cloud = cloud_creds is not None or cloud_fixture_path is not None
    has_office = office_creds is not None or office_waiter_fixture_path is not None

    if not has_cloud and not has_office:
        return {
            "ok": False,
            "error": "iiko_not_configured",
            "detail": "Подключите iiko Cloud и/или iiko Office для KPI офiciантов.",
        }

    days = max(1, min(int(days or 1), 31))
    location_id: int | None = None
    if office_creds is not None and org is not None:
        location_id = resolve_location_id_for_iiko_office_store(org, office_creds.store_id)

    total_upserted = 0
    sources: list[str] = []

    for offset in range(days):
        local_day = _local_yesterday(tz) - timedelta(days=offset)
        date_from_iso, date_to_iso = _day_iso_range(local_day)
        aggs: dict[tuple[str, date], _WaiterDayAgg] = {}

        if has_cloud:
            if cloud_fixture_path:
                import json
                from pathlib import Path

                payload = json.loads(Path(cloud_fixture_path).read_text(encoding="utf-8"))
            elif cloud_creds is not None:
                async with IikoClient(api_login=cloud_creds.api_login) as client:
                    payload = await client.fetch_deliveries_by_date_and_status(
                        organization_ids=[cloud_creds.iiko_organization_id],
                        date_from=date_from_iso,
                        date_to=date_to_iso,
                        statuses=list(_CLOUD_STATUSES),
                    )
            else:
                payload = {}

            cloud_aggs = aggregate_cloud_deliveries(payload, tz=tz, default_date=local_day)
            _merge_aggs(aggs, cloud_aggs)
            if cloud_aggs:
                sources.append(SOURCE_CLOUD)

        if has_office and office_creds is not None:
            async with IikoOfficeClient(
                office_creds,
                transport=transport,
                authenticate_on_enter=office_waiter_fixture_path is None,
            ) as office_client:
                office_rows = await office_client.fetch_waiter_sales_report(
                    date_from=date_from_iso,
                    date_to=date_to_iso,
                    waiter_fixture_path=office_waiter_fixture_path,
                )
            office_aggs = aggregate_office_waiter_rows(
                office_rows,
                kpi_date=local_day,
                location_id=location_id,
            )
            _merge_aggs(aggs, office_aggs)
            if office_aggs:
                sources.append(SOURCE_OFFICE)
        elif office_waiter_fixture_path and office_creds is None:
            stub_creds = OrgIikoOfficeCredentials(
                host="https://office.fixture.local",
                login="u",
                password="p",
                store_id="s",
                department_id="",
            )
            async with IikoOfficeClient(
                stub_creds,
                fixture_path=office_waiter_fixture_path,
                authenticate_on_enter=False,
            ) as office_client:
                office_rows = await office_client.fetch_waiter_sales_report(
                    date_from=date_from_iso,
                    date_to=date_to_iso,
                    waiter_fixture_path=office_waiter_fixture_path,
                )
            office_aggs = aggregate_office_waiter_rows(
                office_rows,
                kpi_date=local_day,
                location_id=location_id,
            )
            _merge_aggs(aggs, office_aggs)
            if office_aggs:
                sources.append(SOURCE_OFFICE)

        for agg in aggs.values():
            await _upsert_waiter_registry(db, organization_id, agg)
            await _upsert_waiter_kpi_daily(db, organization_id, agg)
            total_upserted += 1

    await record_waiter_kpi_sync_run(
        db,
        organization_id,
        ok=True,
        rows_upserted=total_upserted,
    )
    await db.commit()

    logger.info(
        "sync_waiter_kpi_for_org org=%s days=%s rows=%s sources=%s",
        organization_id,
        days,
        total_upserted,
        sorted(set(sources)),
    )
    return {
        "ok": True,
        "days": days,
        "rows_upserted": total_upserted,
        "sources": sorted(set(sources)),
        "hall_connected": has_office,
        "delivery_connected": has_cloud,
    }


async def list_organizations_for_waiter_kpi_sync(db: AsyncSession) -> list[Organization]:
    from app.services.org_iiko import list_organizations_with_iiko_db
    from app.services.org_iiko_office import list_organizations_with_iiko_office_db

    by_id: dict[int, Organization] = {}
    for org in await list_organizations_with_iiko_db(db):
        by_id[int(org.id)] = org
    for org in await list_organizations_with_iiko_office_db(db):
        by_id[int(org.id)] = org
    return list(by_id.values())


def org_has_waiter_kpi_source(org: Organization | None) -> bool:
    return org_has_iiko_in_db(org) or org_has_iiko_office_in_db(org)
