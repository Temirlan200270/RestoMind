"""ETL почасовых продаж из iiko Cloud deliveries → sales_hourly_daily."""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import IikoSyncRun, Organization, SalesHourlyDaily
from app.integrations.iiko_client import IikoClient
from app.services.iiko_waiter_kpi_sync import (
    _CLOUD_STATUSES,
    _day_iso_range,
    _is_cancelled,
    _local_date_from_order,
    _local_yesterday,
    _order_revenue,
    _org_tz,
    _parse_iso_datetime,
)
from app.services.org_iiko import org_has_iiko_in_db, resolve_org_iiko_credentials

logger = logging.getLogger(__name__)

SYNC_KIND_SALES_HOURLY = "sales_hourly_iiko"
SOURCE_IIKO = "iiko"


def _hour_from_order(order: dict[str, Any], tz: ZoneInfo) -> int | None:
    for key in ("deliveryDate", "whenCreated", "whenDelivered", "completeBefore"):
        dt = _parse_iso_datetime(order.get(key))
        if dt is not None:
            return int(dt.astimezone(tz).hour)
    return None


def aggregate_hourly_from_deliveries(
    payload: dict[str, Any],
    *,
    tz: ZoneInfo,
    default_date: date | None = None,
) -> dict[tuple[date, int], dict[str, float | int]]:
    """Bucket (local_day, hour) → orders_count, revenue_kzt."""
    out: dict[tuple[date, int], dict[str, float | int]] = defaultdict(
        lambda: {"orders_count": 0, "revenue_kzt": 0.0},
    )
    blocks = payload.get("ordersByOrganizations") or payload.get("ordersByOrganization") or []
    if isinstance(blocks, dict):
        blocks = [blocks]

    for block in blocks:
        if not isinstance(block, dict):
            continue
        for entry in block.get("orders") or []:
            if not isinstance(entry, dict):
                continue
            order = entry.get("order") if isinstance(entry.get("order"), dict) else entry
            if not isinstance(order, dict) or _is_cancelled(order.get("status")):
                continue
            kpi_date = _local_date_from_order(order, tz) or default_date
            hour = _hour_from_order(order, tz)
            if kpi_date is None or hour is None:
                continue
            key = (kpi_date, hour)
            out[key]["orders_count"] = int(out[key]["orders_count"]) + 1
            out[key]["revenue_kzt"] = float(out[key]["revenue_kzt"]) + _order_revenue(order)
    return out


async def _upsert_sales_hourly_row(
    db: AsyncSession,
    *,
    org_id: int,
    day: date,
    hour: int,
    source: str,
    orders_count: int,
    revenue_kzt: float,
    location_id: int | None = None,
) -> None:
    sql = text("""
        INSERT INTO sales_hourly_daily
            (organization_id, day, hour, source, location_id, orders_count, revenue_kzt)
        VALUES
            (:org_id, :day, :hour, :source, :location_id, :orders, :revenue)
        ON CONFLICT (organization_id, day, hour, source)
        DO UPDATE SET
            orders_count = EXCLUDED.orders_count,
            revenue_kzt = EXCLUDED.revenue_kzt,
            updated_at = CURRENT_TIMESTAMP
    """)
    await db.execute(
        sql,
        {
            "org_id": int(org_id),
            "day": day,
            "hour": int(hour),
            "source": source,
            "location_id": location_id,
            "orders": int(orders_count),
            "revenue": round(float(revenue_kzt), 2),
        },
    )


