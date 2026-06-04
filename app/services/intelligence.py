"""Restaurant Intelligence analytics, insights, and Digital Twin helpers."""

from __future__ import annotations

import logging
import re
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import (
    AiUsageLog,
    ChatLog,
    IntelligenceConversation,
    IntelligenceMessage,
    MenuItem,
    OperationalInsight,
    Order,
    OrderStatus,
    RestaurantStateSnapshot,
)
from app.services.tenant_scope import chat_logs_location_filter, orders_location_filter, orders_tenant_clause

logger = logging.getLogger(__name__)


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


def _period_bounds_same_weekday(now: datetime | None = None) -> tuple[datetime, datetime, datetime, datetime]:
    """Текущий день vs тот же день предыдущей недели (weekday baseline)."""
    if now is None:
        now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elapsed = now - today_start
    prev_week_start = today_start - timedelta(days=7)
    return today_start, now, prev_week_start, prev_week_start + elapsed


def _build_cause_hypotheses(
    *,
    cancel_rate_pct: float,
    kitchen_load: float | None = None,
    stoplist_count: int = 0,
    prev_stoplist_count: int | None = None,
    escalation_rate: float | None = None,
) -> list[str]:
    """Эвристические гипотезы о причинах проблем — для payload_json['cause_hypotheses']."""
    causes: list[str] = []
    if cancel_rate_pct > 15:
        causes.append("high_cancellation_rate")
    if kitchen_load is not None and kitchen_load > 80:
        causes.append("kitchen_overload")
    if prev_stoplist_count is not None and stoplist_count > prev_stoplist_count * 1.3:
        causes.append("stoplist_growth")
    if escalation_rate is not None and escalation_rate > 0.2:
        causes.append("ai_escalation_spike")
    return causes


def _build_recommended_actions(
    insight_type: str,
    *,
    stoplist_count: int = 0,
    cancel_rate_pct: float = 0,
    lost_revenue: float = 0,
) -> list[str]:
    """Простые текстовые рекомендации для payload_json['recommended_actions']."""
    if insight_type == "revenue_drop":
        actions = ["Проверить стоп-лист и наличие топовых позиций"]
        if cancel_rate_pct > 10:
            actions.append(f"Снизить долю отмен — сейчас {cancel_rate_pct:.0f}%, потери ~{lost_revenue:.0f} ₸")
        if stoplist_count > 0:
            actions.append(f"Стоп-лист: {stoplist_count} позиций — уточнить у кухни")
        return actions
    if insight_type == "orders_drop":
        return [
            "Проверить расписание работы и наличие ресурсов",
            "Рассмотреть акцию на популярные блюда",
        ]
    if insight_type == "cancellations_up":
        return [
            "Проверить загрузку кухни и операторов",
            f"Оценить потерянную выручку: ~{lost_revenue:.0f} ₸",
        ]
    return []


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


