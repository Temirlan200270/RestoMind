"""Restaurant knowledge graph read models and simulations."""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    DishIngredient,
    DishMarginProfile,
    DishSeasonalityProfile,
    IngredientSupplier,
    InventoryStockSnapshot,
    MenuItem,
    SalesFactItem,
    SalesFactOrder,
)
from app.services.organization_memory import record_memory_event


_TOKEN_SPLIT_RE = re.compile(r"[,;\n]+")
_SKU_RE = re.compile(r"[^a-z0-9а-яё]+", flags=re.IGNORECASE)


def _dish_product_id(menu: MenuItem) -> str:
    return str(menu.iiko_id or menu.id)


def _ingredient_sku(name: str) -> str:
    normalized = _SKU_RE.sub("-", name.strip().lower()).strip("-")
    return normalized[:120] or "ingredient"


def _ingredient_tokens(summary: str | None) -> list[str]:
    raw = str(summary or "").strip()
    if not raw:
        return []
    seen: set[str] = set()
    tokens: list[str] = []
    for part in _TOKEN_SPLIT_RE.split(raw):
        token = re.sub(r"\s+", " ", part).strip(" .:-")
        if not token:
            continue
        key = token.lower()
        if key in seen:
            continue
        seen.add(key)
        tokens.append(token[:255])
    return tokens[:40]


