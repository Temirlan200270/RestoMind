"""Sales anomaly detection on the unified sales fact layer."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import OperationalInsight, SalesDailyAgg, SalesFactItem, SalesFactOrder, SalesHourlyDaily
from app.services.data_quality import latest_quality_status
from app.services.iiko_olap_sales_sync import SOURCE_IIKO_OLAP


def _actions_for_sales_delta(delta_pct: float) -> list[str]:
    actions = ["Проверить топовые блюда, стоп-лист и доступность ключевых позиций за день"]
    if delta_pct <= -20:
        actions.append("Сравнить категории, блюда и часы продаж с обычным аналогичным днём недели")
    if delta_pct <= -30:
        actions.append("Подготовить короткую акцию или push по прибыльным позициям")
    return actions


def _as_float(value: Any) -> float:
    return float(value or 0)


def _round_money(value: Any) -> float:
    return round(_as_float(value), 2)


def _baseline_days_for(day: date) -> list[date]:
    return [day - timedelta(days=7 * idx) for idx in range(1, 5)]


async def _current_item_rows(
    db: AsyncSession,
    org_id: int,
    day: date,
    *,
    by_dish: bool,
) -> dict[str, dict[str, Any]]:
    fields = [
        SalesFactItem.product_name if by_dish else SalesFactItem.category,
        SalesFactItem.category,
        func.coalesce(func.sum(SalesFactItem.quantity), 0),
        func.coalesce(func.sum(SalesFactItem.revenue), 0),
    ]
    group_by = [SalesFactItem.product_name, SalesFactItem.category] if by_dish else [SalesFactItem.category]
    rows = (
        await db.execute(
            select(*fields)
            .join(SalesFactOrder, SalesFactOrder.id == SalesFactItem.order_id)
            .where(
                SalesFactItem.organization_id == int(org_id),
                SalesFactOrder.organization_id == int(org_id),
                SalesFactOrder.order_date == day,
            )
            .group_by(*group_by),
        )
    ).all()
    result: dict[str, dict[str, Any]] = {}
    for name_or_category, category, quantity, revenue in rows:
        name = str(name_or_category or ("Без категории" if not by_dish else "Без названия"))
        key = name if not by_dish else f"{name}::{category or ''}"
        result[key] = {
            "name": name,
            "category": str(category or "Без категории"),
            "quantity": _as_float(quantity),
            "revenue": _as_float(revenue),
        }
    return result


async def _baseline_item_rows(
    db: AsyncSession,
    org_id: int,
    days: list[date],
    *,
    by_dish: bool,
) -> dict[str, dict[str, Any]]:
    if not days:
        return {}
    fields = [
        SalesFactItem.product_name if by_dish else SalesFactItem.category,
        SalesFactItem.category,
        func.coalesce(func.sum(SalesFactItem.quantity), 0),
        func.coalesce(func.sum(SalesFactItem.revenue), 0),
    ]
    group_by = [SalesFactItem.product_name, SalesFactItem.category] if by_dish else [SalesFactItem.category]
    rows = (
        await db.execute(
            select(*fields)
            .join(SalesFactOrder, SalesFactOrder.id == SalesFactItem.order_id)
            .where(
                SalesFactItem.organization_id == int(org_id),
                SalesFactOrder.organization_id == int(org_id),
                SalesFactOrder.order_date.in_(days),
            )
            .group_by(*group_by),
        )
    ).all()
    divisor = max(1, len(days))
    result: dict[str, dict[str, Any]] = {}
    for name_or_category, category, quantity, revenue in rows:
        name = str(name_or_category or ("Без категории" if not by_dish else "Без названия"))
        key = name if not by_dish else f"{name}::{category or ''}"
        result[key] = {
            "name": name,
            "category": str(category or "Без категории"),
            "baseline_quantity": _as_float(quantity) / divisor,
            "baseline_revenue": _as_float(revenue) / divisor,
        }
    return result


def _merge_item_drilldown(
    current: dict[str, dict[str, Any]],
    baseline: dict[str, dict[str, Any]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key in set(current) | set(baseline):
        cur = current.get(key, {})
        base = baseline.get(key, {})
        quantity = _as_float(cur.get("quantity"))
        revenue = _as_float(cur.get("revenue"))
        baseline_quantity = _as_float(base.get("baseline_quantity"))
        baseline_revenue = _as_float(base.get("baseline_revenue"))
        rows.append(
            {
                "name": str(cur.get("name") or base.get("name") or key),
                "category": str(cur.get("category") or base.get("category") or "Без категории"),
                "quantity": round(quantity, 3),
                "revenue": _round_money(revenue),
                "baseline_quantity": round(baseline_quantity, 3),
                "baseline_revenue": _round_money(baseline_revenue),
                "quantity_delta": round(quantity - baseline_quantity, 3),
                "revenue_delta": _round_money(revenue - baseline_revenue),
            },
        )
    return sorted(rows, key=lambda item: (item["revenue_delta"], -abs(item["revenue"])))[:limit]


async def _hour_drilldown_for_day(
    db: AsyncSession,
    org_id: int,
    day: date,
    baseline_days: list[date],
) -> list[dict[str, Any]]:
    current_rows = (
        await db.execute(
            select(
                SalesHourlyDaily.hour,
                func.coalesce(func.sum(SalesHourlyDaily.orders_count), 0),
                func.coalesce(func.sum(SalesHourlyDaily.revenue_kzt), 0),
            )
            .where(
                SalesHourlyDaily.organization_id == int(org_id),
                SalesHourlyDaily.source == SOURCE_IIKO_OLAP,
                SalesHourlyDaily.day == day,
            )
            .group_by(SalesHourlyDaily.hour),
        )
    ).all()
    baseline_rows = (
        await db.execute(
            select(
                SalesHourlyDaily.hour,
                func.coalesce(func.sum(SalesHourlyDaily.orders_count), 0),
                func.coalesce(func.sum(SalesHourlyDaily.revenue_kzt), 0),
            )
            .where(
                SalesHourlyDaily.organization_id == int(org_id),
                SalesHourlyDaily.source == SOURCE_IIKO_OLAP,
                SalesHourlyDaily.day.in_(baseline_days),
            )
            .group_by(SalesHourlyDaily.hour),
        )
    ).all()
    divisor = max(1, len(baseline_days))
    current = {int(hour): {"orders": _as_float(orders), "revenue": _as_float(revenue)} for hour, orders, revenue in current_rows}
    baseline = {
        int(hour): {"orders": _as_float(orders) / divisor, "revenue": _as_float(revenue) / divisor}
        for hour, orders, revenue in baseline_rows
    }
    rows: list[dict[str, Any]] = []
    for hour in sorted(set(current) | set(baseline)):
        cur = current.get(hour, {})
        base = baseline.get(hour, {})
        orders = _as_float(cur.get("orders"))
        revenue = _as_float(cur.get("revenue"))
        baseline_orders = _as_float(base.get("orders"))
        baseline_revenue = _as_float(base.get("revenue"))
        rows.append(
            {
                "hour": int(hour),
                "orders": int(round(orders)),
                "revenue": _round_money(revenue),
                "baseline_orders": round(baseline_orders, 3),
                "baseline_revenue": _round_money(baseline_revenue),
                "orders_delta": round(orders - baseline_orders, 3),
                "revenue_delta": _round_money(revenue - baseline_revenue),
            },
        )
    return sorted(rows, key=lambda item: (item["revenue_delta"], item["hour"]))[:6]


async def _daily_baseline_contribution(db: AsyncSession, org_id: int, baseline_days: list[date]) -> dict[str, float]:
    if not baseline_days:
        return {"orders": 0.0, "guests": 0.0, "avg_check": 0.0}
    row = (
        await db.execute(
            select(
                func.coalesce(func.sum(SalesDailyAgg.order_count), 0),
                func.coalesce(func.sum(SalesDailyAgg.guest_count), 0),
                func.coalesce(func.avg(SalesDailyAgg.avg_check), 0),
            ).where(
                SalesDailyAgg.organization_id == int(org_id),
                SalesDailyAgg.source == SOURCE_IIKO_OLAP,
                SalesDailyAgg.date.in_(baseline_days),
            ),
        )
    ).one()
    divisor = max(1, len(baseline_days))
    return {
        "orders": _as_float(row[0]) / divisor,
        "guests": _as_float(row[1]) / divisor,
        "avg_check": _as_float(row[2]),
    }


async def _sales_drilldown_for_day(db: AsyncSession, org_id: int, day: date) -> dict[str, Any]:
    baseline_days = _baseline_days_for(day)
    categories = _merge_item_drilldown(
        await _current_item_rows(db, org_id, day, by_dish=False),
        await _baseline_item_rows(db, org_id, baseline_days, by_dish=False),
        limit=5,
    )
    dishes = _merge_item_drilldown(
        await _current_item_rows(db, org_id, day, by_dish=True),
        await _baseline_item_rows(db, org_id, baseline_days, by_dish=True),
        limit=8,
    )
    weak_hours = await _hour_drilldown_for_day(db, org_id, day, baseline_days)
    return {
        "baseline_days": [d.isoformat() for d in baseline_days],
        "categories": categories,
        "dishes": dishes,
        "weak_hours": weak_hours,
        "daily_baseline": await _daily_baseline_contribution(db, org_id, baseline_days),
    }


async def detect_sales_anomalies(
    db: AsyncSession,
    org_id: int,
    *,
    limit_days: int = 7,
) -> list[OperationalInsight]:
    rows = (
        await db.execute(
            select(SalesDailyAgg)
            .where(
                SalesDailyAgg.organization_id == int(org_id),
                SalesDailyAgg.source == SOURCE_IIKO_OLAP,
                SalesDailyAgg.delta_pct.is_not(None),
            )
            .order_by(SalesDailyAgg.date.desc())
            .limit(max(1, limit_days)),
        )
    ).scalars().all()

    created: list[OperationalInsight] = []
    quality = await latest_quality_status(db, int(org_id), source=SOURCE_IIKO_OLAP, entity_type="sales")
    quality_confidence = float(quality.get("confidence_score") or 0)
    for row in rows:
        delta = float(row.delta_pct or 0)
        if delta > -15:
            continue
        idempotency = f"sales_anomaly:{org_id}:{row.date.isoformat()}:revenue_drop"
        existing_rows = (
            await db.execute(
                select(OperationalInsight)
                .where(
                    OperationalInsight.organization_id == int(org_id),
                    OperationalInsight.insight_type == "sales_revenue_drop",
                )
                .limit(50),
            )
        ).scalars().all()
        if any(isinstance(x.payload_json, dict) and x.payload_json.get("idempotency") == idempotency for x in existing_rows):
            continue

        baseline = float(row.baseline_revenue or 0)
        revenue = float(row.total_revenue or 0)
        delta_money = revenue - baseline if baseline else 0
        strength = min(1.0, abs(delta) / 40)
        confidence_score = round(max(0.1, min(0.95, 0.35 + strength * 0.4 + quality_confidence * 0.25)), 4)
        detail = await _sales_drilldown_for_day(db, int(org_id), row.date)
        daily_baseline = detail["daily_baseline"]
        orders = int(row.order_count or 0)
        guests = int(row.guest_count or 0)
        avg_check = float(row.avg_check or 0)

        evidence = {
            "metric": "total_revenue",
            "date": row.date.isoformat(),
            "actual_revenue": round(revenue, 2),
            "baseline_revenue": round(baseline, 2),
            "delta_pct": round(delta, 2),
            "delta_money": round(delta_money, 2),
            "data_quality": quality,
            "baseline_days": detail["baseline_days"],
            "top_categories": detail["categories"],
            "top_dishes": detail["dishes"],
            "weak_hours": detail["weak_hours"],
        }
        drilldown = {
            "path": [
                {"level": "day", "date": row.date.isoformat(), "delta_pct": round(delta, 2)},
                {
                    "level": "orders",
                    "order_count": orders,
                    "baseline_order_count": round(daily_baseline["orders"], 3),
                    "order_count_delta": round(orders - daily_baseline["orders"], 3),
                    "guest_count": guests,
                    "baseline_guest_count": round(daily_baseline["guests"], 3),
                    "guest_count_delta": round(guests - daily_baseline["guests"], 3),
                    "avg_check": round(avg_check, 2),
                    "baseline_avg_check": round(daily_baseline["avg_check"], 2),
                    "avg_check_delta": round(avg_check - daily_baseline["avg_check"], 2),
                },
                {"level": "category", "items": detail["categories"]},
                {"level": "dish", "items": detail["dishes"]},
                {"level": "hour", "items": detail["weak_hours"]},
            ],
            "contribution": {
                "revenue_delta": round(delta_money, 2),
                "orders": orders,
                "baseline_orders": round(daily_baseline["orders"], 3),
                "orders_delta": round(orders - daily_baseline["orders"], 3),
                "guests": guests,
                "baseline_guests": round(daily_baseline["guests"], 3),
                "guests_delta": round(guests - daily_baseline["guests"], 3),
                "avg_check": round(avg_check, 2),
                "baseline_avg_check": round(daily_baseline["avg_check"], 2),
                "avg_check_delta": round(avg_check - daily_baseline["avg_check"], 2),
                "category_revenue_delta": round(sum(item["revenue_delta"] for item in detail["categories"]), 2),
                "dish_revenue_delta": round(sum(item["revenue_delta"] for item in detail["dishes"]), 2),
                "hour_revenue_delta": round(sum(item["revenue_delta"] for item in detail["weak_hours"]), 2),
            },
        }
        insight = OperationalInsight(
            organization_id=int(org_id),
            insight_type="sales_revenue_drop",
            severity="critical" if delta <= -30 else "warning",
            title="Выручка ниже обычного уровня",
            summary=(
                f"{row.date.isoformat()}: выручка {float(row.total_revenue or 0):.0f} ₸, "
                f"отклонение от baseline {delta:.1f}%."
            ),
            confidence_score=confidence_score,
            evidence_json=evidence,
            drilldown_json=drilldown,
            payload_json={
                "idempotency": idempotency,
                "source": SOURCE_IIKO_OLAP,
                "date": row.date.isoformat(),
                "revenue": round(float(row.total_revenue or 0), 2),
                "baseline_revenue": round(float(row.baseline_revenue or 0), 2),
                "delta_pct": round(delta, 2),
                "confidence_score": confidence_score,
                "evidence": evidence,
                "drilldown": drilldown,
                "cause_hypotheses": ["traffic_drop_or_menu_availability", "category_mix_change"],
                "recommended_actions": _actions_for_sales_delta(delta),
                "created_by": "sales_anomaly_engine",
                "created_at": datetime.now(tz=timezone.utc).isoformat(),
            },
        )
        db.add(insight)
        created.append(insight)
    if created:
        await db.flush()
    return created


async def sales_anomaly_scheduled_tick(_ctx: dict[str, Any] | None = None) -> None:
    from sqlalchemy import select as _select

    from app.db.models import Organization
    from app.db.session import async_session_factory

    async with async_session_factory() as db:
        org_ids = list(
            (await db.execute(_select(Organization.id).where(Organization.is_active.is_(True)))).scalars().all(),
        )
    for org_id in org_ids:
        async with async_session_factory() as db:
            await detect_sales_anomalies(db, int(org_id))
            await db.commit()