async def revenue_orders_summary(
    db: AsyncSession,
    org_id: int,
    period: str,
    *,
    location_id: int | None = None,
    allowed_location_ids: set[int] | None = None,
) -> dict[str, Any]:
    start, end, prev_start, prev_end, label = _period_bounds(period)
    start_sql = _sql_dt_for_filter(start)
    end_sql = _sql_dt_for_filter(end)
    prev_start_sql = _sql_dt_for_filter(prev_start)
    prev_end_sql = _sql_dt_for_filter(prev_end)
    org_orders = orders_tenant_clause(org_id)
    order_location_scope = orders_location_filter(allowed_location_ids, location_id)
    not_cancelled = Order.status != OrderStatus.CANCELLED.value

    cur = (
        await db.execute(
            select(func.count(Order.id), func.coalesce(func.sum(Order.total_price), 0))
            .where(
                not_cancelled,
                org_orders,
                order_location_scope,
                Order.created_at >= start_sql,
                Order.created_at < end_sql,
            )
        )
    ).one()
    prev = (
        await db.execute(
            select(func.count(Order.id), func.coalesce(func.sum(Order.total_price), 0))
            .where(
                not_cancelled,
                org_orders,
                order_location_scope,
                Order.created_at >= prev_start_sql,
                Order.created_at < prev_end_sql,
            )
        )
    ).one()
    cur_cancelled = int(
        await db.scalar(
            select(func.count(Order.id)).where(
                org_orders,
                order_location_scope,
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
                order_location_scope,
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
        .where(
            not_cancelled,
            org_orders,
            order_location_scope,
            Order.created_at >= start_sql,
            Order.created_at < end_sql,
        )
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
    role: str | None = None,
) -> dict[str, Any]:
    from app.services.copilot.business_questions import questions_for_role
    from app.services.copilot.engine import (
        _collect_tool_results,
        _compose_answer,
        _llm_answer_or_fallback,
        run_owner_copilot,
    )

    intent = parse_revenue_orders_intent(question)
    started = time.perf_counter()
    copilot_error = False
    try:
        period, results = await _collect_tool_results(
            db,
            org_id=org_id,
            question=question,
            role=role,
        )
        fallback_answer = _compose_answer(question, results)
        answer = await _llm_answer_or_fallback(
            question=question,
            tool_results=results,
            fallback=fallback_answer,
        )
        copilot_result = {
            "answer": answer,
            "period": period,
            "tool_calls": [{"name": r.get("tool"), "result": r} for r in results],
            "data": {str(r.get("tool")): r for r in results},
            "llm_used": answer != fallback_answer,
            "business_questions": questions_for_role(role),
        }
    except Exception:
        logger.exception("owner copilot failed, falling back to local copilot org=%s", org_id)
        copilot_error = True
        copilot_result = await run_owner_copilot(db, org_id=org_id, question=question, role=role)
    latency_ms = int((time.perf_counter() - started) * 1000)
    await _record_copilot_usage(db, org_id, latency_ms=latency_ms, error=copilot_error)
    answer = str(copilot_result.get("answer") or "").strip()
    summary = copilot_result.get("data") or {}

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
                payload_json={
                    "summary": summary,
                    "tool_calls": copilot_result.get("tool_calls") or [],
                },
            ),
        ]
    )
    await db.flush()
    return {
        "conversation_id": int(conv.id),
        "answer": answer,
        "intent": intent,
        "summary": summary,
        "tool_calls": copilot_result.get("tool_calls") or [],
    }


async def _record_copilot_usage(
    db: AsyncSession,
    org_id: int,
    *,
    latency_ms: int,
    error: bool,
) -> None:
    today = datetime.now(tz=timezone.utc).date()
    row = await db.scalar(
        select(AiUsageLog).where(
            AiUsageLog.organization_id == int(org_id),
            AiUsageLog.day == today,
        ),
    )
    if row is None:
        row = AiUsageLog(
            organization_id=int(org_id),
            day=today,
            provider="copilot",
            model="tool-orchestrator",
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            call_count=0,
            error_count=0,
        )
        db.add(row)
    row.call_count = int(row.call_count or 0) + 1
    row.error_count = int(row.error_count or 0) + (1 if error else 0)
    current_p95 = int(row.p95_latency_ms or 0)
    row.p95_latency_ms = max(current_p95, int(latency_ms or 0))
    await db.flush()


