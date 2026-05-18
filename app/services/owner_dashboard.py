"""Метрики Owner Dashboard: прогноз, цели рекомендаций (без LLM)."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import Order, OrderStatus
from app.services.tenant_scope import orders_tenant_clause


def _sql_dt_for_filter(dt: datetime) -> datetime:
    u = dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    if settings.db_mode == "sqlite":
        return u.replace(tzinfo=None)
    return u


def _order_day_key_utc(created_at: datetime | None) -> str | None:
    if created_at is None:
        return None
    dt = created_at
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%d")


async def fetch_daily_revenue_history(
    db: AsyncSession,
    org_id: int,
    *,
    days: int = 28,
    now_utc: datetime | None = None,
) -> dict[str, float]:
    """Выручка по дням (UTC) за последние N дней — для прогноза по дню недели."""
    now_utc = now_utc or datetime.now(tz=timezone.utc)
    today_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    floor = today_start - timedelta(days=max(days - 1, 0))
    floor_sql = _sql_dt_for_filter(floor)
    hi_sql = _sql_dt_for_filter(now_utc)
    not_cancelled = Order.status != OrderStatus.CANCELLED
    org_orders = orders_tenant_clause(org_id)

    rows = await db.execute(
        select(Order.created_at, Order.total_price).where(
            not_cancelled,
            org_orders,
            Order.created_at.isnot(None),
            Order.created_at >= floor_sql,
            Order.created_at <= hi_sql,
        ),
    )
    bucket: dict[str, float] = defaultdict(float)
    for created_at, total_price in rows:
        dk = _order_day_key_utc(created_at)
        if dk:
            bucket[dk] += float(total_price or 0)
    return dict(bucket)


def build_recommendation_target(rec_type: str) -> dict[str, Any]:
    """Куда вести владельца из top_actions (совместимо с incidentGo)."""
    t = (rec_type or "").strip().lower()
    if t == "product_boost":
        return {"tab": "menu", "menuView": "catalog", "label": "Открыть меню"}
    if t == "pricing_adj":
        return {"tab": "menu", "menuView": "catalog", "label": "Проверить цены"}
    if t == "geo_expansion":
        return {"tab": "dashboard", "dashboardTab": "analytics", "label": "Аналитика доставки"}
    if t == "stoplist_impact":
        return {"tab": "menu", "menuView": "stoplist", "label": "Стоп-лист"}
    if t == "upsell_pair":
        return {"tab": "settings", "settingsTab": "restaurant", "label": "Правила допродаж"}
    return {"tab": "ai_center", "aiCenterTab": "insights", "label": "Все рекомендации"}


def build_week_forecast(
    revenue_by_date: dict[str, float],
    *,
    today: date | None = None,
    history_days: int = 28,
) -> dict[str, Any] | None:
    """
    Прогноз выручки до конца недели: среднее по тому же дню недели (история),
    иначе линейное среднее за текущую неделю.
    """
    if not revenue_by_date:
        return None
    today = today or datetime.now(tz=timezone.utc).date()
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)
    hist_floor = today - timedelta(days=history_days)

    week_days = {
        d: revenue_by_date[d]
        for d in revenue_by_date
        if week_start.isoformat() <= d <= today.isoformat()
    }
    if not week_days:
        return None

    earned = sum(week_days.values())
    days_elapsed = len(week_days)
    linear_avg = earned / days_elapsed if days_elapsed else 0.0

    weekday_samples: dict[int, list[float]] = defaultdict(list)
    for d_str, rev in revenue_by_date.items():
        try:
            d = date.fromisoformat(d_str)
        except ValueError:
            continue
        if d < hist_floor or d > today:
            continue
        weekday_samples[d.weekday()].append(float(rev))

    projected = 0.0
    weekday_days_used = 0
    walk = today + timedelta(days=1)
    while walk <= week_end:
        samples = weekday_samples.get(walk.weekday(), [])
        if len(samples) >= 2:
            projected += sum(samples) / len(samples)
            weekday_days_used += 1
        else:
            projected += linear_avg
        walk += timedelta(days=1)

    days_remaining = (week_end - today).days
    forecast = earned + projected
    method = "weekday" if weekday_days_used >= max(1, days_remaining // 2) else "linear"
    if method == "linear":
        forecast = earned + linear_avg * days_remaining

    confidence = "low" if days_elapsed < 3 else ("medium" if days_elapsed < 5 else "high")
    if method == "weekday" and sum(len(v) for v in weekday_samples.values()) >= 14:
        confidence = "high" if confidence != "low" else "medium"

    return {
        "forecast_revenue": round(forecast, 2),
        "earned_so_far": round(earned, 2),
        "days_remaining": days_remaining,
        "days_elapsed": days_elapsed,
        "confidence": confidence,
        "daily_avg": round(linear_avg, 2),
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "method": method,
    }
