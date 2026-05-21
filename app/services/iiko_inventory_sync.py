"""Синхронизация остатков iiko Office → inventory_stock_snapshots."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.iiko_office_client import IikoOfficeClient

if TYPE_CHECKING:
    import httpx
from app.services.inventory_snapshots import InventorySnapshotUpsertItem, upsert_inventory_snapshots
from app.db.models import Organization
from app.services.org_iiko_office import (
    OrgIikoOfficeCredentials,
    resolve_location_id_for_iiko_office_store,
    resolve_org_iiko_office_credentials,
)

logger = logging.getLogger(__name__)

SOURCE_IIKO_OFFICE = "iiko_office"


async def sync_inventory_from_iiko_office(
    db: AsyncSession,
    organization_id: int,
    *,
    creds: OrgIikoOfficeCredentials | None = None,
    fixture_path: str | None = None,
    location_id: int | None = None,
    transport: "httpx.AsyncBaseTransport | None" = None,
) -> dict[str, Any]:
    """
    Загрузить остатки из iiko Office и upsert в ``inventory_stock_snapshots``.
    ``fixture_path`` — только для тестов (обход HTTP).
    """
    if creds is None:
        creds = await resolve_org_iiko_office_credentials(db, organization_id)
    if creds is None:
        raise ValueError("iiko Office credentials not configured")

    org = await db.get(Organization, int(organization_id))
    resolved_location_id = location_id
    if resolved_location_id is None and org is not None:
        resolved_location_id = resolve_location_id_for_iiko_office_store(org, creds.store_id)

    async with IikoOfficeClient(creds, fixture_path=fixture_path, transport=transport) as client:
        rows = await client.fetch_stock_balances()

    items = [
        InventorySnapshotUpsertItem(
            sku=row.sku,
            ingredient=row.name,
            quantity=row.quantity,
            unit=row.unit,
            min_quantity=row.min_quantity,
            location_id=resolved_location_id,
            source=SOURCE_IIKO_OFFICE,
            external_id=row.product_id,
            payload={"iiko_office": row.raw},
        )
        for row in rows
    ]
    updated = await upsert_inventory_snapshots(db, organization_id, items)
    stats = {
        "total": len(rows),
        "updated": updated,
        "source": SOURCE_IIKO_OFFICE,
    }
    logger.info(
        "sync_inventory_from_iiko_office org=%s total=%s updated=%s",
        organization_id,
        stats["total"],
        stats["updated"],
    )
    return stats
