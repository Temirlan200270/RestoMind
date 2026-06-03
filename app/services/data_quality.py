"""Data quality and canonicalization layer for Intelligence OS."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    CanonicalProduct,
    CanonicalSalesItem,
    CanonicalSalesOrder,
    DataQualityReport,
    SalesFactItem,
    SalesFactOrder,
    SourceDataSnapshot,
)
from app.services.timezones import zoneinfo_or_default

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


def _parse_datetime(value: Any, timezone_name: str | None = None) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        local_zone = zoneinfo_or_default(timezone_name).zone
        return parsed.replace(tzinfo=local_zone).astimezone(timezone.utc)
    return parsed.astimezone(timezone.utc)


def checksum_payload(payload: Any) -> str:
    normalized = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def schema_fields_hash(rows: list[dict[str, Any]]) -> str:
    fields = sorted({str(key) for row in rows[:200] for key in row.keys()})
    return checksum_payload(fields)


def validate_olap_sales_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    required_missing = 0
    invalid_count = 0
    missing_by_field = defaultdict(int)
    duplicate_keys: set[tuple[str, str, str]] = set()
    seen_keys: set[tuple[str, str, str]] = set()
    issues: list[dict[str, Any]] = []
    quarantine: list[dict[str, Any]] = []

    for idx, row in enumerate(rows):
        order_id = str(_row_get(row, "UniqOrderId.Id", "OrderId", "UniqOrderId") or "").strip()
        product_id = str(_row_get(row, "DishId", "ProductId") or "").strip()
        product_name = str(_row_get(row, "DishName", "ProductName") or "").strip()
        order_date_raw = _row_get(row, "OpenDate.Typed", "OpenDate")
        order_date = _parse_date(order_date_raw)
        category = str(_row_get(row, "DishCategory", "Category") or "").strip()
        quantity = _parse_decimal(_row_get(row, "DishAmountInt", "DishAmount"), Decimal("0"))
        revenue = _parse_decimal(_row_get(row, "DishDiscountSumInt", "DishDiscountSum", "Revenue", "DishSumInt"))

        for field, value in (
            ("order_id", order_id),
            ("product_name", product_name),
            ("order_date", order_date),
            ("category", category),
        ):
            if value in (None, ""):
                required_missing += 1
                missing_by_field[field] += 1
                if len(issues) < 20:
                    issues.append({"row": idx, "type": "missing_required", "field": field})

        if product_id == "":
            missing_by_field["product_id"] += 1
        if quantity < 0 or revenue < 0:
            invalid_count += 1
            if len(issues) < 20:
                issues.append({"row": idx, "type": "invalid_value", "fields": ["quantity", "revenue"]})
        if order_date is None and order_date_raw not in (None, ""):
            invalid_count += 1
            if len(issues) < 20:
                issues.append({"row": idx, "type": "invalid_date", "value": str(order_date_raw)})

        dedupe_key = (order_id, product_id or product_name, str(_row_get(row, "CloseTime") or order_date_raw or ""))
        if order_id and dedupe_key in seen_keys:
            duplicate_keys.add(dedupe_key)
            if len(quarantine) < 50:
                quarantine.append({"row": idx, "type": "duplicate", "dedupe_key": list(dedupe_key), "sample": row})
        seen_keys.add(dedupe_key)

    row_count = len(rows)
    duplicate_count = len(duplicate_keys)
    issue_count = required_missing + invalid_count + duplicate_count
    if row_count == 0:
        confidence_score = 0.0
    else:
        penalty = (
            min(0.45, required_missing / max(row_count * 4, 1))
            + min(0.25, duplicate_count / max(row_count, 1))
            + min(0.20, invalid_count / max(row_count, 1))
        )
        confidence_score = max(0.0, min(1.0, 1.0 - penalty))

    status = "ok"
    if confidence_score < 0.7:
        status = "low_confidence"
    elif issue_count > 0:
        status = "partial"

    return {
        "status": status,
        "confidence_score": round(confidence_score, 4),
        "row_count": row_count,
        "issue_count": issue_count,
        "required_missing": required_missing,
        "duplicate_count": duplicate_count,
        "invalid_count": invalid_count,
        "missing_by_field": dict(missing_by_field),
        "issues_sample": issues,
        "quarantine_sample": quarantine,
    }


async def record_source_snapshot(
    db: AsyncSession,
    org_id: int,
    *,
    source: str,
    entity_type: str,
    rows: list[dict[str, Any]],
    date_from: date | None = None,
    date_to: date | None = None,
) -> SourceDataSnapshot:
    snapshot = SourceDataSnapshot(
        organization_id=int(org_id),
        source=source,
        entity_type=entity_type,
        date_from=date_from,
        date_to=date_to,
        row_count=len(rows),
        checksum=checksum_payload(rows),
        payload_json={
            "rows": rows,
            "schema_fields": sorted({str(key) for row in rows[:200] for key in row.keys()}),
            "schema_fields_hash": schema_fields_hash(rows),
        },
    )
    db.add(snapshot)
    await db.flush()
    return snapshot


async def write_data_quality_report(
    db: AsyncSession,
    org_id: int,
    *,
    snapshot_id: int | None,
    source: str,
    entity_type: str,
    report: dict[str, Any],
) -> DataQualityReport:
    row = DataQualityReport(
        organization_id=int(org_id),
        snapshot_id=snapshot_id,
        source=source,
        entity_type=entity_type,
        status=str(report.get("status") or "ok"),
        confidence_score=float(report.get("confidence_score") or 0),
        row_count=int(report.get("row_count") or 0),
        issue_count=int(report.get("issue_count") or 0),
        required_missing=int(report.get("required_missing") or 0),
        duplicate_count=int(report.get("duplicate_count") or 0),
        invalid_count=int(report.get("invalid_count") or 0),
        payload_json=report,
    )
    db.add(row)
    await db.flush()
    return row


async def canonicalize_olap_sales_rows(
    db: AsyncSession,
    org_id: int,
    *,
    snapshot_id: int,
    rows: list[dict[str, Any]],
    confidence_score: float,
    timezone_name: str | None = None,
) -> dict[str, int]:
    orders_by_id: dict[str, dict[str, Any]] = {}
    items_by_order: dict[str, list[dict[str, Any]]] = defaultdict(list)
    products: dict[str, dict[str, Any]] = {}
    seen_line_keys: set[tuple[str, str, str, str]] = set()

    for row in rows:
        order_id = str(_row_get(row, "UniqOrderId.Id", "OrderId", "UniqOrderId") or "").strip()
        order_date = _parse_date(_row_get(row, "OpenDate.Typed", "OpenDate"))
        if not order_id or order_date is None:
            continue
        product_id = str(_row_get(row, "DishId", "ProductId") or "").strip()
        product_name = str(_row_get(row, "DishName", "ProductName") or "").strip() or "Без названия"
        line_key = (
            order_id,
            product_id or product_name,
            str(_row_get(row, "CloseTime") or _row_get(row, "OpenDate.Typed", "OpenDate") or ""),
            str(_row_get(row, "DishDiscountSumInt", "DishDiscountSum", "Revenue", "DishSumInt") or ""),
        )
        if line_key in seen_line_keys:
            continue
        seen_line_keys.add(line_key)
        category = str(_row_get(row, "DishCategory", "Category") or "").strip() or None
        revenue = _parse_decimal(_row_get(row, "DishDiscountSumInt", "DishDiscountSum", "Revenue", "DishSumInt"))
        quantity = _parse_decimal(_row_get(row, "DishAmountInt", "DishAmount"), Decimal("0"))

        order = orders_by_id.setdefault(
            order_id,
            {
                "source_order_id": order_id,
                "order_date": order_date,
                "closed_at": _parse_datetime(_row_get(row, "CloseTime"), timezone_name=timezone_name),
                "revenue": Decimal("0"),
                "guest_count": _parse_int(_row_get(row, "GuestNum")),
                "waiter_name": str(_row_get(row, "WaiterName", "WaiterId") or "") or None,
                "order_type": str(_row_get(row, "OrderType") or "") or None,
                "origin_name": str(_row_get(row, "OriginName", "Origin") or "") or None,
                "payload_json": {"source_rows": 0},
            },
        )
        order["revenue"] = _parse_decimal(order["revenue"]) + revenue
        order["guest_count"] = max(int(order["guest_count"] or 0), _parse_int(_row_get(row, "GuestNum")))
        order["payload_json"]["source_rows"] = int(order["payload_json"]["source_rows"]) + 1

        if product_id:
            products[product_id] = {
                "source_product_id": product_id,
                "name": product_name,
                "category": category,
                "payload_json": {"last_seen_order_id": order_id},
            }
        items_by_order[order_id].append(
            {
                "source_product_id": product_id or None,
                "product_name": product_name,
                "category": category,
                "quantity": quantity,
                "revenue": revenue,
                "payload_json": row,
            },
        )

    for product_id, data in products.items():
        product = await db.scalar(
            select(CanonicalProduct).where(
                CanonicalProduct.organization_id == int(org_id),
                CanonicalProduct.source == SOURCE_IIKO_OLAP,
                CanonicalProduct.source_product_id == product_id,
            ),
        )
        if product is None:
            product = CanonicalProduct(
                organization_id=int(org_id),
                source=SOURCE_IIKO_OLAP,
                confidence_score=confidence_score,
                **data,
            )
            db.add(product)
        else:
            product.name = data["name"]
            product.category = data["category"]
            product.confidence_score = confidence_score
            product.payload_json = data["payload_json"]

    canonical_orders = 0
    canonical_items = 0
    for order_id, data in orders_by_id.items():
        order = await db.scalar(
            select(CanonicalSalesOrder).where(
                CanonicalSalesOrder.organization_id == int(org_id),
                CanonicalSalesOrder.source == SOURCE_IIKO_OLAP,
                CanonicalSalesOrder.source_order_id == order_id,
            ),
        )
        if order is None:
            order = CanonicalSalesOrder(
                organization_id=int(org_id),
                snapshot_id=int(snapshot_id),
                source=SOURCE_IIKO_OLAP,
                confidence_score=confidence_score,
                **data,
            )
            db.add(order)
            await db.flush()
        else:
            for key, value in data.items():
                setattr(order, key, value)
            order.snapshot_id = int(snapshot_id)
            order.confidence_score = confidence_score
            await db.flush()

        await db.execute(
            delete(CanonicalSalesItem).where(
                CanonicalSalesItem.organization_id == int(org_id),
                CanonicalSalesItem.canonical_order_id == int(order.id),
            ),
        )
        for item in items_by_order.get(order_id, []):
            db.add(
                CanonicalSalesItem(
                    organization_id=int(org_id),
                    snapshot_id=int(snapshot_id),
                    canonical_order_id=int(order.id),
                    source=SOURCE_IIKO_OLAP,
                    confidence_score=confidence_score,
                    **item,
                ),
            )
            canonical_items += 1
        canonical_orders += 1
    await db.flush()
    return {"canonical_orders": canonical_orders, "canonical_items": canonical_items, "canonical_products": len(products)}


async def latest_quality_status(
    db: AsyncSession,
    org_id: int,
    *,
    source: str = SOURCE_IIKO_OLAP,
    entity_type: str = "sales",
) -> dict[str, Any]:
    row = await db.scalar(
        select(DataQualityReport)
        .where(
            DataQualityReport.organization_id == int(org_id),
            DataQualityReport.source == source,
            DataQualityReport.entity_type == entity_type,
        )
        .order_by(DataQualityReport.created_at.desc(), DataQualityReport.id.desc())
        .limit(1),
    )
    if row is None:
        return {
            "status": "never",
            "confidence_score": 0.0,
            "row_count": 0,
            "issue_count": 0,
            "message": "Данные еще не проходили проверку качества.",
        }
    return {
        "status": row.status,
        "confidence_score": round(float(row.confidence_score or 0), 4),
        "row_count": int(row.row_count or 0),
        "issue_count": int(row.issue_count or 0),
        "required_missing": int(row.required_missing or 0),
        "duplicate_count": int(row.duplicate_count or 0),
        "invalid_count": int(row.invalid_count or 0),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "details": row.payload_json or {},
    }


async def build_sales_facts_from_canonical(
    db: AsyncSession,
    org_id: int,
    *,
    date_from: date,
    date_to: date,
    data_source: str = SOURCE_IIKO_OLAP,
) -> dict[str, Any]:
    orders = list(
        (
            await db.execute(
                select(CanonicalSalesOrder)
                .where(
                    CanonicalSalesOrder.organization_id == int(org_id),
                    CanonicalSalesOrder.source == SOURCE_IIKO_OLAP,
                    CanonicalSalesOrder.order_date >= date_from,
                    CanonicalSalesOrder.order_date <= date_to,
                )
                .order_by(CanonicalSalesOrder.order_date.asc(), CanonicalSalesOrder.id.asc()),
            )
        ).scalars().all(),
    )
    await db.execute(
        delete(SalesFactItem).where(
            SalesFactItem.organization_id == int(org_id),
            SalesFactItem.order_id.in_(
                select(SalesFactOrder.id).where(
                    SalesFactOrder.organization_id == int(org_id),
                    SalesFactOrder.order_date >= date_from,
                    SalesFactOrder.order_date <= date_to,
                ),
            ),
        ),
    )

    upserted_orders = 0
    upserted_items = 0
    reconciliation_issues: list[dict[str, Any]] = []
    for canonical in orders:
        order_data = {
            "organization_id": int(org_id),
            "snapshot_id": canonical.snapshot_id,
            "canonical_order_id": int(canonical.id),
            "iiko_order_id": canonical.source_order_id,
            "order_date": canonical.order_date,
            "closed_at": canonical.closed_at,
            "revenue": _parse_decimal(canonical.revenue),
            "guest_count": int(canonical.guest_count or 0),
            "waiter_name": canonical.waiter_name,
            "order_type": canonical.order_type,
            "source": canonical.origin_name,
            "data_source": data_source,
            "raw_json": {
                "canonical_order_id": int(canonical.id),
                "snapshot_id": canonical.snapshot_id,
                "confidence_score": float(canonical.confidence_score or 0),
                "payload": canonical.payload_json or {},
            },
        }
        order = await db.scalar(
            select(SalesFactOrder).where(
                SalesFactOrder.organization_id == int(org_id),
                SalesFactOrder.iiko_order_id == canonical.source_order_id,
            ),
        )
        if order is None:
            order = SalesFactOrder(**order_data)
            db.add(order)
            await db.flush()
        else:
            for key, value in order_data.items():
                setattr(order, key, value)
            await db.flush()

        items = list(
            (
                await db.execute(
                    select(CanonicalSalesItem).where(
                        CanonicalSalesItem.organization_id == int(org_id),
                        CanonicalSalesItem.canonical_order_id == int(canonical.id),
                    ),
                )
            ).scalars().all(),
        )
        item_sum = Decimal("0")
        for item in items:
            item_revenue = _parse_decimal(item.revenue)
            item_sum += item_revenue
            db.add(
                SalesFactItem(
                    organization_id=int(org_id),
                    order_id=int(order.id),
                    snapshot_id=item.snapshot_id,
                    canonical_item_id=int(item.id),
                    product_id=item.source_product_id,
                    product_name=item.product_name,
                    category=item.category,
                    quantity=_parse_decimal(item.quantity),
                    revenue=item_revenue,
                    cost=_parse_decimal(item.cost) if item.cost is not None else None,
                    raw_json={
                        "canonical_item_id": int(item.id),
                        "snapshot_id": item.snapshot_id,
                        "confidence_score": float(item.confidence_score or 0),
                        "payload": item.payload_json or {},
                    },
                ),
            )
            upserted_items += 1
        order_revenue = _parse_decimal(canonical.revenue)
        if abs(order_revenue - item_sum) > Decimal("0.01"):
            reconciliation_issues.append(
                {
                    "order_id": canonical.source_order_id,
                    "canonical_order_id": int(canonical.id),
                    "order_revenue": float(order_revenue),
                    "items_revenue": float(item_sum),
                    "delta": float(item_sum - order_revenue),
                },
            )
        upserted_orders += 1
    await db.flush()
    return {
        "orders": upserted_orders,
        "items": upserted_items,
        "reconciliation_issues": reconciliation_issues[:100],
        "reconciliation_issue_count": len(reconciliation_issues),
    }


async def append_reconciliation_report(
    db: AsyncSession,
    org_id: int,
    *,
    snapshot_id: int | None,
    source: str,
    entity_type: str,
    facts: dict[str, Any],
) -> DataQualityReport | None:
    issue_count = int(facts.get("reconciliation_issue_count") or 0)
    if issue_count <= 0:
        return None
    return await write_data_quality_report(
        db,
        int(org_id),
        snapshot_id=snapshot_id,
        source=source,
        entity_type=entity_type,
        report={
            "status": "reconciliation_warning",
            "confidence_score": 0.75,
            "row_count": int(facts.get("items") or 0),
            "issue_count": issue_count,
            "required_missing": 0,
            "duplicate_count": 0,
            "invalid_count": issue_count,
            "reconciliation_issues": facts.get("reconciliation_issues") or [],
        },
    )


async def latest_data_lineage(
    db: AsyncSession,
    org_id: int,
    *,
    source: str = SOURCE_IIKO_OLAP,
    entity_type: str = "sales",
) -> dict[str, Any]:
    snapshot = await db.scalar(
        select(SourceDataSnapshot)
        .where(
            SourceDataSnapshot.organization_id == int(org_id),
            SourceDataSnapshot.source == source,
            SourceDataSnapshot.entity_type == entity_type,
        )
        .order_by(SourceDataSnapshot.created_at.desc(), SourceDataSnapshot.id.desc())
        .limit(1),
    )
    quality = await latest_quality_status(db, int(org_id), source=source, entity_type=entity_type)
    if snapshot is None:
        return {"status": "never", "quality": quality, "snapshot": None}
    canonical_orders = await db.scalar(
        select(func.count(CanonicalSalesOrder.id)).where(
            CanonicalSalesOrder.organization_id == int(org_id),
            CanonicalSalesOrder.snapshot_id == int(snapshot.id),
        ),
    )
    canonical_items = await db.scalar(
        select(func.count(CanonicalSalesItem.id)).where(
            CanonicalSalesItem.organization_id == int(org_id),
            CanonicalSalesItem.snapshot_id == int(snapshot.id),
        ),
    )
    fact_orders = await db.scalar(
        select(func.count(SalesFactOrder.id)).where(
            SalesFactOrder.organization_id == int(org_id),
            SalesFactOrder.snapshot_id == int(snapshot.id),
        ),
    )
    fact_items = await db.scalar(
        select(func.count(SalesFactItem.id)).where(
            SalesFactItem.organization_id == int(org_id),
            SalesFactItem.snapshot_id == int(snapshot.id),
        ),
    )
    payload = snapshot.payload_json or {}
    return {
        "status": "ok",
        "snapshot": {
            "id": int(snapshot.id),
            "source": snapshot.source,
            "entity_type": snapshot.entity_type,
            "date_from": snapshot.date_from.isoformat() if snapshot.date_from else None,
            "date_to": snapshot.date_to.isoformat() if snapshot.date_to else None,
            "row_count": int(snapshot.row_count or 0),
            "checksum": snapshot.checksum,
            "schema_fields_hash": payload.get("schema_fields_hash"),
            "schema_fields": payload.get("schema_fields") or [],
            "created_at": snapshot.created_at.isoformat() if snapshot.created_at else None,
        },
        "quality": quality,
        "counts": {
            "canonical_orders": int(canonical_orders or 0),
            "canonical_items": int(canonical_items or 0),
            "fact_orders": int(fact_orders or 0),
            "fact_items": int(fact_items or 0),
        },
    }
