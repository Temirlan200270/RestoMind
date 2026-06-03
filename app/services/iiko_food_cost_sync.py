"""Food-cost enrichment from iiko product_expenses / STOCK OLAP."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import IikoSyncRun, MenuItem, SalesFactItem, SalesFactOrder
from app.integrations.iiko_client import IikoClient
from app.services.iiko_olap_sales_sync import _parse_decimal, _row_get
from app.services.org_iiko import resolve_org_iiko_credentials

logger = logging.getLogger(__name__)

SYNC_KIND_FOOD_COST = "food_cost_iiko"


def _norm_name(value: str) -> str:
    return " ".join((value or "").strip().lower().split())


def _cost_from_row(row: dict[str, Any]) -> Decimal | None:
    raw = _row_get(row, "ProductCostBase.ProductCost", "ProductCost", "Cost", "cost")
    cost = _parse_decimal(raw)
    return cost if cost > 0 else None


async def sync_food_cost_for_org(
    db: AsyncSession,
    org_id: int,
    date_from: date,
    date_to: date,
) -> int:
    creds = await resolve_org_iiko_credentials(db, org_id)
    if creds is None:
        await _record_run(db, org_id, ok=False, rows=0, error_text="iiko cloud credentials missing")
        return 0

    async with IikoClient(api_login=creds.api_login) as client:
        rows = await client.fetch_product_expenses(creds.iiko_organization_id, date_from, date_to)

    costs_by_id: dict[str, Decimal] = {}
    costs_by_name: dict[str, Decimal] = {}
    for row in rows:
        cost = _cost_from_row(row)
        if cost is None:
            continue
        dish_id = str(_row_get(row, "DishId", "ProductId", "productId") or "").strip()
        dish_name = str(_row_get(row, "DishName", "ProductName", "productName") or "").strip()
        if dish_id:
            costs_by_id[dish_id] = cost
        name_key = _norm_name(dish_name)
        if name_key:
            costs_by_name[name_key] = cost

    updated = 0
    menu_items = (
        await db.execute(
            select(MenuItem).where(
                MenuItem.organization_id == int(org_id),
                MenuItem.is_archived.is_(False),
            ),
        )
    ).scalars().all()
    for item in menu_items:
        cost = None
        if item.iiko_id:
            cost = costs_by_id.get(str(item.iiko_id))
        if cost is None:
            cost = costs_by_name.get(_norm_name(item.name))
        if cost is not None:
            item.cost_price = cost.quantize(Decimal("0.01"))
            updated += 1

    fact_items = (
        await db.execute(
            select(SalesFactItem)
            .join(SalesFactOrder, SalesFactOrder.id == SalesFactItem.order_id)
            .where(
                SalesFactItem.organization_id == int(org_id),
                SalesFactOrder.order_date >= date_from,
                SalesFactOrder.order_date <= date_to,
            ),
        )
    ).scalars().all()
    for item in fact_items:
        cost = None
        if item.product_id:
            cost = costs_by_id.get(str(item.product_id))
        if cost is None:
            cost = costs_by_name.get(_norm_name(item.product_name))
        if cost is not None:
            qty = _parse_decimal(item.quantity, Decimal("1"))
            item.cost = (cost * max(qty, Decimal("1"))).quantize(Decimal("0.01"))

    await _record_run(db, org_id, ok=True, rows=updated)
    logger.info("iiko food-cost sync org=%s menu_items_updated=%s", org_id, updated)
    return updated


async def _record_run(
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
            sync_kind=SYNC_KIND_FOOD_COST,
            status="ok" if ok else "error",
            rows_upserted=int(rows),
            error_text=(error_text or "")[:1000] or None,
        ),
    )


async def food_cost_scheduled_tick(_ctx: dict[str, Any] | None = None) -> None:
    """Daily food-cost enrichment from iiko product_expenses / STOCK OLAP."""
    from sqlalchemy import select as _select

    from app.db.models import Organization
    from app.db.session import async_session_factory

    today = datetime.now(tz=timezone.utc).date()
    date_from = today - timedelta(days=14)
    date_to = today
    async with async_session_factory() as db:
        org_ids = list(
            (await db.execute(_select(Organization.id).where(Organization.is_active.is_(True)))).scalars().all(),
        )
    for org_id in org_ids:
        async with async_session_factory() as db:
            try:
                await sync_food_cost_for_org(db, int(org_id), date_from, date_to)
                await db.commit()
            except Exception as exc:
                logger.exception("food_cost_scheduled_tick failed org=%s", org_id)
                await _record_run(db, int(org_id), ok=False, rows=0, error_text=str(exc))
                await db.commit()
