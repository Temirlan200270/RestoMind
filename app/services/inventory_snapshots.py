"""Upsert inventory stock snapshots (shared by admin bulk API and iiko Office sync)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import InventoryStockSnapshot


@dataclass(frozen=True)
class InventorySnapshotUpsertItem:
    sku: str
    ingredient: str
    quantity: float
    unit: str = ""
    min_quantity: float | None = None
    reorder_quantity: float | None = None
    daily_usage_estimate: float | None = None
    location_id: int | None = None
    source: str = "manual"
    external_id: str | None = None
    payload: dict[str, Any] | None = None


async def upsert_inventory_snapshots(
    db: AsyncSession,
    organization_id: int,
    items: list[InventorySnapshotUpsertItem],
) -> int:
    """Upsert rows by (org, location_id, source, sku). Returns count of items processed."""
    updated = 0
    for item in items:
        row = await db.scalar(
            select(InventoryStockSnapshot).where(
                InventoryStockSnapshot.organization_id == organization_id,
                InventoryStockSnapshot.location_id == item.location_id,
                InventoryStockSnapshot.source == item.source,
                InventoryStockSnapshot.sku == item.sku,
            )
        )
        if row is None:
            row = InventoryStockSnapshot(
                organization_id=organization_id,
                location_id=item.location_id,
                source=item.source,
                sku=item.sku,
            )
            db.add(row)
        row.ingredient = item.ingredient
        row.quantity = item.quantity
        row.unit = item.unit or ""
        row.min_quantity = item.min_quantity
        row.reorder_quantity = item.reorder_quantity
        row.daily_usage_estimate = item.daily_usage_estimate
        row.external_id = item.external_id
        row.payload_json = item.payload
        updated += 1
    return updated
