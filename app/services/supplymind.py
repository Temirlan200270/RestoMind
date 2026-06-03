"""SupplyMind MVP: stock runout forecast and draft purchase orders."""

from __future__ import annotations

import csv
import io
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import InventoryStockSnapshot, SalesDailyAgg, SupplyPurchaseDraft
from app.services.owner_dashboard import build_stock_alerts_from_inventory

SUPPLY_DRAFT_STATUSES = frozenset({"draft", "approved", "completed", "cancelled"})

SUPPLY_DRAFT_TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"approved", "cancelled"}),
    "approved": frozenset({"completed", "cancelled"}),
    "completed": frozenset(),
    "cancelled": frozenset(),
}


def recommended_order_quantity(alert: dict[str, Any], *, cover_days: int = 7) -> float:
    quantity = float(alert.get("quantity") or 0)
    daily_usage = alert.get("daily_usage_estimate")
    reorder_quantity = alert.get("reorder_quantity")
    min_quantity = alert.get("min_quantity")
    if daily_usage is not None and float(daily_usage) > 0:
        target = float(daily_usage) * cover_days
    elif reorder_quantity is not None:
        target = float(reorder_quantity)
    elif min_quantity is not None:
        target = float(min_quantity) * 2
    else:
        target = max(quantity, 1.0)
    return round(max(target - quantity, 0.0), 3)


async def demand_cover_multiplier(db: AsyncSession, org_id: int, *, cover_days: int = 7) -> float:
    """Simple demand signal from OLAP revenue trend; keeps SupplyMind deterministic and testable."""
    rows = (
        await db.execute(
            select(SalesDailyAgg)
            .where(SalesDailyAgg.organization_id == org_id, SalesDailyAgg.source == "iiko_olap")
            .order_by(SalesDailyAgg.date.desc())
            .limit(14),
        )
    ).scalars().all()
    if len(rows) < 7:
        return 1.0
    recent = rows[:7]
    previous = rows[7:14]
    recent_avg = sum(float(r.total_revenue or 0) for r in recent) / max(len(recent), 1)
    previous_avg = sum(float(r.total_revenue or 0) for r in previous) / max(len(previous), 1)
    if previous_avg <= 0:
        return 1.0
    ratio = recent_avg / previous_avg
    return round(min(1.35, max(0.8, ratio)), 2)


async def build_supplymind_draft(
    db: AsyncSession,
    org_id: int,
    *,
    location_id: int | None = None,
    cover_days: int = 7,
    today: date | None = None,
) -> SupplyPurchaseDraft:
    stmt = select(InventoryStockSnapshot).where(InventoryStockSnapshot.organization_id == org_id)
    if location_id is not None:
        stmt = stmt.where(InventoryStockSnapshot.location_id == location_id)
    rows = (await db.execute(stmt.order_by(InventoryStockSnapshot.updated_at.desc()).limit(300))).scalars().all()
    alerts = build_stock_alerts_from_inventory(rows, limit=50)
    demand_multiplier = await demand_cover_multiplier(db, org_id, cover_days=cover_days)
    adjusted_cover_days = max(1, round(float(cover_days) * demand_multiplier))
    items: list[dict[str, Any]] = []
    for alert in alerts:
        qty = recommended_order_quantity(alert, cover_days=adjusted_cover_days)
        if qty <= 0:
            continue
        items.append({
            "sku": alert.get("sku"),
            "ingredient": alert.get("ingredient"),
            "unit": alert.get("unit") or "",
            "recommended_quantity": qty,
            "days_until_runout": alert.get("days_until_runout"),
            "source": alert.get("source"),
            "demand_multiplier": demand_multiplier,
            "cover_days": adjusted_cover_days,
        })

    title_day = (today or date.today()).isoformat()
    draft = SupplyPurchaseDraft(
        organization_id=org_id,
        location_id=location_id,
        status="draft",
        source="supplymind",
        title=f"Черновик закупки {title_day}",
        items_json=items,
        payload_json={
            "cover_days": cover_days,
            "adjusted_cover_days": adjusted_cover_days,
            "demand_multiplier": demand_multiplier,
            "alerts_count": len(alerts),
        },
    )
    db.add(draft)
    await db.flush()
    return draft


