"""Unified sales fact ETL from iiko OLAP into RestoMind analytics tables."""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import IikoSyncRun, Organization, SalesDailyAgg, SalesFactOrder, SalesHourlyDaily
from app.db.session import async_session_factory
from app.services.data_quality import (
    append_reconciliation_report,
    build_sales_facts_from_canonical,
    canonicalize_olap_sales_rows,
    record_source_snapshot,
    validate_olap_sales_rows,
    write_data_quality_report,
)
from app.services.iiko_sales_factory import resolve_iiko_sales_client
from app.services.system_events import BusinessEvent, emit_event

logger = logging.getLogger(__name__)

SYNC_KIND_OLAP_SALES = "sales_olap_iiko"
SOURCE_IIKO_OLAP = "iiko_olap"


def _row_get(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in row:
            return row[key]
    return None


def _parse_decimal(value: Any, default: Decimal = Decimal("0")) -> Decimal:
    if value is None or value == "":
        return default
    try:
        return Decimal(str(value))
    except Exception:
        return default


def _parse_int(value: Any, default: int = 0) -> int:
    if value is None or value == "":
        return default
    try:
        return int(float(str(value)))
    except Exception:
        return default


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def _olap_error_message(exc: httpx.HTTPStatusError) -> str:
    status = exc.response.status_code if exc.response is not None else 0
    body = (exc.response.text or "")[:1000] if exc.response is not None else ""
    lowered = body.lower()
    if status in (401, 403) and "reports/olap" in lowered and "not allowed" in lowered:
        return "olap_not_allowed: api/1/reports/olap is not allowed for this ApiLogin"
    if status in (401, 403) and "not allowed" in lowered:
        return f"olap_not_allowed: {body[:300]}"
    return f"olap_http_{status}: {body[:500]}"


async def sync_olap_sales_for_org(
    db: AsyncSession,
    org_id: int,
    date_from: date,
    date_to: date,
) -> int:
    """Fetch OLAP SALES, validate, canonicalize, rebuild facts and aggregates."""
    org = await db.get(Organization, int(org_id))
    resolved = await resolve_iiko_sales_client(db, int(org_id))
    if resolved is None:
        await record_olap_sales_sync_run(db, org_id, ok=False, rows=0, error_text="iiko credentials missing")
        return 0
    client, creds, data_source = resolved

    try:
        async with client as active_client:
            rows = list(await active_client.fetch_olap_sales(creds.iiko_organization_id, date_from, date_to))
    except httpx.HTTPStatusError as exc:
        message = _olap_error_message(exc)
        await record_olap_sales_sync_run(db, org_id, ok=False, rows=0, error_text=message)
        logger.warning("iiko OLAP sales sync skipped org=%s range=%s..%s: %s", org_id, date_from, date_to, message)
        return 0

    snapshot = await record_source_snapshot(
        db,
        int(org_id),
        source=SOURCE_IIKO_OLAP,
        entity_type="sales",
        rows=rows,
        date_from=date_from,
        date_to=date_to,
    )
    quality_report = validate_olap_sales_rows(rows)
    quality = await write_data_quality_report(
        db,
        int(org_id),
        snapshot_id=int(snapshot.id),
        source=SOURCE_IIKO_OLAP,
        entity_type="sales",
        report=quality_report,
    )
    canonical_stats = await canonicalize_olap_sales_rows(
        db,
        int(org_id),
        snapshot_id=int(snapshot.id),
        rows=rows,
        confidence_score=float(quality.confidence_score or 0),
        timezone_name=(org.timezone if org is not None else None),
    )
    fact_stats = await build_sales_facts_from_canonical(
        db,
        int(org_id),
        date_from=date_from,
        date_to=date_to,
        data_source=data_source,
    )
    await append_reconciliation_report(
        db,
        int(org_id),
        snapshot_id=int(snapshot.id),
        source=SOURCE_IIKO_OLAP,
        entity_type="sales",
        facts=fact_stats,
    )

    upserted = int(fact_stats.get("orders") or 0)
    await rebuild_olap_sales_aggregates(db, int(org_id), date_from, date_to)
    await record_olap_sales_sync_run(db, org_id, ok=True, rows=upserted)
    await emit_event(
        db,
        BusinessEvent(
            org_id=int(org_id),
            type="sales.synced",
            actor="system",
            entity_type="sales_olap",
            payload={
                "source": SOURCE_IIKO_OLAP,
                "data_source": data_source,
                "date_from": date_from.isoformat(),
                "date_to": date_to.isoformat(),
                "orders": upserted,
                "items": int(fact_stats.get("items") or 0),
                "data_quality": {
                    "status": quality.status,
                    "confidence_score": float(quality.confidence_score or 0),
                    "issue_count": int(quality.issue_count or 0),
                },
                "canonical": canonical_stats,
                "reconciliation": {
                    "issue_count": int(fact_stats.get("reconciliation_issue_count") or 0),
                    "issues": fact_stats.get("reconciliation_issues") or [],
                },
                "lineage": {
                    "snapshot_id": int(snapshot.id),
                    "checksum": snapshot.checksum,
                    "schema_fields_hash": (snapshot.payload_json or {}).get("schema_fields_hash"),
                },
            },
        ),
    )
    logger.info("iiko OLAP sales sync org=%s range=%s..%s orders=%s", org_id, date_from, date_to, upserted)
    return upserted


async def rebuild_olap_sales_aggregates(
    db: AsyncSession,
    org_id: int,
    date_from: date,
    date_to: date,
) -> None:
    orders = list(
        (
            await db.execute(
                select(SalesFactOrder).where(
                    SalesFactOrder.organization_id == int(org_id),
                    SalesFactOrder.order_date >= date_from,
                    SalesFactOrder.order_date <= date_to,
                ),
            )
        ).scalars().all(),
    )

    by_day: dict[date, dict[str, Decimal | int]] = defaultdict(
        lambda: {"revenue": Decimal("0"), "orders": 0, "guests": 0},
    )
    by_hour: dict[tuple[date, int], dict[str, Decimal | int]] = defaultdict(
        lambda: {"revenue": Decimal("0"), "orders": 0},
    )
    for order in orders:
        day = order.order_date
        by_day[day]["revenue"] = _parse_decimal(by_day[day]["revenue"]) + _parse_decimal(order.revenue)
        by_day[day]["orders"] = int(by_day[day]["orders"]) + 1
        by_day[day]["guests"] = int(by_day[day]["guests"]) + int(order.guest_count or 0)
        if order.closed_at is not None:
            hour_key = (day, int(order.closed_at.hour))
            by_hour[hour_key]["revenue"] = _parse_decimal(by_hour[hour_key]["revenue"]) + _parse_decimal(order.revenue)
            by_hour[hour_key]["orders"] = int(by_hour[hour_key]["orders"]) + 1

    await db.execute(
        delete(SalesDailyAgg).where(
            SalesDailyAgg.organization_id == int(org_id),
            SalesDailyAgg.date >= date_from,
            SalesDailyAgg.date <= date_to,
            SalesDailyAgg.source == SOURCE_IIKO_OLAP,
        ),
    )
    await db.execute(
        delete(SalesHourlyDaily).where(
            SalesHourlyDaily.organization_id == int(org_id),
            SalesHourlyDaily.day >= date_from,
            SalesHourlyDaily.day <= date_to,
            SalesHourlyDaily.source == SOURCE_IIKO_OLAP,
        ),
    )

    for day, metrics in by_day.items():
        revenue = _parse_decimal(metrics["revenue"])
        order_count = int(metrics["orders"])
        avg_check = revenue / order_count if order_count else Decimal("0")
        baseline = await _median_baseline(db, int(org_id), day, in_memory_daily=by_day)
        delta_pct: Decimal | None = None
        if baseline is not None and baseline > 0:
            delta_pct = ((revenue - baseline) / baseline * Decimal("100")).quantize(Decimal("0.0001"))
        db.add(
            SalesDailyAgg(
                organization_id=int(org_id),
                date=day,
                source=SOURCE_IIKO_OLAP,
                total_revenue=revenue.quantize(Decimal("0.01")),
                order_count=order_count,
                guest_count=int(metrics["guests"]),
                avg_check=avg_check.quantize(Decimal("0.01")),
                baseline_revenue=baseline,
                delta_pct=delta_pct,
            ),
        )

    for (day, hour), metrics in by_hour.items():
        db.add(
            SalesHourlyDaily(
                organization_id=int(org_id),
                day=day,
                hour=int(hour),
                source=SOURCE_IIKO_OLAP,
                orders_count=int(metrics["orders"]),
                revenue_kzt=_parse_decimal(metrics["revenue"]).quantize(Decimal("0.01")),
            ),
        )


async def _median_baseline(
    db: AsyncSession,
    org_id: int,
    target: date,
    *,
    in_memory_daily: dict[date, dict[str, Decimal | int]] | None = None,
) -> Decimal | None:
    values: list[Decimal] = []
    for weeks_ago in range(1, 5):
        past = target - timedelta(weeks=weeks_ago)
        if in_memory_daily is not None and past in in_memory_daily:
            values.append(_parse_decimal(in_memory_daily[past]["revenue"]))
            continue
        row = await db.scalar(
            select(SalesDailyAgg.total_revenue).where(
                SalesDailyAgg.organization_id == int(org_id),
                SalesDailyAgg.source == SOURCE_IIKO_OLAP,
                SalesDailyAgg.date == past,
            ),
        )
        if row is not None:
            values.append(_parse_decimal(row))
    if not values:
        return None
    values.sort()
    mid = len(values) // 2
    if len(values) % 2 == 1:
        return values[mid]
    return ((values[mid - 1] + values[mid]) / 2).quantize(Decimal("0.01"))


async def record_olap_sales_sync_run(
    db: AsyncSession,
    org_id: int,
    *,
    ok: bool,
    rows: int,
    error_text: str | None = None,
) -> None:
    db.add(
        IikoSyncRun(
            organization_id=int(org_id),
            sync_kind=SYNC_KIND_OLAP_SALES,
            status="ok" if ok else "error",
            rows_upserted=int(rows),
            error_text=(error_text or "")[:1000] or None,
        ),
    )


async def list_organizations_for_olap_sales_sync(db: AsyncSession) -> list[Organization]:
    rows = (
        await db.execute(select(Organization).where(Organization.is_active.is_(True)))
    ).scalars().all()
    return [org for org in rows if (org.iiko_organization_id or "").strip()]


async def olap_sales_scheduled_tick(_ctx: dict[str, Any] | None = None) -> None:
    async with async_session_factory() as db:
        orgs = await list_organizations_for_olap_sales_sync(db)

    today = datetime.now(tz=timezone.utc).date()
    date_from = today - timedelta(days=1)
    date_to = today
    for org in orgs:
        try:
            async with async_session_factory() as db:
                await sync_olap_sales_for_org(db, int(org.id), date_from, date_to)
                await db.commit()
        except Exception as exc:
            logger.exception("olap_sales_scheduled_tick: org_id=%s", org.id)
            try:
                async with async_session_factory() as db:
                    await record_olap_sales_sync_run(
                        db,
                        int(org.id),
                        ok=False,
                        rows=0,
                        error_text=str(exc),
                    )
                    await db.commit()
            except Exception:
                logger.exception("olap_sales_scheduled_tick audit failed org_id=%s", org.id)


async def olap_sales_backfill_org(org_id: int, *, days: int = 30) -> int:
    today = datetime.now(tz=timezone.utc).date()
    date_from = today - timedelta(days=max(1, int(days)) - 1)
    async with async_session_factory() as db:
        count = await sync_olap_sales_for_org(db, int(org_id), date_from, today)
        await db.commit()
        return count
