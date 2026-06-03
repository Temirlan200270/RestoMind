"""Food-cost enrichment from iiko product_expenses / STOCK OLAP."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import IikoSyncRun, MenuItem, SalesFactItem, SalesFactOrder
from app.services.iiko_olap_sales_sync import _parse_decimal
from app.services.iiko_sales_factory import resolve_iiko_sales_client

logger = logging.getLogger(__name__)

SYNC_KIND_FOOD_COST = "food_cost_iiko"

ID_FIELDS = (
    "DishId",
    "DishId.Id",
    "ProductId",
    "Product.Id",
    "ProductId.Id",
    "productId",
    "id",
)
NAME_FIELDS = (
    "DishName",
    "DishName.Name",
    "ProductName",
    "Product.Name",
    "productName",
    "name",
)
UNIT_COST_FIELDS = (
    "ProductCostBase.ProductCost",
    "ProductCostBase.Cost",
    "ProductCost",
    "CostPrice",
    "costPrice",
    "Cost",
    "cost",
    "price",
)
TOTAL_COST_FIELDS = (
    "ProductCostBase.Sum",
    "ProductCostBase.Total",
    "ProductCostBase",
    "Sum",
    "sum",
    "CostSum",
    "costSum",
    "totalCost",
    "TotalCost",
)
QUANTITY_FIELDS = ("Amount", "amount", "Qty", "qty", "Quantity", "quantity")


def _norm_name(value: str) -> str:
    return " ".join((value or "").strip().lower().split())


def _deep_row_get(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in row:
            return row[key]
        current: Any = row
        for part in key.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                current = None
                break
        if current is not None:
            return current
    return None


def _cost_from_row(row: dict[str, Any]) -> Decimal | None:
    raw = _deep_row_get(row, *UNIT_COST_FIELDS)
    cost = _parse_decimal(raw)
    if cost <= 0:
        total = _parse_decimal(_deep_row_get(row, *TOTAL_COST_FIELDS))
        qty = _parse_decimal(_deep_row_get(row, *QUANTITY_FIELDS))
        if total > 0 and qty > 0:
            cost = total / qty
    return cost if cost > 0 else None


async def sync_food_cost_for_org(
    db: AsyncSession,
    org_id: int,
    date_from: date,
    date_to: date,
) -> int:
    resolved = await resolve_iiko_sales_client(db, org_id)
    if resolved is None:
        await _record_run(db, org_id, ok=False, rows=0, error_text="iiko credentials missing")
        return 0
    client, creds, data_source = resolved

    try:
        async with client as active_client:
            rows = await active_client.fetch_product_expenses(creds.iiko_organization_id, date_from, date_to)
    except Exception as exc:
        await _record_run(db, org_id, ok=False, rows=0, error_text=str(exc))
        logger.warning("iiko food-cost sync org=%s source=%s failed: %s", org_id, data_source, exc)
        return 0

    costs_by_id: dict[str, Decimal] = {}
    costs_by_name: dict[str, Decimal] = {}
    rows_with_cost = 0
    for row in rows:
        cost = _cost_from_row(row)
        if cost is None:
            continue
        rows_with_cost += 1
        dish_id = str(_deep_row_get(row, *ID_FIELDS) or "").strip()
        dish_name = str(_deep_row_get(row, *NAME_FIELDS) or "").strip()
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

    warning = None
    if not rows:
        warning = f"iiko returned no product expense/STOCK rows for {date_from}..{date_to}"
    elif rows_with_cost == 0:
        warning = f"iiko returned {len(rows)} product expense/STOCK rows, but no recognized cost fields"
    elif updated == 0:
        warning = f"iiko returned {rows_with_cost} rows with cost, but none matched active menu items"

    await _record_run(db, org_id, ok=True, rows=updated, error_text=warning)
    logger.info(
        "iiko food-cost sync org=%s source=%s rows=%s rows_with_cost=%s menu_items_updated=%s warning=%s",
        org_id,
        data_source,
        len(rows),
        rows_with_cost,
        updated,
        warning,
    )
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