async def rebuild_restaurant_graph_profiles(
    db: AsyncSession,
    org_id: int,
    *,
    days: int = 30,
) -> dict[str, Any]:
    """Rebuild dish ingredient, margin and seasonality profiles from current facts/menu data."""
    org_id_i = int(org_id)
    today = datetime.now(tz=timezone.utc).date()
    start = today - timedelta(days=max(1, int(days)))

    menu_rows = (
        await db.execute(
            select(MenuItem).where(
                MenuItem.organization_id == org_id_i,
                MenuItem.is_archived.is_(False),
            ),
        )
    ).scalars().all()
    menu_by_pid = {_dish_product_id(row): row for row in menu_rows}

    sales_rows = (
        await db.execute(
            select(
                SalesFactOrder.order_date,
                SalesFactItem.product_id,
                SalesFactItem.product_name,
                SalesFactItem.category,
                func.coalesce(func.sum(SalesFactItem.quantity), 0),
                func.coalesce(func.sum(SalesFactItem.revenue), 0),
            )
            .join(SalesFactOrder, SalesFactOrder.id == SalesFactItem.order_id)
            .where(
                SalesFactItem.organization_id == org_id_i,
                SalesFactOrder.organization_id == org_id_i,
                SalesFactOrder.order_date >= start,
                SalesFactOrder.order_date <= today,
            )
            .group_by(
                SalesFactOrder.order_date,
                SalesFactItem.product_id,
                SalesFactItem.product_name,
                SalesFactItem.category,
            ),
        )
    ).all()

    revenue_by_pid: dict[str, float] = defaultdict(float)
    quantity_by_pid: dict[str, float] = defaultdict(float)
    names_by_pid: dict[str, str] = {}
    categories_by_pid: dict[str, str] = {}
    weekday_dish: dict[tuple[str, int], list[tuple[float, float]]] = defaultdict(list)
    weekday_category: dict[tuple[str, int], list[tuple[float, float]]] = defaultdict(list)
    for day, product_id, product_name, category, qty, revenue in sales_rows:
        pid = str(product_id or product_name or "unknown")
        name = str(product_name or pid)
        cat = str(category or "Без категории")
        qty_f = float(qty or 0)
        rev_f = float(revenue or 0)
        names_by_pid[pid] = name
        categories_by_pid[pid] = cat
        revenue_by_pid[pid] += rev_f
        quantity_by_pid[pid] += qty_f
        weekday = day.weekday()
        weekday_dish[(pid, weekday)].append((qty_f, rev_f))
        weekday_category[(cat, weekday)].append((qty_f, rev_f))

    await db.execute(delete(DishIngredient).where(DishIngredient.organization_id == org_id_i))

    ingredient_count = 0
    for menu in menu_rows:
        ingredients = _ingredient_tokens(menu.ingredients_summary)
        if not ingredients:
            continue
        cost_price = float(menu.cost_price) if menu.cost_price is not None else None
        unit_cost = round(cost_price / len(ingredients), 4) if cost_price is not None and ingredients else None
        for ingredient_name in ingredients:
            db.add(
                DishIngredient(
                    organization_id=org_id_i,
                    dish_product_id=_dish_product_id(menu),
                    dish_name=menu.name,
                    ingredient_sku=_ingredient_sku(ingredient_name),
                    ingredient_name=ingredient_name,
                    quantity=1,
                    unit="portion",
                    unit_cost=unit_cost,
                    payload_json={"source": "menu.ingredients_summary"},
                ),
            )
            ingredient_count += 1

    # Supplier exposure from inventory payloads, when an external source provides supplier metadata.
    inventory_rows = (
        await db.execute(select(InventoryStockSnapshot).where(InventoryStockSnapshot.organization_id == org_id_i))
    ).scalars().all()
    supplier_count = 0
    for row in inventory_rows:
        payload = row.payload_json or {}
        supplier_name = str(payload.get("supplier_name") or payload.get("supplier") or "").strip()
        if not supplier_name:
            continue
        existing = await db.scalar(
            select(IngredientSupplier).where(
                IngredientSupplier.organization_id == org_id_i,
                IngredientSupplier.ingredient_sku == str(row.sku),
                IngredientSupplier.supplier_name == supplier_name,
            ),
        )
        data = {
            "ingredient_name": row.ingredient,
            "supplier_external_id": payload.get("supplier_id") or payload.get("supplier_external_id"),
            "lead_time_days": int(payload["lead_time_days"]) if payload.get("lead_time_days") is not None else None,
            "risk_score": float(payload.get("risk_score") or 0.35),
            "payload_json": {"source": row.source, "inventory_snapshot_id": int(row.id)},
        }
        if existing is None:
            db.add(
                IngredientSupplier(
                    organization_id=org_id_i,
                    ingredient_sku=str(row.sku),
                    supplier_name=supplier_name,
                    **data,
                ),
            )
            await record_memory_event(
                db,
                org_id_i,
                event_type="supplier_change",
                entity_type="ingredient_supplier",
                entity_id=f"{row.sku}:{supplier_name}",
                summary=f"Supplier linked: {supplier_name} supplies {row.ingredient}.",
                payload={
                    "ingredient_sku": str(row.sku),
                    "ingredient": row.ingredient,
                    "supplier_name": supplier_name,
                    "source": row.source,
                },
                source="inventory_graph",
                confidence_score=0.75,
            )
            supplier_count += 1
        else:
            for key, value in data.items():
                setattr(existing, key, value)

    profile_pids = set(menu_by_pid) | set(revenue_by_pid)
    for pid in sorted(profile_pids):
        menu = menu_by_pid.get(pid)
        name = names_by_pid.get(pid) or (menu.name if menu is not None else pid)
        category = categories_by_pid.get(pid) or (menu.category if menu is not None else None)
        revenue_30d = float(revenue_by_pid.get(pid, 0))
        quantity_30d = float(quantity_by_pid.get(pid, 0))
        avg_price = revenue_30d / quantity_30d if quantity_30d else (float(menu.price or 0) if menu else None)
        estimated_cost = float(menu.cost_price) if menu is not None and menu.cost_price is not None else None
        margin_pct = None
        if estimated_cost is not None and avg_price and avg_price > 0:
            margin_pct = (avg_price - estimated_cost) / avg_price * 100
        confidence = 0.85 if estimated_cost is not None and quantity_30d >= 10 else 0.65 if estimated_cost is not None else 0.35
        profile_data = {
            "dish_name": name,
            "category": category,
            "avg_price": round(avg_price, 2) if avg_price is not None else None,
            "estimated_cost": round(estimated_cost, 2) if estimated_cost is not None else None,
            "margin_pct": round(margin_pct, 4) if margin_pct is not None else None,
            "revenue_30d": round(revenue_30d, 2),
            "confidence_score": confidence,
            "payload_json": {"source": "graph_rebuild", "quantity_30d": round(quantity_30d, 3)},
        }
        existing_profile = await db.scalar(
            select(DishMarginProfile).where(
                DishMarginProfile.organization_id == org_id_i,
                DishMarginProfile.dish_product_id == pid,
            ),
        )
        if existing_profile is None:
            db.add(DishMarginProfile(organization_id=org_id_i, dish_product_id=pid, **profile_data))
        else:
            for key, value in profile_data.items():
                setattr(existing_profile, key, value)

    async def upsert_seasonality(
        *,
        entity_type: str,
        entity_id: str,
        entity_name: str,
        period_key: str,
        expected_quantity: float,
        expected_revenue: float,
        confidence_score: float,
        basis_days: int,
    ) -> None:
        data = {
            "entity_name": entity_name,
            "expected_quantity": expected_quantity,
            "expected_revenue": expected_revenue,
            "confidence_score": confidence_score,
            "payload_json": {"basis_days": basis_days, "source": "sales_fact_items"},
        }
        existing = await db.scalar(
            select(DishSeasonalityProfile).where(
                DishSeasonalityProfile.organization_id == org_id_i,
                DishSeasonalityProfile.entity_type == entity_type,
                DishSeasonalityProfile.entity_id == entity_id,
                DishSeasonalityProfile.period_key == period_key,
            ),
        )
        if existing is None:
            db.add(
                DishSeasonalityProfile(
                    organization_id=org_id_i,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    period_key=period_key,
                    **data,
                ),
            )
        else:
            for key, value in data.items():
                setattr(existing, key, value)

    seasonality_count = 0
    for (pid, weekday), values in weekday_dish.items():
        if not values:
            continue
        await upsert_seasonality(
            entity_type="dish",
            entity_id=pid,
            entity_name=names_by_pid.get(pid, pid),
            period_key=f"weekday:{weekday}",
            expected_quantity=round(sum(v[0] for v in values) / len(values), 3),
            expected_revenue=round(sum(v[1] for v in values) / len(values), 2),
            confidence_score=round(min(0.9, 0.35 + len(values) / 12), 4),
            basis_days=len(values),
        )
        seasonality_count += 1
    for (category, weekday), values in weekday_category.items():
        if not values:
            continue
        await upsert_seasonality(
            entity_type="category",
            entity_id=category,
            entity_name=category,
            period_key=f"weekday:{weekday}",
            expected_quantity=round(sum(v[0] for v in values) / len(values), 3),
            expected_revenue=round(sum(v[1] for v in values) / len(values), 2),
            confidence_score=round(min(0.9, 0.35 + len(values) / 12), 4),
            basis_days=len(values),
        )
        seasonality_count += 1

    await db.flush()
    return {
        "dish_ingredients": ingredient_count,
        "ingredient_suppliers": supplier_count,
        "dish_margin_profiles": len(profile_pids),
        "dish_seasonality_profiles": seasonality_count,
        "source": "canonical_sales_facts",
    }