async def generate_revenue_order_insights(db: AsyncSession, org_id: int) -> list[OperationalInsight]:
    summary = await revenue_orders_summary(db, org_id, "today")
    ch = summary["changes"]
    cur = summary["current"]
    lost_rev = float(summary.get("lost_revenue_estimate") or 0)
    cancel_rate = float(cur.get("cancel_rate_pct") or 0)

    # Weekday baseline — сравниваем с тем же днём прошлой недели
    now_utc = datetime.now(timezone.utc)
    wd_start, wd_end, wd_prev_start, wd_prev_end = _period_bounds_same_weekday(now_utc)
    wd_start_sql = _sql_dt_for_filter(wd_start)
    wd_end_sql = _sql_dt_for_filter(wd_end)
    wd_prev_start_sql = _sql_dt_for_filter(wd_prev_start)
    wd_prev_end_sql = _sql_dt_for_filter(wd_prev_end)
    org_orders = orders_tenant_clause(org_id)
    not_cancelled = Order.status != OrderStatus.CANCELLED.value

    wd_cur = (await db.execute(
        select(func.count(Order.id), func.coalesce(func.sum(Order.total_price), 0))
        .where(not_cancelled, org_orders, Order.created_at >= wd_start_sql, Order.created_at < wd_end_sql)
    )).one()
    wd_prev = (await db.execute(
        select(func.count(Order.id), func.coalesce(func.sum(Order.total_price), 0))
        .where(not_cancelled, org_orders, Order.created_at >= wd_prev_start_sql, Order.created_at < wd_prev_end_sql)
    )).one()
    wd_rev_pct = pct_change(float(wd_cur[1] or 0), float(wd_prev[1] or 0))

    # Стоп-лист (для cause_hypotheses)
    stoplist_count = int(await db.scalar(
        select(func.count(MenuItem.id)).where(
            MenuItem.organization_id == org_id,
            MenuItem.is_available.is_(False),
            MenuItem.is_archived.is_(False),
        )
    ) or 0)

    weekday_label = now_utc.strftime("%A")  # "Monday", etc.

    insights: list[OperationalInsight] = []

    def add(insight_type: str, severity: str, title: str, text: str) -> None:
        causes = _build_cause_hypotheses(
            cancel_rate_pct=cancel_rate,
            stoplist_count=stoplist_count,
        )
        actions = _build_recommended_actions(
            insight_type,
            stoplist_count=stoplist_count,
            cancel_rate_pct=cancel_rate,
            lost_revenue=lost_rev,
        )
        payload = dict(summary)
        payload["baseline_type"] = "duration_match"
        payload["weekday_baseline"] = {
            "baseline_type": "same_weekday_7d",
            "comparison_label": f"vs прошлый {weekday_label}",
            "current_revenue": round(float(wd_cur[1] or 0), 2),
            "baseline_revenue": round(float(wd_prev[1] or 0), 2),
            "pct_change": wd_rev_pct,
        }
        if causes:
            payload["cause_hypotheses"] = causes
        if actions:
            payload["recommended_actions"] = actions
        insights.append(
            OperationalInsight(
                organization_id=org_id,
                insight_type=insight_type,
                severity=severity,
                title=title,
                summary=text,
                payload_json=payload,
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
            f"Доля отмен выросла на {ch['cancel_rate_pp']} п.п.; потерянная выручка оценивается в {lost_rev:.0f} ₸.",
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


async def detect_ai_incidents(db: AsyncSession, org_id: int) -> list[OperationalInsight]:
    """
    Анализирует AiUsageLog за последние 7 дней и генерирует инсайты при аномалиях:
    - ai_token_spike: сегодня > 3× rolling-7d avg
    - ai_error_spike: error_count/call_count > 15%
    - ai_latency_spike: p95_latency_ms > 1.5 × SLA threshold
    """
    now = datetime.now(timezone.utc)
    today_date = now.date()
    week_ago = (now - timedelta(days=7)).date()

    rows = (await db.execute(
        select(AiUsageLog)
        .where(AiUsageLog.organization_id == org_id, AiUsageLog.day >= week_ago)
        .order_by(AiUsageLog.day.asc())
    )).scalars().all()

    if not rows:
        return []

    generated: list[OperationalInsight] = []
    today_row = next((r for r in rows if r.day == today_date), None)
    prev_rows = [r for r in rows if r.day < today_date]

    async def _upsert_insight(
        insight_type: str, severity: str, title: str, summary: str, payload: dict
    ) -> None:
        idempotency = f"ai_incident:{org_id}:{insight_type}:{today_date}"
        existing = await db.scalar(
            select(OperationalInsight).where(
                OperationalInsight.organization_id == org_id,
                OperationalInsight.insight_type == insight_type,
                OperationalInsight.status == "new",
                OperationalInsight.created_at >= _sql_dt_for_filter(now - timedelta(hours=12)),
            ).limit(1)
        )
        if existing:
            return
        ins = OperationalInsight(
            organization_id=org_id,
            insight_type=insight_type,
            severity=severity,
            title=title,
            summary=summary,
            payload_json={**payload, "_idempotency": idempotency},
            status="new",
        )
        db.add(ins)
        await db.flush()
        generated.append(ins)

    # --- Token spike ---
    if today_row and prev_rows:
        avg_tokens = sum(r.total_tokens for r in prev_rows) / len(prev_rows)
        if avg_tokens > 0 and today_row.total_tokens > avg_tokens * 3:
            await _upsert_insight(
                "ai_token_spike", "warning",
                "Аномальный расход токенов AI сегодня",
                f"Сегодня использовано {today_row.total_tokens:,} токенов "
                f"(≈{int(avg_tokens):,} в среднем за прошлую неделю — превышение в {today_row.total_tokens / avg_tokens:.1f}×).",
                {"today_tokens": today_row.total_tokens, "avg_tokens_7d": round(avg_tokens)},
            )

    # --- Error spike ---
    if today_row and today_row.call_count > 5:
        error_rate = today_row.error_count / today_row.call_count
        if error_rate > 0.15:
            await _upsert_insight(
                "ai_error_spike", "critical",
                f"Высокая частота ошибок AI-провайдера ({int(error_rate*100)}%)",
                f"За сегодня {today_row.error_count} из {today_row.call_count} запросов завершились ошибкой провайдера. "
                "Проверьте статус OpenAI / Gemini и квоты API.",
                {"error_count": today_row.error_count, "call_count": today_row.call_count},
            )

    # --- Latency spike ---
    if today_row and today_row.p95_latency_ms:
        threshold = settings.sla_llm_p95_ms * 1.5
        if today_row.p95_latency_ms > threshold:
            await _upsert_insight(
                "ai_latency_spike", "warning",
                f"Повышенная задержка LLM (p95 = {today_row.p95_latency_ms} ms)",
                f"95-й перцентиль задержки AI составил {today_row.p95_latency_ms} ms "
                f"(порог SLA: {settings.sla_llm_p95_ms} ms). Возможна деградация провайдера.",
                {"p95_ms": today_row.p95_latency_ms, "sla_threshold_ms": settings.sla_llm_p95_ms},
            )

    return generated


async def list_insights(db: AsyncSession, org_id: int, *, limit: int = 20) -> list[OperationalInsight]:
    # P4: запускаем детектор AI-инцидентов при каждом запросе insights (lazy detection)
    try:
        await detect_ai_incidents(db, org_id)
    except Exception:
        logger.debug("AI incident detection skipped org=%s", org_id, exc_info=True)

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


async def build_state_snapshot(
    db: AsyncSession,
    org_id: int,
    *,
    persist: bool = True,
    location_id: int | None = None,
    allowed_location_ids: set[int] | None = None,
) -> RestaurantStateSnapshot:
    now = datetime.now(timezone.utc)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_sql = _sql_dt_for_filter(today)
    now_sql = _sql_dt_for_filter(now)
    org_orders = orders_tenant_clause(org_id)
    order_location_scope = orders_location_filter(allowed_location_ids, location_id)
    chat_location_scope = chat_logs_location_filter(allowed_location_ids, location_id)

    draft = int(
        await db.scalar(
            select(func.count(Order.id)).where(
                org_orders,
                order_location_scope,
                Order.status == OrderStatus.DRAFT.value,
            )
        )
        or 0
    )
    confirmed = int(
        await db.scalar(
            select(func.count(Order.id)).where(
                org_orders,
                order_location_scope,
                Order.status.in_([OrderStatus.CONFIRMED.value, OrderStatus.SENDING_TO_IIKO.value]),
            )
        )
        or 0
    )
    active = int(
        await db.scalar(
            select(func.count(Order.id)).where(
                org_orders,
                order_location_scope,
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
                order_location_scope,
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
                order_location_scope,
                Order.status == OrderStatus.CANCELLED.value,
                Order.created_at >= today_sql,
                Order.created_at <= now_sql,
            )
        )
        or 0
    )
    stoplist = int(
        await db.scalar(
            select(func.count(MenuItem.id)).where(
                MenuItem.organization_id == org_id,
                MenuItem.is_available.is_(False),
                MenuItem.is_archived.is_(False),
            ),
        )
        or 0
    )
    operator_msgs_15m = int(
        await db.scalar(
            select(func.count(ChatLog.id)).where(
                ChatLog.organization_id == org_id,
                ChatLog.role == "operator",
                ChatLog.created_at >= _sql_dt_for_filter(now - timedelta(minutes=15)),
                chat_location_scope,
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
        payload_json={
            "today_orders": today_orders,
            "operator_messages_15m": operator_msgs_15m,
            "location_id": int(location_id) if location_id is not None else None,
        },
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
