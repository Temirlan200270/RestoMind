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


async def fetch_daily_revenue_history_from_events(
    db: AsyncSession,
    org_id: int,
    *,
    days: int = 28,
    now_utc: datetime | None = None,
) -> dict[str, float]:
    """Выручка по дням из DailyOrgStats.revenue_kzt (event-driven).

    Делегирует в get_event_stats — единственный источник чтения из DailyOrgStats.
    """
    from app.services.analytics_consumer import get_event_stats
    rows = await get_event_stats(db, org_id, days=days)
    return {r["date"]: float(r["revenue_kzt"] or 0) for r in rows}


def event_revenue_history_usable(revenue_by_date: dict[str, float], *, min_days: int = 3) -> bool:
    """Достаточно event-driven дней с ненулевой выручкой для прогноза."""
    nonzero = sum(1 for v in revenue_by_date.values() if float(v) > 0)
    return nonzero >= min_days


def build_demand_forecast(
    orders_by_date: dict[str, int],
    *,
    today: date | None = None,
) -> dict[str, Any] | None:
    """Прогноз объёма заказов до конца текущей недели (пн–вс).

    Аналог build_week_forecast, но для orders_confirmed из DailyOrgStats.
    Использует линейное среднее по прошедшим дням текущей недели.
    """
    if not orders_by_date:
        return None
    today = today or datetime.now(tz=timezone.utc).date()
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)

    week_days = {
        d: int(orders_by_date[d])
        for d in orders_by_date
        if week_start.isoformat() <= d <= today.isoformat()
    }
    if not week_days:
        return None

    confirmed_so_far = sum(week_days.values())
    days_elapsed = len(week_days)
    daily_avg = confirmed_so_far / days_elapsed if days_elapsed else 0.0
    days_remaining = (week_end - today).days
    forecast = confirmed_so_far + round(daily_avg * days_remaining)
    confidence = "low" if days_elapsed < 3 else ("medium" if days_elapsed < 5 else "high")

    return {
        "forecast_orders": forecast,
        "confirmed_so_far": confirmed_so_far,
        "daily_avg_orders": round(daily_avg, 1),
        "days_remaining": days_remaining,
        "days_elapsed": days_elapsed,
        "confidence": confidence,
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
    }


def build_cancellation_forecast(
    stats_rows: list[dict],
    *,
    today: date | None = None,
) -> dict[str, Any] | None:
    """Прогноз риска отмен на основе event-driven данных DailyOrgStats.

    Возвращает уровень риска (low/medium/high) + историческую ставку отмен.
    """
    if not stats_rows:
        return None
    today = today or datetime.now(tz=timezone.utc).date()
    week_start = today - timedelta(days=today.weekday())

    total_confirmed = 0
    total_cancelled = 0
    week_confirmed = 0
    week_cancelled = 0

    for r in stats_rows:
        c = int(r.get("orders_confirmed") or 0)
        x = int(r.get("orders_cancelled") or 0)
        total_confirmed += c
        total_cancelled += x
        d_str = str(r.get("date") or "")
        if d_str >= week_start.isoformat():
            week_confirmed += c
            week_cancelled += x

    total_orders = total_confirmed + total_cancelled
    if total_orders == 0:
        return None

    hist_rate = total_cancelled / total_orders
    week_total = week_confirmed + week_cancelled
    week_rate = (week_cancelled / week_total) if week_total > 0 else hist_rate

    if week_rate >= 0.20 or (week_rate > hist_rate * 1.5 and week_rate >= 0.10):
        risk_level = "high"
    elif week_rate >= 0.10 or week_rate > hist_rate * 1.3:
        risk_level = "medium"
    else:
        risk_level = "low"

    return {
        "risk_level": risk_level,
        "cancellation_rate_pct": round(week_rate * 100, 1),
        "historical_rate_pct": round(hist_rate * 100, 1),
        "week_cancelled": week_cancelled,
        "week_confirmed": week_confirmed,
        "description": (
            "Высокий риск отмен — выше нормы" if risk_level == "high"
            else "Умеренный риск отмен" if risk_level == "medium"
            else "Отмены в норме"
        ),
    }


def build_overload_risk(
    stats_rows: list[dict],
    *,
    today: date | None = None,
) -> dict[str, Any] | None:
    """Прогноз риска перегрузки: текущий темп заказов vs историческая норма.

    Сравнивает среднедневной темп текущей недели с 4-недельным историческим средним.
    """
    if not stats_rows:
        return None
    today = today or datetime.now(tz=timezone.utc).date()
    week_start = today - timedelta(days=today.weekday())

    week_days_data: dict[str, int] = {}
    hist_by_weekday: dict[int, list[int]] = defaultdict(list)

    for r in stats_rows:
        d_str = str(r.get("date") or "")
        if not d_str:
            continue
        try:
            d = date.fromisoformat(d_str)
        except ValueError:
            continue
        orders = int(r.get("orders_confirmed") or 0)
        if d >= week_start:
            week_days_data[d_str] = orders
        elif d < week_start:
            hist_by_weekday[d.weekday()].append(orders)

    if not week_days_data:
        return None

    current_pace = sum(week_days_data.values()) / len(week_days_data) if week_days_data else 0.0

    hist_avgs = [
        sum(v) / len(v)
        for v in hist_by_weekday.values()
        if v
    ]
    historical_avg = sum(hist_avgs) / len(hist_avgs) if hist_avgs else None

    if historical_avg is None or historical_avg == 0:
        return {
            "risk_level": "unknown",
            "current_pace": round(current_pace, 1),
            "historical_avg": None,
            "overload_ratio": None,
            "description": "Недостаточно исторических данных",
        }

    ratio = current_pace / historical_avg
    if ratio >= 1.5:
        risk_level = "high"
    elif ratio >= 1.2:
        risk_level = "medium"
    else:
        risk_level = "low"

    return {
        "risk_level": risk_level,
        "current_pace": round(current_pace, 1),
        "historical_avg": round(historical_avg, 1),
        "overload_ratio": round(ratio, 2),
        "description": (
            "Перегрузка вероятна — темп выше нормы на {:.0f}%".format((ratio - 1) * 100)
            if risk_level == "high"
            else "Умеренный рост нагрузки" if risk_level == "medium"
            else "Нагрузка в норме"
        ),
    }