async def get_low_margin_high_revenue_dishes(
    db: AsyncSession,
    org_id: int,
    *,
    limit: int = 10,
) -> dict[str, Any]:
    today = datetime.now(tz=timezone.utc).date()
    start = today - timedelta(days=30)
    profile_rows = (
        await db.execute(
            select(DishMarginProfile)
            .where(DishMarginProfile.organization_id == int(org_id))
            .order_by(DishMarginProfile.revenue_30d.desc().nullslast(), DishMarginProfile.margin_pct.asc().nullsfirst())
            .limit(max(1, int(limit))),
        )
    ).scalars().all()
    if profile_rows:
        return {
            "items": [
                {
                    "product_id": row.dish_product_id,
                    "name": row.dish_name,
                    "category": row.category,
                    "revenue_30d": round(float(row.revenue_30d or 0), 2),
                    "margin_pct": round(float(row.margin_pct or 0), 2) if row.margin_pct is not None else None,
                    "confidence_score": round(float(row.confidence_score or 0), 4) if row.confidence_score is not None else None,
                }
                for row in profile_rows
            ],
            "source": "dish_margin_profile",
        }

    rows = (
        await db.execute(
            select(
                SalesFactItem.product_id,
                SalesFactItem.product_name,
                SalesFactItem.category,
                func.coalesce(func.sum(SalesFactItem.revenue), 0),
                func.coalesce(func.sum(SalesFactItem.cost), 0),
            )
            .join(SalesFactOrder, SalesFactOrder.id == SalesFactItem.order_id)
            .where(
                SalesFactItem.organization_id == int(org_id),
                SalesFactOrder.organization_id == int(org_id),
                SalesFactOrder.order_date >= start,
            )
            .group_by(SalesFactItem.product_id, SalesFactItem.product_name, SalesFactItem.category)
            .order_by(func.coalesce(func.sum(SalesFactItem.revenue), 0).desc())
            .limit(max(1, int(limit)) * 3),
        )
    ).all()
    items = []
    for product_id, name, category, revenue, cost in rows:
        revenue_f = float(revenue or 0)
        cost_f = float(cost or 0)
        margin_pct = (revenue_f - cost_f) / revenue_f * 100 if revenue_f else None
        if margin_pct is None or margin_pct >= 45:
            continue
        items.append(
            {
                "product_id": product_id,
                "name": name,
                "category": category,
                "revenue_30d": round(revenue_f, 2),
                "margin_pct": round(margin_pct, 2),
                "confidence_score": 0.55 if cost_f else 0.35,
            },
        )
        if len(items) >= limit:
            break
    return {"items": items, "source": "sales_fact_items"}


