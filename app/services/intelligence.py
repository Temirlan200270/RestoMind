"""Restaurant Intelligence analytics, insights, and Digital Twin helpers."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import (
    ChatLog,
    IntelligenceConversation,
    IntelligenceMessage,
    MenuItem,
    OperationalInsight,
    Order,
    OrderStatus,
    RestaurantStateSnapshot,
)
from app.services.tenant_scope import orders_tenant_clause


def _dt_as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _sql_dt_for_filter(dt: datetime) -> datetime:
    u = _dt_as_utc(dt)
    if settings.db_mode == "sqlite":
        return u.replace(tzinfo=None)
    return u


def pct_change(current: float, previous: float) -> float | None:
    if previous == 0:
        return None if current == 0 else 100.0
    return round((current - previous) / previous * 100, 1)


def _period_bounds(period: str) -> tuple[datetime, datetime, datetime, datetime, str]:
    now = datetime.now(timezone.utc)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    p = (period or "today").strip().lower()
    if p in {"yesterday", "вчера"}:
        start = today - timedelta(days=1)
        end = today
        label = "yesterday"
    elif p in {"week", "7d", "неделя"}:
        start = today - timedelta(days=7)
        end = now
        label = "week"
    else:
        start = today
        end = now
        label = "today"
    duration = end - start
    return start, end, start - duration, start, label


def detect_language(text: str) -> str:
    t = text or ""
    if re.search(r"[а-яА-ЯёЁ]", t):
        return "ru"
    return "en"


def parse_revenue_orders_intent(question: str) -> dict[str, str]:
    q = (question or "").lower()
    period = "today"
    if "вчера" in q or "yesterday" in q:
        period = "yesterday"
    elif "недел" in q or "week" in q or "7" in q:
        period = "week"
    metric = "summary"
    if any(x in q for x in ("выруч", "revenue", "прибыл", "profit")):
        metric = "revenue"
    elif any(x in q for x in ("заказ", "orders")):
        metric = "orders"
    elif any(x in q for x in ("отмен", "cancel")):
        metric = "cancellations"
    return {"metric": metric, "period": period, "language": detect_language(question)}


async def revenue_orders_summary(db: AsyncSession, org_id: int, period: str) -> dict[str, Any]:
    start, end, prev_start, prev_end, label = _period_bounds(period)
    start_sql = _sql_dt_for_filter(start)
    end_sql = _sql_dt_for_filter(end)
    prev_start_sql = _sql_dt_for_filter(prev_start)
    prev_end_sql = _sql_dt_for_filter(prev_end)
    org_orders = orders_tenant_clause(org_id)
    not_cancelled = Order.status != OrderStatus.CANCELLED.value

    cur = (
        await db.execute(
            select(func.count(Order.id), func.coalesce(func.sum(Order.total_price), 0))
            .where(not_cancelled, org_orders, Order.created_at >= start_sql, Order.created_at < end_sql)
        )
    ).one()
    prev = (
        await db.execute(
            select(func.count(Order.id), func.coalesce(func.sum(Order.total_price), 0))
            .where(not_cancelled, org_orders, Order.created_at >= prev_start_sql, Order.created_at < prev_end_sql)
        )
    ).one()
    cur_cancelled = int(
        await db.scalar(
            select(func.count(Order.id)).where(
                org_orders,
                Order.status == OrderStatus.CANCELLED.value,
                Order.created_at >= start_sql,
                Order.created_at < end_sql,
            )
        )
        or 0
    )
    prev_cancelled = int(
        await db.scalar(
            select(func.count(Order.id)).where(
                org_orders,
                Order.status == OrderStatus.CANCELLED.value,
                Order.created_at >= prev_start_sql,
                Order.created_at < prev_end_sql,
            )
        )
        or 0
    )
    cur_orders = int(cur[0] or 0)
    prev_orders = int(prev[0] or 0)
    cur_revenue = float(cur[1] or 0)
    prev_revenue = float(prev[1] or 0)

    rows = await db.execute(
        select(Order.items_json, Order.total_price)
        .where(not_cancelled, org_orders, Order.created_at >= start_sql, Order.created_at < end_sql)
    )
    item_counter: Counter[str] = Counter()
    item_revenue: Counter[str] = Counter()
    for items_json, _total in rows.all():
        if not isinstance(items_json, dict):
            continue
        for item in items_json.get("items") or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "?").strip() or "?"
            qty = int(item.get("quantity") or 0)
            item_counter[name] += qty
            item_revenue[name] += float(item.get("item_total") or 0)

    top_items = [
        {"name": name, "quantity": qty, "revenue": round(float(item_revenue[name]), 2)}
        for name, qty in item_counter.most_common(5)
    ]

    cur_avg = cur_revenue / cur_orders if cur_orders else 0
    prev_avg = prev_revenue / prev_orders if prev_orders else 0
    cancel_rate = round(cur_cancelled / max(cur_orders + cur_cancelled, 1) * 100, 1)
    prev_cancel_rate = round(prev_cancelled / max(prev_orders + prev_cancelled, 1) * 100, 1)
    lost_revenue_estimate = max(0.0, cur_cancelled * (cur_avg or prev_avg))

    return {
        "period": label,
        "date_from": start.date().isoformat(),
        "date_to": end.date().isoformat(),
        "current": {
            "revenue": round(cur_revenue, 2),
            "orders": cur_orders,
            "avg_check": round(cur_avg, 2),
            "cancelled_orders": cur_cancelled,
            "cancel_rate_pct": cancel_rate,
        },
        "previous": {
            "revenue": round(prev_revenue, 2),
            "orders": prev_orders,
            "avg_check": round(prev_avg, 2),
            "cancelled_orders": prev_cancelled,
            "cancel_rate_pct": prev_cancel_rate,
        },
        "changes": {
            "revenue_pct": pct_change(cur_revenue, prev_revenue),
            "orders_pct": pct_change(cur_orders, prev_orders),
            "avg_check_pct": pct_change(cur_avg, prev_avg),
            "cancelled_orders_pct": pct_change(cur_cancelled, prev_cancelled),
            "cancel_rate_pp": round(cancel_rate - prev_cancel_rate, 1),
        },
        "top_items": top_items,
        "lost_revenue_estimate": round(lost_revenue_estimate, 2),
    }


def build_intelligence_answer(question: str, intent: dict[str, str], summary: dict[str, Any]) -> str:
    lang = intent.get("language") or "ru"
    cur = summary["current"]
    prev = summary["previous"]
    ch = summary["changes"]
    if lang == "en":
        lines = [
            f"Revenue is {cur['revenue']:.0f} KZT across {cur['orders']} orders.",
            f"Compared with the previous period: revenue {ch['revenue_pct']}%, orders {ch['orders_pct']}%, average check {ch['avg_check_pct']}%.",
        ]
        if (ch.get("orders_pct") or 0) < -5:
            lines.append("The main driver is fewer orders, not only check size.")
        if (ch.get("cancel_rate_pp") or 0) > 2:
            lines.append(f"Cancellations also worsened: cancel rate is up by {ch['cancel_rate_pp']} pp.")
        return " ".join(lines)

    lines = [
        f"За период выручка составила {cur['revenue']:.0f} ₸ при {cur['orders']} заказах.",
        f"К предыдущему периоду: выручка {ch['revenue_pct']}%, заказы {ch['orders_pct']}%, средний чек {ch['avg_check_pct']}%.",
    ]
    if (ch.get("orders_pct") or 0) < -5:
        lines.append("Главная причина выглядит как снижение количества заказов, а не только изменение среднего чека.")
    if (ch.get("avg_check_pct") or 0) < -5:
        lines.append("Дополнительно просел средний чек, стоит проверить топовые блюда и допродажи.")
    if (ch.get("cancel_rate_pp") or 0) > 2:
        lines.append(f"Отмены тоже ухудшили результат: доля отмен выросла на {ch['cancel_rate_pp']} п.п.")
    if cur["cancelled_orders"]:
        lines.append(f"Оценка потерянной выручки из-за отмен: около {summary['lost_revenue_estimate']:.0f} ₸.")
    return " ".join(lines)


async def answer_intelligence_query(
    db: AsyncSession,
    *,
    org_id: int,
    question: str,
    conversation_id: int | None = None,
) -> dict[str, Any]:
    intent = parse_revenue_orders_intent(question)
    summary = await revenue_orders_summary(db, org_id, intent["period"])
    answer = build_intelligence_answer(question, intent, summary)

    conv: IntelligenceConversation | None = None
    if conversation_id:
        conv = await db.get(IntelligenceConversation, int(conversation_id))
        if conv is not None and int(conv.organization_id) != int(org_id):
            conv = None
    if conv is None:
        conv = IntelligenceConversation(organization_id=org_id, title=(question or "AI-аналитик")[:240])
        db.add(conv)
        await db.flush()
    db.add_all(
        [
            IntelligenceMessage(
                conversation_id=int(conv.id),
                organization_id=org_id,
                role="user",
                content=question,
                payload_json={"intent": intent},
            ),
            IntelligenceMessage(
                conversation_id=int(conv.id),
                organization_id=org_id,
                role="assistant",
                content=answer,
                payload_json={"summary": summary},
            ),
        ]
    )
    await db.flush()
    return {
        "conversation_id": int(conv.id),
        "answer": answer,
        "intent": intent,
        "summary": summary,
    }


async def generate_revenue_order_insights(db: AsyncSession, org_id: int) -> list[OperationalInsight]:
    summary = await revenue_orders_summary(db, org_id, "today")
    ch = summary["changes"]
    cur = summary["current"]
    insights: list[OperationalInsight] = []

    def add(insight_type: str, severity: str, title: str, text: str) -> None:
        insights.append(
            OperationalInsight(
                organization_id=org_id,
                insight_type=insight_type,
                severity=severity,
                title=title,
                summary=text,
                payload_json=summary,
            )
        )

    if (ch.get("revenue_pct") or 0) <= -15:
        add(
            "revenue_drop",
            "warning",
            "Выручка ниже предыдущего периода",
            f"Сейчас {cur['revenue']:.0f} ₸, изменение {ch['revenue_pct']}%. Проверьте поток заказов и отмены.",
        )
    if (ch.get("orders_pct") or 0) <= -15:
        add(
            "orders_drop",
            "warning",
            "Заказов стало меньше",
            f"Количество заказов изменилось на {ch['orders_pct']}%. Средний чек: {cur['avg_check']:.0f} ₸.",
        )
    if (ch.get("cancel_rate_pp") or 0) >= 5:
        add(
            "cancellations_up",
            "critical",
            "Выросла доля отмен",
            f"Доля отмен выросла на {ch['cancel_rate_pp']} п.п.; потерянная выручка оценивается в {summary['lost_revenue_estimate']:.0f} ₸.",
        )
    if not insights:
        add(
            "sales_stable",
            "info",
            "Продажи без резких отклонений",
            f"Сегодня {cur['orders']} заказов на {cur['revenue']:.0f} ₸. Сильных аномалий по выручке и заказам нет.",
        )

    for insight in insights:
        db.add(insight)
    await db.flush()
    return insights


async def list_insights(db: AsyncSession, org_id: int, *, limit: int = 20) -> list[OperationalInsight]:
    rows = await db.execute(
        select(OperationalInsight)
        .where(OperationalInsight.organization_id == org_id, OperationalInsight.status != "dismissed")
        .order_by(OperationalInsight.created_at.desc(), OperationalInsight.id.desc())
        .limit(max(1, min(limit, 100)))
    )
    found = list(rows.scalars())
    if not found:
        found = await generate_revenue_order_insights(db, org_id)
    return found


async def build_state_snapshot(db: AsyncSession, org_id: int, *, persist: bool = True) -> RestaurantStateSnapshot:
    now = datetime.now(timezone.utc)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_sql = _sql_dt_for_filter(today)
    now_sql = _sql_dt_for_filter(now)
    org_orders = orders_tenant_clause(org_id)

    draft = int(await db.scalar(select(func.count(Order.id)).where(org_orders, Order.status == OrderStatus.DRAFT.value)) or 0)
    confirmed = int(
        await db.scalar(
            select(func.count(Order.id)).where(
                org_orders,
                Order.status.in_([OrderStatus.CONFIRMED.value, OrderStatus.SENDING_TO_IIKO.value]),
            )
        )
        or 0
    )
    active = int(
        await db.scalar(
            select(func.count(Order.id)).where(
                org_orders,
                Order.status.in_(
                    [
                        OrderStatus.DRAFT.value,
                        OrderStatus.CONFIRMED.value,
                        OrderStatus.SENDING_TO_IIKO.value,
                        OrderStatus.SENT_TO_IIKO.value,
                        OrderStatus.IN_TRANSIT.value,
                        OrderStatus.WAITING_PICKUP.value,
                    ]
                ),
            )
        )
        or 0
    )
    revenue_row = (
        await db.execute(
            select(func.count(Order.id), func.coalesce(func.sum(Order.total_price), 0))
            .where(
                org_orders,
                Order.status != OrderStatus.CANCELLED.value,
                Order.created_at >= today_sql,
                Order.created_at <= now_sql,
            )
        )
    ).one()
    today_orders = int(revenue_row[0] or 0)
    revenue = float(revenue_row[1] or 0)
    cancelled = int(
        await db.scalar(
            select(func.count(Order.id)).where(
                org_orders,
                Order.status == OrderStatus.CANCELLED.value,
                Order.created_at >= today_sql,
                Order.created_at <= now_sql,
            )
        )
        or 0
    )
    stoplist = int(
        await db.scalar(select(func.count(MenuItem.id)).where(MenuItem.organization_id == org_id, MenuItem.is_available.is_(False)))
        or 0
    )
    operator_msgs_15m = int(
        await db.scalar(
            select(func.count(ChatLog.id)).where(
                ChatLog.organization_id == org_id,
                ChatLog.role == "operator",
                ChatLog.created_at >= _sql_dt_for_filter(now - timedelta(minutes=15)),
            )
        )
        or 0
    )
    queue_size = draft + confirmed
    operator_load = round(min(100.0, (queue_size / max(operator_msgs_15m, 1)) * 25), 1)
    kitchen_load = round(min(100.0, active * 8 + stoplist * 1.5), 1)
    snapshot = RestaurantStateSnapshot(
        organization_id=org_id,
        active_orders=active,
        draft_orders=draft,
        confirmed_orders=confirmed,
        cancelled_today=cancelled,
        revenue_today=revenue,
        avg_check_today=(revenue / today_orders if today_orders else 0),
        queue_size=queue_size,
        operator_load=operator_load,
        kitchen_load=kitchen_load,
        stoplist_count=stoplist,
        payload_json={"today_orders": today_orders, "operator_messages_15m": operator_msgs_15m},
    )
    if persist:
        db.add(snapshot)
        await db.flush()
    return snapshot


@dataclass
class SimulationInput:
    orders_per_hour: float
    operators: int
    avg_check: float
    base_cancel_rate_pct: float = 5.0


def simulate_operator_capacity(inp: SimulationInput) -> dict[str, Any]:
    capacity_per_operator = 18.0
    total_capacity = max(1.0, inp.operators * capacity_per_operator)
    load_pct = round(inp.orders_per_hour / total_capacity * 100, 1)
    overload = max(0.0, inp.orders_per_hour - total_capacity)
    expected_wait_min = round(2.5 + max(0.0, load_pct - 70) * 0.16, 1)
    cancel_rate = min(60.0, inp.base_cancel_rate_pct + overload * 1.2 + max(0.0, load_pct - 100) * 0.12)
    expected_lost_orders = round(inp.orders_per_hour * cancel_rate / 100, 1)
    lost_revenue = round(expected_lost_orders * inp.avg_check, 0)
    severity = "ok"
    if load_pct >= 120 or cancel_rate >= 18:
        severity = "critical"
    elif load_pct >= 90 or cancel_rate >= 10:
        severity = "warning"
    return {
        "orders_per_hour": inp.orders_per_hour,
        "operators": inp.operators,
        "load_pct": load_pct,
        "expected_wait_min": expected_wait_min,
        "cancel_rate_pct": round(cancel_rate, 1),
        "expected_lost_orders": expected_lost_orders,
        "lost_revenue": lost_revenue,
        "severity": severity,
    }