def build_autopilot_pricing(
    stats_rows: list[dict],
    *,
    today: date | None = None,
) -> dict[str, Any] | None:
    """Autopilot pricing signal: рекомендация по ценовой стратегии на основе event-данных.

    Сравнивает текущую неделю с предыдущей: рост/падение выручки → конкретная ценовая тактика.
    Не изменяет данные — только recommendation.
    """
    if not stats_rows:
        return None
    today = today or datetime.now(tz=timezone.utc).date()
    week_start = today - timedelta(days=today.weekday())
    prev_week_start = week_start - timedelta(days=7)

    current_week: list[dict] = []
    prev_week: list[dict] = []
    for r in stats_rows:
        d_str = str(r.get("date") or "")
        if not d_str:
            continue
        if d_str >= week_start.isoformat():
            current_week.append(r)
        elif d_str >= prev_week_start.isoformat():
            prev_week.append(r)

    if not current_week or not prev_week:
        return None

    cur_revenue = sum(r["revenue_kzt"] for r in current_week)
    prev_revenue = sum(r["revenue_kzt"] for r in prev_week)
    cur_orders = sum(r["orders_confirmed"] for r in current_week)
    prev_orders = sum(r["orders_confirmed"] for r in prev_week)

    if prev_revenue <= 0 or prev_orders <= 0:
        return None

    revenue_ratio = cur_revenue / prev_revenue
    orders_ratio = cur_orders / prev_orders

    cur_avg_check = cur_revenue / cur_orders if cur_orders > 0 else 0
    prev_avg_check = prev_revenue / prev_orders if prev_orders > 0 else 0

    if revenue_ratio >= 1.2 and orders_ratio >= 1.1:
        tactic = "demand_up"
        suggestion = "Спрос вырос. Рассмотрите подъём цен на 5-10% на топовые позиции."
        price_adj_pct = 7
    elif revenue_ratio <= 0.8 and orders_ratio <= 0.85:
        tactic = "demand_down"
        suggestion = "Спрос упал. Промо-акция или снижение цен на 10% может вернуть трафик."
        price_adj_pct = -10
    elif orders_ratio >= 1.15 and cur_avg_check < prev_avg_check * 0.9:
        tactic = "upsell_needed"
        suggestion = "Заказов больше, но средний чек ниже нормы. Усильте upsell-правила."
        price_adj_pct = 0
    elif revenue_ratio >= 1.0 and orders_ratio <= 0.95:
        tactic = "avg_check_up"
        suggestion = "Заказов меньше, но выручка держится — средний чек растёт. Продолжайте стратегию."
        price_adj_pct = 0
    else:
        tactic = "stable"
        suggestion = "Метрики стабильны. Ценовые изменения не требуются."
        price_adj_pct = 0

    return {
        "tactic": tactic,
        "suggestion": suggestion,
        "price_adj_pct": price_adj_pct,
        "current_avg_check": round(cur_avg_check, 0),
        "prev_avg_check": round(prev_avg_check, 0),
        "revenue_ratio": round(revenue_ratio, 2),
        "orders_ratio": round(orders_ratio, 2),
        "confidence": "high" if len(current_week) >= 5 and len(prev_week) >= 5 else "medium" if len(current_week) >= 3 else "low",
    }


def build_stock_alerts_stub(
    stats_rows: list[dict],
    *,
    today: date | None = None,
) -> list[dict[str, Any]]:
    """Прокси-алерты запасов до интеграции iiko Office (SupplyMind bridge)."""
    if not stats_rows:
        return []
    today = today or datetime.now(tz=timezone.utc).date()
    week_start = today - timedelta(days=today.weekday())
    week_orders = 0
    for r in stats_rows:
        d_str = str(r.get("date") or "")
        if d_str >= week_start.isoformat():
            week_orders += int(r.get("orders_confirmed") or 0)
    if week_orders < 3:
        return []
    pace = max(week_orders / max(1, (today - week_start).days + 1), 1.0)
    days_left = max(2, int(14 - pace))
    return [
        {
            "ingredient": "kitchen_supply_proxy",
            "days_until_runout": days_left,
            "confidence": "low",
            "source": "daily_org_stats.orders_confirmed",
            "message": (
                f"По темпу заказов ({week_orders} за неделю) проверьте ключевые позиции "
                "на кухне — полный SupplyMind подключит iiko Office."
            ),
        },
    ]


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
