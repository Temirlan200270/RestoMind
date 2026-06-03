"""Forecasting v2 over canonical/fact sales data."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DishSeasonalityProfile, SalesFactItem, SalesFactOrder
from app.services.data_quality import latest_quality_status
from app.services.organization_memory import find_related_memory_events, memory_event_public


async def build_dish_category_forecast(
    db: AsyncSession,
    org_id: int,
    *,
    days_ahead: int = 7,
) -> dict[str, Any]:
    today = datetime.now(tz=timezone.utc).date()
    history_start = today - timedelta(days=90)
    rows = (
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
                SalesFactItem.organization_id == int(org_id),
                SalesFactOrder.organization_id == int(org_id),
                SalesFactOrder.order_date >= history_start,
                SalesFactOrder.order_date < today + timedelta(days=1),
            )
            .group_by(
                SalesFactOrder.order_date,
                SalesFactItem.product_id,
                SalesFactItem.product_name,
                SalesFactItem.category,
            ),
        )
    ).all()

    by_dish_weekday: dict[tuple[str, int], list[tuple[float, float]]] = defaultdict(list)
    by_category_weekday: dict[tuple[str, int], list[tuple[float, float]]] = defaultdict(list)
    dish_names: dict[str, str] = {}
    dish_categories: dict[str, str] = {}
    for day, product_id, product_name, category, qty, revenue in rows:
        key = str(product_id or product_name or "unknown")
        weekday = day.weekday()
        dish_names[key] = str(product_name or "Без названия")
        dish_categories[key] = str(category or "Без категории")
        by_dish_weekday[(key, weekday)].append((float(qty or 0), float(revenue or 0)))
        by_category_weekday[(str(category or "Без категории"), weekday)].append((float(qty or 0), float(revenue or 0)))

    future_days = [today + timedelta(days=i) for i in range(1, max(1, int(days_ahead)) + 1)]
    seasonality_rows = (
        await db.execute(
            select(DishSeasonalityProfile).where(
                DishSeasonalityProfile.organization_id == int(org_id),
                DishSeasonalityProfile.period_key.in_([f"weekday:{day.weekday()}" for day in future_days]),
            ),
        )
    ).scalars().all()
    seasonality_by_dish = {
        (row.entity_id, row.period_key): row for row in seasonality_rows if row.entity_type == "dish"
    }
    seasonality_by_category = {
        (row.entity_id, row.period_key): row for row in seasonality_rows if row.entity_type == "category"
    }
    seasonality_profiles_used = 0

    dish_forecast: dict[str, dict[str, Any]] = {}
    for (dish_id, weekday), values in by_dish_weekday.items():
        count_future = sum(1 for day in future_days if day.weekday() == weekday)
        if count_future == 0:
            continue
        avg_qty = sum(v[0] for v in values) / max(len(values), 1)
        avg_revenue = sum(v[1] for v in values) / max(len(values), 1)
        profile = seasonality_by_dish.get((dish_id, f"weekday:{weekday}"))
        source = "sales_fact_weekday"
        item_confidence = min(0.85, 0.35 + len(values) / 16)
        if profile is not None:
            avg_qty = float(profile.expected_quantity or avg_qty)
            avg_revenue = float(profile.expected_revenue or avg_revenue)
            item_confidence = max(item_confidence, float(profile.confidence_score or 0))
            source = "dish_seasonality_profile"
            seasonality_profiles_used += 1
        item = dish_forecast.setdefault(
            dish_id,
            {
                "product_id": dish_id,
                "name": dish_names.get(dish_id, dish_id),
                "category": dish_categories.get(dish_id),
                "forecast_quantity": 0.0,
                "forecast_revenue": 0.0,
                "basis_days": 0,
                "forecast_source": source,
                "confidence_score": 0.0,
            },
        )
        item["forecast_quantity"] += avg_qty * count_future
        item["forecast_revenue"] += avg_revenue * count_future
        item["basis_days"] += len(values)
        item["confidence_score"] = max(float(item.get("confidence_score") or 0), item_confidence)
        if source == "dish_seasonality_profile":
            item["forecast_source"] = source

    category_forecast: dict[str, dict[str, Any]] = {}
    for (category, weekday), values in by_category_weekday.items():
        count_future = sum(1 for day in future_days if day.weekday() == weekday)
        if count_future == 0:
            continue
        avg_qty = sum(v[0] for v in values) / max(len(values), 1)
        avg_revenue = sum(v[1] for v in values) / max(len(values), 1)
        profile = seasonality_by_category.get((category, f"weekday:{weekday}"))
        source = "sales_fact_weekday"
        item_confidence = min(0.85, 0.35 + len(values) / 16)
        if profile is not None:
            avg_qty = float(profile.expected_quantity or avg_qty)
            avg_revenue = float(profile.expected_revenue or avg_revenue)
            item_confidence = max(item_confidence, float(profile.confidence_score or 0))
            source = "dish_seasonality_profile"
            seasonality_profiles_used += 1
        item = category_forecast.setdefault(
            category,
            {
                "category": category,
                "forecast_quantity": 0.0,
                "forecast_revenue": 0.0,
                "basis_days": 0,
                "forecast_source": source,
                "confidence_score": 0.0,
            },
        )
        item["forecast_quantity"] += avg_qty * count_future
        item["forecast_revenue"] += avg_revenue * count_future
        item["basis_days"] += len(values)
        item["confidence_score"] = max(float(item.get("confidence_score") or 0), item_confidence)
        if source == "dish_seasonality_profile":
            item["forecast_source"] = source

    quality = await latest_quality_status(db, int(org_id), source="iiko_olap", entity_type="sales")
    memory_rows = await find_related_memory_events(
        db,
        int(org_id),
        query="акция цена меню поставщик campaign price menu supplier",
        days=60,
        limit=5,
    )
    basis_days = len({day for day, *_rest in rows})
    base_confidence = min(0.9, basis_days / 45)
    quality_confidence = float(quality.get("confidence_score") or 0)
    confidence_score = round(max(0.05, min(0.95, base_confidence * max(0.25, quality_confidence))), 4)
    dirty_data_weight = round(max(0.25, min(1.0, quality_confidence or 0.25)), 4)

    dishes = sorted(dish_forecast.values(), key=lambda x: float(x["forecast_revenue"]), reverse=True)[:20]
    categories = sorted(category_forecast.values(), key=lambda x: float(x["forecast_revenue"]), reverse=True)[:12]
    for item in dishes + categories:
        item["forecast_quantity"] = round(float(item["forecast_quantity"]), 3)
        item["forecast_revenue"] = round(float(item["forecast_revenue"]), 2)
        item["confidence_score"] = round(float(item.get("confidence_score") or confidence_score) * dirty_data_weight, 4)

    return {
        "days_ahead": int(days_ahead),
        "date_from": future_days[0].isoformat(),
        "date_to": future_days[-1].isoformat(),
        "forecast_revenue": round(sum(float(x["forecast_revenue"]) for x in categories), 2),
        "confidence_score": confidence_score,
        "confidence": "low" if confidence_score < 0.5 else "medium" if confidence_score < 0.8 else "high",
        "basis_days": basis_days,
        "data_quality": quality,
        "dirty_data_weighting": {
            "weight": dirty_data_weight,
            "reason": "quality_confidence",
            "partial_data": quality_confidence < 0.75,
        },
        "seasonality_profiles_used": seasonality_profiles_used,
        "memory_adjustments": [memory_event_public(row) for row in memory_rows],
        "dishes": dishes,
        "categories": categories,
    }