async def sync_sales_hourly_from_iiko_for_org(
    db: AsyncSession,
    org_id: int,
    *,
    days_back: int = 14,
) -> int:
    """Fetch iiko deliveries and upsert sales_hourly_daily. Returns rows upserted."""
    org = await db.get(Organization, org_id)
    if org is None or not org_has_iiko_in_db(org):
        return 0

    creds = await resolve_org_iiko_credentials(db, org_id)
    if creds is None:
        return 0

    tz = _org_tz(org)
    upserted = 0

    async with IikoClient(api_login=creds.api_login) as client:
        for offset in range(max(1, days_back)):
            local_day = _local_yesterday(tz) - timedelta(days=offset)
            date_from_iso, date_to_iso = _day_iso_range(local_day)
            try:
                payload = await client.fetch_deliveries_by_date_and_status(
                    organization_ids=[creds.iiko_organization_id],
                    date_from=date_from_iso,
                    date_to=date_to_iso,
                    statuses=list(_CLOUD_STATUSES),
                )
            except Exception:
                logger.exception("sales_hourly_iiko fetch failed org=%s day=%s", org_id, local_day)
                continue

            buckets = aggregate_hourly_from_deliveries(payload, tz=tz, default_date=local_day)
            for (day, hour), agg in buckets.items():
                await _upsert_sales_hourly_row(
                    db,
                    org_id=org_id,
                    day=day,
                    hour=hour,
                    source=SOURCE_IIKO,
                    orders_count=int(agg["orders_count"]),
                    revenue_kzt=float(agg["revenue_kzt"]),
                )
                upserted += 1

    run = IikoSyncRun(
        organization_id=int(org_id),
        sync_kind=SYNC_KIND_SALES_HOURLY,
        status="ok" if upserted >= 0 else "empty",
        records_processed=upserted,
        payload_json={"days_back": days_back},
    )
    db.add(run)
    return upserted


async def sales_hourly_iiko_scheduled_tick(_ctx: dict[str, Any] | None = None) -> None:
    from app.db.session import async_session_factory

    async with async_session_factory() as db:
        org_ids = list(
            (await db.execute(select(Organization.id).where(Organization.is_active.is_(True)))).scalars().all()
        )

    total = 0
    for org_id in org_ids:
        try:
            async with async_session_factory() as db:
                n = await sync_sales_hourly_from_iiko_for_org(db, int(org_id))
                if n:
                    await db.commit()
                total += n
        except Exception:
            logger.exception("sales_hourly_iiko tick org=%s", org_id)

    if total:
        logger.info("sales_hourly_iiko_scheduled_tick: upserted %d buckets across %d orgs", total, len(org_ids))


async def load_sales_heatmap(
    db: AsyncSession,
    org_id: int,
    *,
    days: int = 7,
    source: str = "iiko",
) -> dict[str, Any]:
    """Read sales_hourly_daily for heatmap UI."""
    since = datetime.now(tz=timezone.utc).date() - timedelta(days=max(1, days) - 1)
    rows = (
        await db.execute(
            select(SalesHourlyDaily)
            .where(
                SalesHourlyDaily.organization_id == org_id,
                SalesHourlyDaily.source == source,
                SalesHourlyDaily.day >= since,
            )
            .order_by(SalesHourlyDaily.day.asc(), SalesHourlyDaily.hour.asc())
        )
    ).scalars().all()

    matrix = [[0.0 for _ in range(24)] for _ in range(7)]
    hour_totals = [0.0 for _ in range(24)]
    total_revenue = 0.0
    total_orders = 0
    weekday_labels = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

    for row in rows:
        wd = int(row.day.weekday())
        h = int(row.hour)
        rev = float(row.revenue_kzt or 0)
        matrix[wd][h] += rev
        hour_totals[h] += rev
        total_revenue += rev
        total_orders += int(row.orders_count or 0)

    peak_hours = sorted(
        range(24),
        key=lambda hh: (hour_totals[hh], hh),
        reverse=True,
    )[:3]
    peak_hours = [h for h in peak_hours if hour_totals[h] > 0]

    return {
        "source": source,
        "days": days,
        "matrix": matrix,
        "weekday_labels": weekday_labels,
        "hour_totals": [{"hour": h, "revenue": round(hour_totals[h], 2)} for h in range(24) if hour_totals[h] > 0],
        "peak_hours_local": peak_hours,
        "total_revenue_kzt": round(total_revenue, 2),
        "total_orders": total_orders,
        "has_data": total_orders > 0,
    }