async def list_supply_drafts(
    db: AsyncSession,
    org_id: int,
    *,
    limit: int = 20,
) -> list[SupplyPurchaseDraft]:
    rows = (await db.execute(
        select(SupplyPurchaseDraft)
        .where(SupplyPurchaseDraft.organization_id == org_id)
        .order_by(SupplyPurchaseDraft.created_at.desc())
        .limit(limit)
    )).scalars().all()
    return list(rows)


async def get_supply_draft(
    db: AsyncSession,
    org_id: int,
    draft_id: int,
) -> SupplyPurchaseDraft | None:
    row = (await db.execute(
        select(SupplyPurchaseDraft).where(
            SupplyPurchaseDraft.id == int(draft_id),
            SupplyPurchaseDraft.organization_id == org_id,
        )
    )).scalar_one_or_none()
    return row


async def update_draft_status(
    db: AsyncSession,
    org_id: int,
    draft_id: int,
    status: str,
) -> SupplyPurchaseDraft:
    normalized = (status or "").strip().lower()
    if normalized not in SUPPLY_DRAFT_STATUSES:
        raise ValueError(f"invalid_status:{normalized}")

    draft = await get_supply_draft(db, org_id, draft_id)
    if draft is None:
        raise LookupError("draft_not_found")

    current = (draft.status or "draft").strip().lower()
    allowed = SUPPLY_DRAFT_TRANSITIONS.get(current, frozenset())
    if normalized == current:
        return draft
    if normalized not in allowed:
        raise ValueError(f"invalid_transition:{current}->{normalized}")

    draft.status = normalized
    await db.flush()
    return draft


async def update_draft_items(
    db: AsyncSession,
    org_id: int,
    draft_id: int,
    item_checks: list[dict[str, Any]],
) -> SupplyPurchaseDraft:
    """Persist per-line checked state inside items_json (SupplyMind checklist)."""
    draft = await get_supply_draft(db, org_id, draft_id)
    if draft is None:
        raise LookupError("draft_not_found")

    items = list(draft.items_json or [])
    if not items:
        return draft

    for patch in item_checks:
        if not isinstance(patch, dict):
            continue
        idx = patch.get("idx")
        if idx is None:
            continue
        try:
            i = int(idx)
        except (TypeError, ValueError):
            continue
        if i < 0 or i >= len(items):
            continue
        row = dict(items[i]) if isinstance(items[i], dict) else {}
        row["checked"] = bool(patch.get("checked"))
        items[i] = row

    draft.items_json = items
    await db.flush()
    return draft


def export_draft_csv(draft: SupplyPurchaseDraft) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow([
        "sku",
        "ingredient",
        "unit",
        "recommended_quantity",
        "days_until_runout",
        "source",
        "checked",
    ])
    for item in draft.items_json or []:
        if not isinstance(item, dict):
            continue
        writer.writerow([
            item.get("sku") or "",
            item.get("ingredient") or "",
            item.get("unit") or "",
            item.get("recommended_quantity") or "",
            item.get("days_until_runout") if item.get("days_until_runout") is not None else "",
            item.get("source") or "",
            "1" if item.get("checked") else "0",
        ])
    return buf.getvalue()


def supply_draft_public(row: SupplyPurchaseDraft) -> dict[str, Any]:
    return {
        "id": int(row.id),
        "organization_id": int(row.organization_id),
        "location_id": row.location_id,
        "status": row.status,
        "source": row.source,
        "title": row.title,
        "items": row.items_json or [],
        "payload": row.payload_json or {},
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }
