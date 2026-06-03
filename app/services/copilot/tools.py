"""Whitelisted read-only tools for the owner AI analyst."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    InventoryStockSnapshot,
    Order,
    OrderStatus,
    OperationalInsight,
    SalesDailyAgg,
    SalesFactItem,
    SalesFactOrder,
    SalesHourlyDaily,
)
from app.services.iiko_olap_sales_sync import SOURCE_IIKO_OLAP
from app.services.data_quality import latest_data_lineage, latest_quality_status
from app.services.forecasting import build_dish_category_forecast
from app.services.organization_memory import (
    find_related_memory_events as find_memory_rows,
    list_memory_events,
    memory_event_public,
)
from app.services.restaurant_graph import (
    get_low_margin_high_revenue_dishes as graph_low_margin_high_revenue,
    get_seasonal_dish_trends as graph_seasonal_dish_trends,
    get_supplier_exposure as graph_supplier_exposure,
    simulate_price_change as graph_simulate_price_change,
)
from app.services.supplymind import recommended_order_quantity


def period_days(period: str) -> int:
    p = (period or "7d").strip().lower()
    if p in {"today", "сегодня"}:
        return 1
    if p in {"yesterday", "вчера"}:
        return 1
    if p in {"30d", "month", "месяц"}:
        return 30
    return 7


def period_range(period: str) -> tuple[Any, Any]:
    today = datetime.now(tz=timezone.utc).date()
    if (period or "").strip().lower() in {"yesterday", "вчера"}:
        return today - timedelta(days=1), today - timedelta(days=1)
    days = period_days(period)
    return today - timedelta(days=days - 1), today


async def get_revenue_summary(db: AsyncSession, org_id: int, *, period: str = "7d") -> dict[str, Any]:
    start, end = period_range(period)
    rows = (
        await db.execute(
            select(SalesDailyAgg)
            .where(
                SalesDailyAgg.organization_id == int(org_id),
                SalesDailyAgg.source == SOURCE_IIKO_OLAP,
                SalesDailyAgg.date >= start,
                SalesDailyAgg.date <= end,
            )
            .order_by(SalesDailyAgg.date.asc()),
        )
    ).scalars().all()
    revenue = sum(float(row.total_revenue or 0) for row in rows)
    orders = sum(int(row.order_count or 0) for row in rows)
    return {
        "tool": "get_revenue_summary",
        "period": period,
        "date_from": start.isoformat(),
        "date_to": end.isoformat(),
        "revenue": round(revenue, 2),
        "orders": orders,
        "avg_check": round(revenue / orders, 2) if orders else 0,
        "daily": [
            {
                "date": row.date.isoformat(),
                "revenue": round(float(row.total_revenue or 0), 2),
                "delta_pct": round(float(row.delta_pct), 2) if row.delta_pct is not None else None,
            }
            for row in rows
        ],
    }


async def get_top_dishes(db: AsyncSession, org_id: int, *, period: str = "7d", limit: int = 10) -> dict[str, Any]:
    start, end = period_range(period)
    rows = (
        await db.execute(
            select(
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
                SalesFactOrder.order_date <= end,
            )
            .group_by(SalesFactItem.product_name)
            .order_by(func.coalesce(func.sum(SalesFactItem.revenue), 0).desc())
            .limit(limit),
        )
    ).all()
    return {
        "tool": "get_top_dishes",
        "period": period,
        "items": [
            {
                "name": name,
                "quantity": round(float(qty or 0), 3),
                "revenue": round(float(revenue or 0), 2),
                "margin": round(float(revenue or 0) - float(cost or 0), 2),
            }
            for name, qty, revenue, cost in rows
        ],
    }


async def get_category_breakdown(db: AsyncSession, org_id: int, *, period: str = "7d") -> dict[str, Any]:
    start, end = period_range(period)
    rows = (
        await db.execute(
            select(
                SalesFactItem.category,
                func.coalesce(func.sum(SalesFactItem.revenue), 0),
            )
            .join(SalesFactOrder, SalesFactOrder.id == SalesFactItem.order_id)
            .where(
                SalesFactItem.organization_id == int(org_id),
                SalesFactOrder.organization_id == int(org_id),
                SalesFactOrder.order_date >= start,
                SalesFactOrder.order_date <= end,
            )
            .group_by(SalesFactItem.category)
            .order_by(func.coalesce(func.sum(SalesFactItem.revenue), 0).desc())
            .limit(12),
        )
    ).all()
    return {
        "tool": "get_category_breakdown",
        "period": period,
        "items": [{"category": cat or "Без категории", "revenue": round(float(rev or 0), 2)} for cat, rev in rows],
    }


async def compare_periods(db: AsyncSession, org_id: int, *, period: str = "7d") -> dict[str, Any]:
    current_start, current_end = period_range(period)
    days = (current_end - current_start).days + 1
    previous_end = current_start - timedelta(days=1)
    previous_start = previous_end - timedelta(days=days - 1)

    async def _sum(start: Any, end: Any) -> tuple[float, int]:
        row = (
            await db.execute(
                select(
                    func.coalesce(func.sum(SalesDailyAgg.total_revenue), 0),
                    func.coalesce(func.sum(SalesDailyAgg.order_count), 0),
                ).where(
                    SalesDailyAgg.organization_id == int(org_id),
                    SalesDailyAgg.source == SOURCE_IIKO_OLAP,
                    SalesDailyAgg.date >= start,
                    SalesDailyAgg.date <= end,
                ),
            )
        ).one()
        return float(row[0] or 0), int(row[1] or 0)

    current_revenue, current_orders = await _sum(current_start, current_end)
    previous_revenue, previous_orders = await _sum(previous_start, previous_end)

    def _pct(current: float, previous: float) -> float | None:
        if previous == 0:
            return None
        return round((current - previous) / previous * 100, 2)

    return {
        "tool": "compare_periods",
        "period": period,
        "current": {
            "date_from": current_start.isoformat(),
            "date_to": current_end.isoformat(),
            "revenue": round(current_revenue, 2),
            "orders": current_orders,
        },
        "previous": {
            "date_from": previous_start.isoformat(),
            "date_to": previous_end.isoformat(),
            "revenue": round(previous_revenue, 2),
            "orders": previous_orders,
        },
        "changes": {
            "revenue_pct": _pct(current_revenue, previous_revenue),
            "orders_pct": _pct(current_orders, previous_orders),
        },
    }


async def get_hourly_heatmap(db: AsyncSession, org_id: int, *, period: str = "7d") -> dict[str, Any]:
    start, end = period_range(period)
    rows = (
        await db.execute(
            select(
                SalesHourlyDaily.day,
                SalesHourlyDaily.hour,
                SalesHourlyDaily.orders_count,
                SalesHourlyDaily.revenue_kzt,
            ).where(
                SalesHourlyDaily.organization_id == int(org_id),
                SalesHourlyDaily.source == SOURCE_IIKO_OLAP,
                SalesHourlyDaily.day >= start,
                SalesHourlyDaily.day <= end,
            ),
        )
    ).all()
    return {
        "tool": "get_hourly_heatmap",
        "period": period,
        "items": [
            {
                "date": day.isoformat(),
                "hour": int(hour),
                "orders": int(orders or 0),
                "revenue": round(float(revenue or 0), 2),
            }
            for day, hour, orders, revenue in rows
        ],
    }


async def get_waiter_kpi(db: AsyncSession, org_id: int, *, period: str = "7d", limit: int = 10) -> dict[str, Any]:
    start, end = period_range(period)
    rows = (
        await db.execute(
            select(
                SalesFactOrder.waiter_name,
                func.count(SalesFactOrder.id),
                func.coalesce(func.sum(SalesFactOrder.revenue), 0),
                func.coalesce(func.sum(SalesFactOrder.guest_count), 0),
            )
            .where(
                SalesFactOrder.organization_id == int(org_id),
                SalesFactOrder.order_date >= start,
                SalesFactOrder.order_date <= end,
                SalesFactOrder.waiter_name.is_not(None),
                SalesFactOrder.waiter_name != "",
            )
            .group_by(SalesFactOrder.waiter_name)
            .order_by(func.coalesce(func.sum(SalesFactOrder.revenue), 0).desc())
            .limit(limit),
        )
    ).all()
    return {
        "tool": "get_waiter_kpi",
        "period": period,
        "items": [
            {
                "waiter": waiter,
                "orders": int(orders or 0),
                "revenue": round(float(revenue or 0), 2),
                "guests": int(guests or 0),
                "avg_check": round(float(revenue or 0) / int(orders or 1), 2),
            }
            for waiter, orders, revenue, guests in rows
        ],
    }


async def get_food_cost_margin(db: AsyncSession, org_id: int, *, period: str = "7d") -> dict[str, Any]:
    start, end = period_range(period)
    rows = (
        await db.execute(
            select(
                SalesFactItem.category,
                func.coalesce(func.sum(SalesFactItem.revenue), 0),
                func.coalesce(func.sum(SalesFactItem.cost), 0),
            )
            .join(SalesFactOrder, SalesFactOrder.id == SalesFactItem.order_id)
            .where(
                SalesFactItem.organization_id == int(org_id),
                SalesFactOrder.organization_id == int(org_id),
                SalesFactOrder.order_date >= start,
                SalesFactOrder.order_date <= end,
            )
            .group_by(SalesFactItem.category)
            .order_by(func.coalesce(func.sum(SalesFactItem.revenue), 0).desc()),
        )
    ).all()
    return {
        "tool": "get_food_cost_margin",
        "period": period,
        "items": [
            {
                "category": category or "Uncategorized",
                "revenue": round(float(revenue or 0), 2),
                "cost": round(float(cost or 0), 2),
                "margin": round(float(revenue or 0) - float(cost or 0), 2),
                "food_cost_pct": round(float(cost or 0) / float(revenue or 1) * 100, 2),
            }
            for category, revenue, cost in rows
        ],
    }


async def get_anomalies(db: AsyncSession, org_id: int, *, limit: int = 5) -> dict[str, Any]:
    rows = (
        await db.execute(
            select(OperationalInsight)
            .where(
                OperationalInsight.organization_id == int(org_id),
                OperationalInsight.status != "dismissed",
            )
            .order_by(OperationalInsight.created_at.desc())
            .limit(limit),
        )
    ).scalars().all()
    return {
        "tool": "get_anomalies",
        "items": [
            {
                "type": row.insight_type,
                "severity": row.severity,
                "title": row.title,
                "summary": row.summary,
                "confidence_score": round(float(row.confidence_score or 0), 4) if row.confidence_score is not None else None,
                "evidence": row.evidence_json or {},
                "drilldown": row.drilldown_json or {},
                "payload": row.payload_json or {},
            }
            for row in rows
        ],
    }


async def get_stock_alerts(db: AsyncSession, org_id: int, *, limit: int = 10) -> dict[str, Any]:
    rows = (
        await db.execute(
            select(InventoryStockSnapshot)
            .where(InventoryStockSnapshot.organization_id == int(org_id))
            .order_by(InventoryStockSnapshot.updated_at.desc())
            .limit(200),
        )
    ).scalars().all()
    alerts: list[dict[str, Any]] = []
    for row in rows:
        quantity = float(row.quantity or 0)
        min_quantity = float(row.min_quantity or 0)
        if min_quantity > 0 and quantity > min_quantity:
            continue
        alert = {
            "sku": row.sku,
            "name": row.ingredient,
            "quantity": quantity,
            "unit": row.unit or "",
            "min_quantity": min_quantity,
            "reorder_quantity": float(row.reorder_quantity) if row.reorder_quantity is not None else None,
            "daily_usage_estimate": float(row.daily_usage_estimate) if row.daily_usage_estimate is not None else None,
        }
        alert["recommended_order_quantity"] = recommended_order_quantity(alert)
        alerts.append(alert)
        if len(alerts) >= limit:
            break
    return {"tool": "get_stock_alerts", "items": alerts}


async def get_demand_forecast(db: AsyncSession, org_id: int, *, days_ahead: int = 7) -> dict[str, Any]:
    forecast = await build_dish_category_forecast(db, org_id, days_ahead=days_ahead)
    return {"tool": "get_demand_forecast", **forecast}


async def get_data_quality_status(db: AsyncSession, org_id: int) -> dict[str, Any]:
    return {"tool": "get_data_quality_status", **await latest_quality_status(db, org_id)}


async def get_data_lineage(db: AsyncSession, org_id: int, *, metric: str = "sales") -> dict[str, Any]:
    return {"tool": "get_data_lineage", "metric": metric, **await latest_data_lineage(db, org_id)}


async def get_live_sales_preview(db: AsyncSession, org_id: int) -> dict[str, Any]:
    live_statuses = [
        OrderStatus.DRAFT.value,
        OrderStatus.CONFIRMED.value,
        OrderStatus.SENT_TO_IIKO.value,
        OrderStatus.IN_TRANSIT.value,
        OrderStatus.WAITING_PICKUP.value,
    ]
    rows = (
        await db.execute(
            select(Order.status, func.count(Order.id), func.coalesce(func.sum(Order.total_price), 0))
            .where(Order.organization_id == int(org_id), Order.status.in_(live_statuses))
            .group_by(Order.status),
        )
    ).all()
    return {
        "tool": "get_live_sales_preview",
        "preliminary": True,
        "source": "orders_live_preview",
        "order_count": sum(int(count or 0) for _status, count, _revenue in rows),
        "expected_revenue": round(sum(float(revenue or 0) for _status, _count, revenue in rows), 2),
        "by_status": [
            {"status": status, "orders": int(count or 0), "expected_revenue": round(float(revenue or 0), 2)}
            for status, count, revenue in rows
        ],
    }


async def get_org_memory(db: AsyncSession, org_id: int, *, days: int = 90, limit: int = 10) -> dict[str, Any]:
    rows = await list_memory_events(db, org_id, days=days, limit=limit)
    return {"tool": "get_org_memory", "items": [memory_event_public(row) for row in rows]}


async def find_related_memory_events(
    db: AsyncSession,
    org_id: int,
    *,
    query: str,
    days: int = 180,
    limit: int = 10,
) -> dict[str, Any]:
    rows = await find_memory_rows(db, org_id, query=query, days=days, limit=limit)
    return {"tool": "find_related_memory_events", "query": query, "items": [memory_event_public(row) for row in rows]}


async def get_low_margin_high_revenue_dishes(db: AsyncSession, org_id: int, *, limit: int = 10) -> dict[str, Any]:
    return {"tool": "get_low_margin_high_revenue_dishes", **await graph_low_margin_high_revenue(db, org_id, limit=limit)}


async def simulate_price_change(
    db: AsyncSession,
    org_id: int,
    *,
    product_name: str | None = None,
    price_delta_pct: float = 5.0,
) -> dict[str, Any]:
    return {
        "tool": "simulate_price_change",
        **await graph_simulate_price_change(
            db,
            org_id,
            product_name=product_name,
            price_delta_pct=price_delta_pct,
        ),
    }


async def get_supplier_exposure(db: AsyncSession, org_id: int, *, limit: int = 10) -> dict[str, Any]:
    return {"tool": "get_supplier_exposure", **await graph_supplier_exposure(db, org_id, limit=limit)}


async def get_seasonal_dish_trends(db: AsyncSession, org_id: int, *, limit: int = 10) -> dict[str, Any]:
    return {"tool": "get_seasonal_dish_trends", **await graph_seasonal_dish_trends(db, org_id, limit=limit)}