async def simulate_price_change(
    db: AsyncSession,
    org_id: int,
    *,
    product_id: str | None = None,
    product_name: str | None = None,
    price_delta_pct: float = 5.0,
) -> dict[str, Any]:
    today = datetime.now(tz=timezone.utc).date()
    start = today - timedelta(days=30)
    stmt = (
        select(
            SalesFactItem.product_id,
            SalesFactItem.product_name,
            func.coalesce(func.sum(SalesFactItem.quantity), 0),
            func.coalesce(func.sum(SalesFactItem.revenue), 0),
            func.coalesce(func.sum(SalesFactItem.cost), 0),
        )
        .join(SalesFactOrder, SalesFactOrder.id == SalesFactItem.order_id)
        .where(
            SalesFactItem.organization_id == int(org_id),
            SalesFactOrder.organization_id == int(org_id),
            SalesFactOrder.order_date >= start,
        )
        .group_by(SalesFactItem.product_id, SalesFactItem.product_name)
        .order_by(func.coalesce(func.sum(SalesFactItem.revenue), 0).desc())
    )
    if product_id:
        stmt = stmt.where(SalesFactItem.product_id == product_id)
    elif product_name:
        stmt = stmt.where(SalesFactItem.product_name.ilike(f"%{product_name}%"))
    row = (await db.execute(stmt.limit(1))).first()
    if row is None:
        menu = await db.scalar(
            select(MenuItem).where(
                MenuItem.organization_id == int(org_id),
                MenuItem.is_archived.is_(False),
                MenuItem.name.ilike(f"%{product_name or ''}%"),
            ).limit(1),
        )
        if menu is None:
            return {"found": False, "message": "Блюдо не найдено в продажах или меню."}
        base_price = float(menu.price or 0)
        return {
            "found": True,
            "product_id": menu.iiko_id or str(menu.id),
            "name": menu.name,
            "current_avg_price": round(base_price, 2),
            "new_price": round(base_price * (1 + float(price_delta_pct) / 100), 2),
            "estimated_revenue_delta_30d": 0,
            "confidence_score": 0.25,
            "note": "Нет продаж за 30 дней, симуляция только по цене меню.",
        }
    product_id_r, name, qty, revenue, cost = row
    qty_f = float(qty or 0)
    revenue_f = float(revenue or 0)
    avg_price = revenue_f / qty_f if qty_f else 0
    new_price = avg_price * (1 + float(price_delta_pct) / 100)
    # Conservative elasticity: every +10% price reduces quantity by 3%.
    elasticity_qty = max(0.5, 1 - (float(price_delta_pct) / 10 * 0.03))
    new_revenue = new_price * qty_f * elasticity_qty
    new_margin = new_revenue - float(cost or 0)
    return {
        "found": True,
        "product_id": product_id_r,
        "name": name,
        "current_avg_price": round(avg_price, 2),
        "new_price": round(new_price, 2),
        "quantity_30d": round(qty_f, 3),
        "revenue_30d": round(revenue_f, 2),
        "estimated_revenue_delta_30d": round(new_revenue - revenue_f, 2),
        "estimated_margin_30d": round(new_margin, 2),
        "confidence_score": 0.6 if qty_f >= 30 else 0.4,
    }


async def get_supplier_exposure(db: AsyncSession, org_id: int, *, limit: int = 10) -> dict[str, Any]:
    rows = (
        await db.execute(
            select(
                IngredientSupplier.supplier_name,
                func.count(DishIngredient.dish_product_id.distinct()),
                func.coalesce(func.avg(IngredientSupplier.risk_score), 0),
            )
            .join(
                DishIngredient,
                (DishIngredient.organization_id == IngredientSupplier.organization_id)
                & (DishIngredient.ingredient_sku == IngredientSupplier.ingredient_sku),
                isouter=True,
            )
            .where(IngredientSupplier.organization_id == int(org_id))
            .group_by(IngredientSupplier.supplier_name)
            .order_by(func.count(DishIngredient.dish_product_id.distinct()).desc())
            .limit(max(1, int(limit))),
        )
    ).all()
    return {
        "items": [
            {
                "supplier": supplier,
                "dish_count": int(dish_count or 0),
                "risk_score": round(float(risk or 0), 4),
            }
            for supplier, dish_count, risk in rows
        ],
    }


async def get_seasonal_dish_trends(db: AsyncSession, org_id: int, *, limit: int = 10) -> dict[str, Any]:
    rows = (
        await db.execute(
            select(DishSeasonalityProfile)
            .where(DishSeasonalityProfile.organization_id == int(org_id))
            .order_by(DishSeasonalityProfile.expected_revenue.desc().nullslast())
            .limit(max(1, int(limit))),
        )
    ).scalars().all()
    return {
        "items": [
            {
                "entity_type": row.entity_type,
                "entity_id": row.entity_id,
                "name": row.entity_name,
                "period_key": row.period_key,
                "expected_quantity": round(float(row.expected_quantity or 0), 3),
                "expected_revenue": round(float(row.expected_revenue or 0), 2),
                "confidence_score": round(float(row.confidence_score or 0), 4) if row.confidence_score is not None else None,
            }
            for row in rows
        ],
    }
