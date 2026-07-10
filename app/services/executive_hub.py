"""Executive Hub — narrative cards over existing intelligence/analytics layers."""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import IikoSyncRun, OperationalInsight, SalesDailyAgg, SalesFactItem, SalesFactOrder
from app.services.copilot.business_questions import questions_for_role
from app.services.intelligence import list_insights, revenue_orders_summary
from app.services.iiko_olap_sales_sync import SOURCE_IIKO_OLAP, SYNC_KIND_OLAP_SALES
from app.services.owner_intelligence import build_owner_intelligence_summary
from app.services.revenue_leak import build_revenue_leak


logger = logging.getLogger(__name__)
_SUMMARY_TIMEOUT_SEC = 3.0
_OLAP_TIMEOUT_SEC = 2.0
_LEAK_TIMEOUT_SEC = 2.0
_INSIGHTS_TIMEOUT_SEC = 1.5
_OWNER_TIMEOUT_SEC = 2.5


def _severity_from_delta(pct: float | None, *, warning_below: float = -5.0, critical_below: float = -15.0) -> str:
    if pct is None:
        return "info"
    if pct <= critical_below:
        return "critical"
    if pct <= warning_below:
        return "warning"
    if pct >= 10:
        return "info"
    return "info"


def _format_trend(pct: float | None) -> str:
    if pct is None:
        return "без сравнения с прошлым периодом"
    if pct > 0:
        return f"выросла на {pct:.0f}% к прошлому периоду"
    if pct < 0:
        return f"упала на {abs(pct):.0f}% к прошлому периоду"
    return "на уровне прошлого периода"


def _money(value: float | int | None) -> str:
    return f"{float(value or 0):,.0f} ₸".replace(",", " ")


def _metric(label: str, value: str, hint: str = "", *, severity: str = "info") -> dict[str, str]:
    return {"label": label, "value": value, "hint": hint, "severity": severity}


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, dict):
        rows = value.get("rows") or value.get("items") or value.get("data")
        if isinstance(rows, list):
            return rows
        return list(value.values())
    return []


def _pct_change(current: float, previous: float) -> float | None:
    if previous == 0:
        return None if current == 0 else 100.0
    return round((current - previous) / previous * 100, 1)


def _empty_sales_summary(period: str, *, source: str = "orders") -> dict[str, Any]:
    today = datetime.now(tz=timezone.utc).date()
    return {
        "period": period or "today",
        "date_from": today.isoformat(),
        "date_to": today.isoformat(),
        "source": source,
        "current": {
            "revenue": 0,
            "orders": 0,
            "avg_check": 0,
            "cancelled_orders": 0,
            "cancel_rate_pct": 0,
        },
        "previous": {
            "revenue": 0,
            "orders": 0,
            "avg_check": 0,
            "cancelled_orders": 0,
            "cancel_rate_pct": 0,
        },
        "changes": {
            "revenue_pct": None,
            "orders_pct": None,
            "avg_check_pct": None,
            "cancelled_orders_pct": None,
            "cancel_rate_pp": 0,
        },
        "top_items": [],
        "lost_revenue_estimate": 0,
    }


def _empty_leak() -> dict[str, Any]:
    return {
        "total_leak_kzt": 0,
        "action_risk_kzt": 0,
        "recovered_today_kzt": 0,
        "aov": 0,
        "surfaces": [],
        "breakdown": {},
        "labels": {},
    }


def _empty_owner_summary(period: str) -> dict[str, Any]:
    return {
        "period": period or "today",
        "accepted_revenue": 0,
        "recovered_revenue": 0,
        "upsell_revenue": 0,
        "lost_revenue": 0,
        "prevented_risk_value": 0,
        "ai_cost": 0,
        "net_roi": 0,
        "top_losses": [],
        "top_actions": [],
        "menu_profit_preview": {},
    }


async def _wait_or_fallback(
    db: AsyncSession,
    label: str,
    coro: Any,
    fallback: Any,
    *,
    timeout: float,
) -> Any:
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except Exception as exc:
        logger.warning("executive_hub.%s fallback after error/timeout: %s", label, exc)
        try:
            await db.rollback()
        except Exception:
            logger.debug("executive_hub.%s rollback failed", label, exc_info=True)
        return fallback


def _period_dates(period: str) -> tuple[date, date, date, date]:
    today = datetime.now(tz=timezone.utc).date()
    tag = (period or "today").strip().lower()
    if tag == "30d":
        days = 30
    elif tag == "7d":
        days = 7
    else:
        days = 1
    cur_start = today - timedelta(days=days - 1)
    cur_end = today
    prev_end = cur_start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=days - 1)
    return cur_start, cur_end, prev_start, prev_end


async def _olap_sales_summary(db: AsyncSession, organization_id: int, period: str) -> dict[str, Any] | None:
    cur_start, cur_end, prev_start, prev_end = _period_dates(period)

    async def _agg(start: date, end: date) -> tuple[float, int]:
        row = (
            await db.execute(
                select(
                    func.coalesce(func.sum(SalesDailyAgg.total_revenue), 0),
                    func.coalesce(func.sum(SalesDailyAgg.order_count), 0),
                ).where(
                    SalesDailyAgg.organization_id == int(organization_id),
                    SalesDailyAgg.source == SOURCE_IIKO_OLAP,
                    SalesDailyAgg.date >= start,
                    SalesDailyAgg.date <= end,
                ),
            )
        ).one()
        return float(row[0] or 0), int(row[1] or 0)

    cur_revenue, cur_orders = await _agg(cur_start, cur_end)
    if cur_revenue <= 0 and cur_orders <= 0:
        return None
    prev_revenue, prev_orders = await _agg(prev_start, prev_end)
    cur_avg = cur_revenue / cur_orders if cur_orders else 0
    prev_avg = prev_revenue / prev_orders if prev_orders else 0

    top_rows = (
        await db.execute(
            select(
                SalesFactItem.product_name,
                func.coalesce(func.sum(SalesFactItem.quantity), 0),
                func.coalesce(func.sum(SalesFactItem.revenue), 0),
            )
            .join(SalesFactOrder, SalesFactOrder.id == SalesFactItem.order_id)
            .where(
                SalesFactItem.organization_id == int(organization_id),
                SalesFactOrder.organization_id == int(organization_id),
                SalesFactOrder.order_date >= cur_start,
                SalesFactOrder.order_date <= cur_end,
            )
            .group_by(SalesFactItem.product_name)
            .order_by(func.coalesce(func.sum(SalesFactItem.revenue), 0).desc())
            .limit(5),
        )
    ).all()
    last_sync = (
        await db.execute(
            select(IikoSyncRun)
            .where(
                IikoSyncRun.organization_id == int(organization_id),
                IikoSyncRun.sync_kind == SYNC_KIND_OLAP_SALES,
            )
            .order_by(IikoSyncRun.finished_at.desc())
            .limit(1),
        )
    ).scalar_one_or_none()
    return {
        "period": period or "today",
        "date_from": cur_start.isoformat(),
        "date_to": cur_end.isoformat(),
        "source": SOURCE_IIKO_OLAP,
        "current": {
            "revenue": round(cur_revenue, 2),
            "orders": cur_orders,
            "avg_check": round(cur_avg, 2),
            "cancelled_orders": 0,
            "cancel_rate_pct": 0,
        },
        "previous": {
            "revenue": round(prev_revenue, 2),
            "orders": prev_orders,
            "avg_check": round(prev_avg, 2),
            "cancelled_orders": 0,
            "cancel_rate_pct": 0,
        },
        "changes": {
            "revenue_pct": _pct_change(cur_revenue, prev_revenue),
            "orders_pct": _pct_change(float(cur_orders), float(prev_orders)),
            "avg_check_pct": _pct_change(cur_avg, prev_avg),
            "cancelled_orders_pct": None,
            "cancel_rate_pp": 0,
        },
        "top_items": [
            {"name": str(name or "?"), "quantity": float(qty or 0), "revenue": round(float(revenue or 0), 2)}
            for name, qty, revenue in top_rows
        ],
        "lost_revenue_estimate": 0,
        "olap": {
            "source": SOURCE_IIKO_OLAP,
            "last_sync_at": last_sync.finished_at.isoformat() if last_sync and last_sync.finished_at else None,
            "last_sync_ok": bool(last_sync and last_sync.status == "ok"),
            "last_sync_error": last_sync.error_text if last_sync else None,
        },
    }


def _pct_label(value: float | int | None, *, metric: str) -> str:
    if not isinstance(value, (int, float)):
        return f"{metric}: нет базы для сравнения"
    if value > 0:
        return f"{metric}: +{value:.0f}% к прошлому периоду"
    if value < 0:
        return f"{metric}: −{abs(value):.0f}% к прошлому периоду"
    return f"{metric}: без изменений"


def _forecast_range(revenue: float, orders: int, period: str | None) -> dict[str, Any]:
    if revenue <= 0 or orders <= 0 or (period or "today") != "today":
        return {
            "label": "Прогноз появится после первых заказов",
            "low_kzt": 0,
            "high_kzt": 0,
            "available": False,
        }
    now = datetime.now(timezone.utc)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elapsed_share = max((now - day_start).total_seconds() / 86_400, 0.15)
    projected = revenue / min(elapsed_share, 1.0)
    low = projected * 0.92
    high = projected * 1.08
    return {
        "label": f"Прогноз дня: {_money(low)}–{_money(high)}",
        "low_kzt": round(low, 2),
        "high_kzt": round(high, 2),
        "available": True,
    }


def _action_item(
    *,
    action_id: str,
    label: str,
    action_type: str,
    confirm_required: bool = False,
    payload: dict[str, Any] | None = None,
    drilldown: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": action_id,
        "label": label,
        "action_type": action_type,
        "confirm_required": confirm_required,
        "payload": payload or {},
        "drilldown": drilldown or {},
    }


def _card(
    *,
    card_id: str,
    title: str,
    headline: str,
    summary: str,
    severity: str = "info",
    dimension: str = "ops",
    narrative: str | None = None,
    metrics: dict[str, Any] | None = None,
    why: list[str] | None = None,
    actions: list[str] | None = None,
    action_items: list[dict[str, Any]] | None = None,
    evidence: dict[str, Any] | None = None,
    drilldown: dict[str, Any] | None = None,
    chat_prompt: str | None = None,
) -> dict[str, Any]:
    return {
        "id": card_id,
        "title": title,
        "headline": headline,
        "summary": summary,
        "narrative": narrative or summary,
        "dimension": dimension,
        "severity": severity,
        "metrics": metrics or {},
        "why": why or [],
        "actions": actions or [],
        "action_items": action_items or [],
        "evidence": evidence or {},
        "drilldown": drilldown or {},
        "chat_prompt": chat_prompt or headline,
    }


def _business_summary(
    summary: dict[str, Any],
    leak: dict[str, Any],
    owner_summary: dict[str, Any],
) -> dict[str, Any]:
    current = summary.get("current") or {}
    changes = summary.get("changes") or {}
    revenue = float(current.get("revenue") or 0)
    orders = int(current.get("orders") or 0)
    avg_check = float(current.get("avg_check") or 0)
    revenue_pct = changes.get("revenue_pct")
    total_leak = float(leak.get("total_leak_kzt") or 0)
    net_roi = float(owner_summary.get("net_roi") or 0)

    has_orders = orders > 0 or revenue > 0
    severity = _severity_from_delta(revenue_pct if isinstance(revenue_pct, (int, float)) else None)
    if total_leak >= 30_000:
        severity = "critical"
    elif total_leak >= 10_000 and severity != "critical":
        severity = "warning"

    if not has_orders:
        headline = "Сегодня пока нет заказов"
        narrative = (
            "Это может быть нормой до начала смены. Если ресторан уже работает, проверьте подключение продаж, "
            "очередь клиентов и первый тестовый заказ."
        )
        status = "Нужно понять, это тишина или проблема с данными"
    else:
        headline = f"Сегодня {_money(revenue)} — {_format_trend(revenue_pct)}"
        narrative = (
            f"{orders} заказов, средний чек {_money(avg_check)}. "
            f"Деньги на кону: {_money(total_leak)}. Чистый эффект ИИ: {_money(net_roi)}."
        )
        status = "Есть что смотреть по деньгам" if severity in {"warning", "critical"} else "День под контролем"

    return {
        "headline": headline,
        "status": status,
        "narrative": narrative,
        "severity": severity,
        "has_orders": has_orders,
        "stats": [
            _metric(
                "Выручка",
                _money(revenue),
                _format_trend(revenue_pct),
                severity=severity if revenue_pct is not None else "info",
            ),
            _metric("Заказы", str(orders), "текущий поток"),
            _metric("Средний чек", _money(avg_check), "качество чека"),
            _metric(
                "Деньги на кону",
                _money(total_leak),
                "потери, зависшие действия и риски",
                severity="warning" if total_leak > 0 else "info",
            ),
            _metric("Эффект ИИ", _money(net_roi), "принято, допродано и потеряно"),
        ],
    }


def _focused_view_for_card(card: dict[str, Any], summary: dict[str, Any], leak: dict[str, Any], owner_summary: dict[str, Any]) -> dict[str, Any]:
    card_id = str(card.get("id") or "")
    current = summary.get("current") or {}
    previous = summary.get("previous") or {}
    changes = summary.get("changes") or {}
    top_items = summary.get("top_items") or []
    if card_id == "revenue_pulse":
        rows = [
            {"label": "Сегодня", "value": _money(current.get("revenue")), "hint": f"{int(current.get('orders') or 0)} заказов"},
            {"label": "Прошлый период", "value": _money(previous.get("revenue")), "hint": f"{int(previous.get('orders') or 0)} заказов"},
            {"label": "Средний чек", "value": _money(current.get("avg_check")), "hint": _pct_label(changes.get("avg_check_pct"), metric="чек")},
            {"label": "Динамика", "value": _format_trend(changes.get("revenue_pct")), "hint": "выручка"},
        ]
        table = [
            {
                "name": str(row.get("name") or "?"),
                "metric": f"{float(row.get('quantity') or 0):.0f} шт.",
                "value": _money(row.get("revenue")),
            }
            for row in top_items[:6]
            if isinstance(row, dict)
        ]
        return {
            "title": "Разбор выручки",
            "summary": "Смотрим, что изменило деньги: поток заказов, средний чек, отмены и топ позиций.",
            "kpis": rows,
            "drivers": _money_drivers(summary, owner_summary),
            "table_title": "Топ позиций по выручке",
            "table": table,
            "actions": card.get("action_items") or [],
            "chat_prompt": "Разбери выручку по часам, позициям, заказам и среднему чеку. Что сделать сегодня?",
        }
    if card_id == "money_at_risk":
        risk = _money_at_risk_breakdown(leak, owner_summary)
        return {
            "title": "Разбор денег на кону",
            "summary": risk.get("headline") or "Смотрим источники потенциальных потерь.",
            "kpis": [
                {"label": "На кону", "value": _money(risk.get("total_kzt")), "hint": "оценка риска"},
                {"label": "Возвращено", "value": _money(leak.get("recovered_today_kzt")), "hint": "сегодня"},
                {"label": "Средний чек", "value": _money(leak.get("aov")), "hint": "для оценки потерь"},
            ],
            "drivers": risk.get("rows") or [],
            "table_title": "Источники риска",
            "table": [
                {"name": row.get("label"), "metric": row.get("severity"), "value": row.get("value")}
                for row in (risk.get("rows") or [])
            ],
            "actions": card.get("action_items") or [],
            "chat_prompt": str(risk.get("chat_prompt") or card.get("chat_prompt") or ""),
        }
    if card_id == "owner_roi":
        return {
            "title": "Разбор вклада ИИ",
            "summary": "Финансовый след ИИ: принятая выручка, допродажи, потери и где нужен оператор.",
            "kpis": [
                {"label": "Чистый эффект", "value": _money(owner_summary.get("net_roi")), "hint": "после оценённых потерь"},
                {"label": "Принято ИИ", "value": _money(owner_summary.get("accepted_revenue")), "hint": "подтверждённая выручка"},
                {"label": "Допродажи", "value": _money(owner_summary.get("upsell_revenue")), "hint": "добавлено к чеку"},
                {"label": "Потери", "value": _money(owner_summary.get("lost_revenue")), "hint": "где уступил/не довёл"},
            ],
            "drivers": [str(x.get("label") or x.get("title") or x) for x in (owner_summary.get("top_losses") or [])[:5]],
            "table_title": "Что проверить",
            "table": [
                {"name": str(x.get("label") or x.get("title") or x), "metric": "риск", "value": _money(x.get("amount_kzt") or x.get("value_kzt") or 0)}
                for x in (owner_summary.get("top_losses") or [])[:5]
                if isinstance(x, dict)
            ],
            "actions": card.get("action_items") or [],
            "chat_prompt": "Покажи, где ИИ принёс деньги, где потерял и какие правила допродаж включить.",
        }
    if card_id in {"margin_data_gap", "margin_risk"}:
        preview = owner_summary.get("menu_profit_preview") or {}
        missing = _as_list(preview.get("missing_cost_checklist"))
        candidates = _as_list(preview.get("price_increase_candidates") or preview.get("promote_today"))
        return {
            "title": "Разбор маржи и себестоимости",
            "summary": "Показываем, какие позиции мешают точному контролю маржи и фудкоста.",
            "kpis": [
                {"label": "Без себестоимости", "value": str(len(missing)), "hint": "позиции"},
                {"label": "Кандидаты", "value": str(len(candidates)), "hint": "для проверки цены/маржи"},
            ],
            "drivers": [str((row or {}).get("name") or row) for row in [*missing[:3], *candidates[:3]]],
            "table_title": "Позиции для проверки",
            "table": [
                {
                    "name": str((row or {}).get("name") or (row or {}).get("dish_name") or row),
                    "metric": str((row or {}).get("category") or "меню") if isinstance(row, dict) else "меню",
                    "value": str((row or {}).get("margin_pct") or "нет cost price") if isinstance(row, dict) else "нет cost price",
                }
                for row in [*missing[:6], *candidates[:6]]
                if row
            ],
            "actions": card.get("action_items") or [],
            "chat_prompt": "С чего начать заполнение себестоимости и какие блюда проверить по марже?",
        }
    return {
        "title": str(card.get("title") or "Разбор сигнала"),
        "summary": str(card.get("summary") or card.get("narrative") or ""),
        "kpis": [
            {"label": key, "value": str(value), "hint": ""}
            for key, value in list((card.get("metrics") or {}).items())[:4]
        ],
        "drivers": card.get("why") or [],
        "table_title": "Основания",
        "table": [{"name": str(x), "metric": "причина", "value": ""} for x in (card.get("why") or [])[:6]],
        "actions": card.get("action_items") or [],
        "chat_prompt": str(card.get("chat_prompt") or ""),
    }


def _next_actions_from_cards(cards: list[dict[str, Any]], business_summary: dict[str, Any]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    if not business_summary.get("has_orders"):
        actions.extend(
            [
                {
                    "id": "create_test_order",
                    "title": "Создать тестовый заказ",
                    "reason": "Проверить, что продажи и бот попадают в аналитику.",
                    "severity": "warning",
                    "action_item": _action_item(
                        action_id="open_orders_for_test",
                        label="Открыть заказы",
                        action_type="navigate",
                        drilldown={"tab": "orders"},
                    ),
                },
                {
                    "id": "check_integrations",
                    "title": "Проверить подключение продаж",
                    "reason": "Если смена уже идёт, нули могут означать проблему с синхронизацией.",
                    "severity": "warning",
                    "action_item": _action_item(
                        action_id="open_integrations",
                        label="Проверить интеграции",
                        action_type="navigate",
                        drilldown={"tab": "settings", "settingsTab": "connections"},
                    ),
                },
            ]
        )

    ranked = sorted(
        cards,
        key=lambda card: _score_from_severity(str(card.get("severity") or "info")),
    )
    for card in ranked:
        card_actions = [a for a in (card.get("action_items") or []) if isinstance(a, dict)]
        if not card_actions:
            continue
        action = card_actions[0]
        actions.append(
            {
                "id": f"card_{card.get('id')}",
                "title": str(card.get("headline") or card.get("title") or "Разобрать сигнал"),
                "reason": str(card.get("summary") or card.get("narrative") or ""),
                "severity": str(card.get("severity") or "info"),
                "card_id": card.get("id"),
                "action_item": action,
            }
        )
        if len(actions) >= 5:
            break
    return actions[:5]


def _money_drivers(summary: dict[str, Any], owner_summary: dict[str, Any]) -> list[dict[str, Any]]:
    changes = summary.get("changes") or {}
    current = summary.get("current") or {}
    top_items = summary.get("top_items") or []
    upsell = float(owner_summary.get("upsell_revenue") or 0)
    drivers = [
        {
            "id": "orders_flow",
            "label": "Поток заказов",
            "value": _pct_label(changes.get("orders_pct"), metric="заказы"),
            "severity": _severity_from_delta(changes.get("orders_pct") if isinstance(changes.get("orders_pct"), (int, float)) else None),
            "card_id": "revenue_pulse",
            "chat_prompt": "Разложи изменение выручки на поток заказов, средний чек и отмены.",
        },
        {
            "id": "avg_check",
            "label": "Средний чек",
            "value": _pct_label(changes.get("avg_check_pct"), metric="средний чек"),
            "severity": _severity_from_delta(changes.get("avg_check_pct") if isinstance(changes.get("avg_check_pct"), (int, float)) else None),
            "card_id": "revenue_pulse",
            "chat_prompt": "Почему изменился средний чек и какие позиции или допродажи повлияли?",
        },
        {
            "id": "cancellations",
            "label": "Отмены",
            "value": f"{int(current.get('cancelled_orders') or 0)} отмен, доля {float(current.get('cancel_rate_pct') or 0):.1f}%",
            "severity": "warning" if float(current.get("cancel_rate_pct") or 0) >= 8 else "info",
            "card_id": "ops_status",
            "chat_prompt": "Какие отмены сегодня бьют по деньгам и что сделать?",
        },
        {
            "id": "upsell",
            "label": "Допродажи ИИ",
            "value": f"+{_money(upsell)} к чеку" if upsell else "пока нет подтверждённых допродаж",
            "severity": "info" if upsell else "warning",
            "card_id": "owner_roi",
            "chat_prompt": "Где ИИ мог допродать больше и какое правило стоит включить?",
        },
    ]
    if top_items:
        names = ", ".join(str(row.get("name") or "?") for row in top_items[:3] if isinstance(row, dict))
        drivers.append(
            {
                "id": "top_items",
                "label": "Топ позиций",
                "value": names or "нет позиций",
                "severity": "info",
                "card_id": "revenue_pulse",
                "chat_prompt": "Какие категории и позиции сегодня ведут выручку?",
            }
        )
    return drivers[:5]


def _money_at_risk_breakdown(leak: dict[str, Any], owner_summary: dict[str, Any]) -> dict[str, Any]:
    total = float(leak.get("total_leak_kzt") or 0)
    breakdown = leak.get("breakdown") or {}
    labels = leak.get("labels") or {}
    rows: list[dict[str, Any]] = []
    for key, amount in sorted(breakdown.items(), key=lambda item: float(item[1] or 0), reverse=True):
        value = float(amount or 0)
        normalized = str(key).removesuffix("_kzt")
        label = str(labels.get(normalized) or labels.get(key) or normalized.replace("_", " "))
        rows.append(
            {
                "id": str(key),
                "label": label,
                "value": _money(value),
                "amount_kzt": round(value, 2),
                "severity": "warning" if value > 0 else "info",
                "card_id": "money_at_risk",
                "chat_prompt": f"Разбери источник риска денег: {label}.",
            }
        )
    preview = owner_summary.get("menu_profit_preview") or {}
    missing_cost = preview.get("missing_cost_checklist") or []
    if missing_cost:
        rows.append(
            {
                "id": "missing_cost",
                "label": "Позиции без себестоимости",
                "value": f"{len(missing_cost)} позиций",
                "amount_kzt": 0,
                "severity": "warning",
                "card_id": "margin_data_gap",
                "chat_prompt": "Покажи позиции без себестоимости и с чего начать заполнение.",
            }
        )
    return {
        "headline": f"На кону {_money(total)}" if total else "Критичных утечек денег сейчас не видно",
        "total_kzt": round(total, 2),
        "rows": rows[:6],
        "chat_prompt": "Где сегодня деньги на кону и какие действия дадут быстрый эффект?",
    }


def _network_branch_brief(owner_summary: dict[str, Any]) -> dict[str, Any]:
    bench = owner_summary.get("location_benchmark_preview") or {}
    if not isinstance(bench, dict) or not bench.get("enabled"):
        return {"enabled": False, "rows": [], "actions": []}

    best = bench.get("best_location") or {}
    worst = bench.get("worst_location") or {}
    reasons = bench.get("location_decline_reasons") or bench.get("decline_reasons") or []
    rows = []
    for row in (bench.get("locations") or [])[:6]:
        if not isinstance(row, dict):
            continue
        name = row.get("organization_name") or row.get("location_name") or row.get("name") or f"Филиал #{row.get('organization_id') or row.get('id') or '?'}"
        revenue = row.get("revenue") or row.get("revenue_kzt") or row.get("org_revenue_kzt") or 0
        rank = row.get("rank_label") or row.get("rank") or ""
        rows.append(
            {
                "name": str(name),
                "metric": str(rank or "филиал"),
                "value": _money(revenue),
                "severity": "warning" if row.get("is_declining") or row.get("decline_pct") else "info",
            }
        )

    headline = "Сеть без явного лидера/просадки"
    if best or worst:
        best_name = best.get("organization_name") or best.get("location_name") or best.get("name")
        worst_name = worst.get("organization_name") or worst.get("location_name") or worst.get("name")
        if best_name and worst_name and best_name != worst_name:
            headline = f"Лучше всех: {best_name}. Просадка: {worst_name}"
        elif best_name:
            headline = f"Лучше всех сейчас: {best_name}"

    driver_lines: list[dict[str, Any]] = []
    for idx, reason in enumerate(reasons[:5]):
        if isinstance(reason, dict):
            label = reason.get("label") or reason.get("title") or reason.get("reason") or "Причина просадки"
            value = reason.get("summary") or reason.get("value") or reason.get("detail") or ""
            severity = reason.get("severity") or "warning"
        else:
            label = str(reason)
            value = "Проверьте филиал, часы, категории и средний чек."
            severity = "warning"
        driver_lines.append(
            {
                "id": f"branch_reason_{idx}",
                "label": str(label),
                "value": str(value),
                "severity": str(severity),
                "card_id": "network_branch_intelligence",
                "chat_prompt": f"Разбери сетевую просадку: {label}. Что владелец должен сделать сегодня?",
            }
        )

    return {
        "enabled": True,
        "title": "Филиалы сети",
        "headline": headline,
        "summary": "Сравнение точек: кто тащит выручку, где просадка и какие причины проверить управляющему.",
        "severity": "warning" if driver_lines or worst else "info",
        "best_location": best,
        "worst_location": worst,
        "network_averages": bench.get("network_averages") or {},
        "drivers": driver_lines,
        "rows": rows,
        "actions": [
            _action_item(
                action_id="open_network_benchmark",
                label="Открыть сравнение филиалов",
                action_type="navigate",
                drilldown={"tab": "ai_center", "aiCenterTab": "network_benchmark"},
            ),
        ],
        "chat_prompt": "Сравни филиалы сети: где деньги просели, почему и что поручить управляющим.",
    }


def _owner_cards(
    summary: dict[str, Any],
    leak: dict[str, Any],
    owner_summary: dict[str, Any],
    cards: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    current = summary.get("current") or {}
    previous = summary.get("previous") or {}
    changes = summary.get("changes") or {}
    revenue = float(current.get("revenue") or 0)
    orders = int(current.get("orders") or 0)
    avg_check = float(current.get("avg_check") or 0)
    total_leak = float(leak.get("total_leak_kzt") or 0)
    net_roi = float(owner_summary.get("net_roi") or 0)
    lost = float(owner_summary.get("lost_revenue") or 0)
    upsell = float(owner_summary.get("upsell_revenue") or 0)
    margin_card = next((card for card in cards if card.get("id") in {"margin_data_gap", "margin_risk"}), None)
    ops_card = next((card for card in cards if card.get("id") == "ops_status"), None)
    return [
        {
            "id": "owner_money",
            "title": "Деньги",
            "headline": f"{_money(revenue)} сегодня",
            "summary": f"{orders} заказов, средний чек {_money(avg_check)}. Вчера/прошлый период: {_money(previous.get('revenue'))}.",
            "severity": _severity_from_delta(changes.get("revenue_pct") if isinstance(changes.get("revenue_pct"), (int, float)) else None),
            "facts": [_pct_label(changes.get("revenue_pct"), metric="выручка"), _pct_label(changes.get("orders_pct"), metric="заказы")],
            "card_id": "revenue_pulse",
            "chat_prompt": "Почему изменились деньги сегодня: заказы, чек, категории, отмены и допродажи?",
        },
        {
            "id": "owner_ops",
            "title": "Операции",
            "headline": ops_card.get("headline") if ops_card else "Смена под контролем",
            "summary": ops_card.get("summary") if ops_card else "Критичных операционных сигналов нет.",
            "severity": str((ops_card or {}).get("severity") or "info"),
            "facts": [
                f"Отмены: {int(current.get('cancelled_orders') or 0)}",
                f"Потери/риски: {_money(total_leak)}",
            ],
            "card_id": "ops_status",
            "chat_prompt": "Что в операциях может ударить по выручке до конца дня?",
        },
        {
            "id": "owner_ai_clients",
            "title": "ИИ и клиенты",
            "headline": f"Чистый эффект ИИ {_money(net_roi)}",
            "summary": f"Допродажи +{_money(upsell)}, оценённые потери −{_money(lost)}.",
            "severity": "critical" if lost >= 30_000 else ("warning" if lost > 0 or not upsell else "info"),
            "facts": [
                (margin_card.get("summary") if margin_card else "Маржа готова к контролю."),
                "Кликните, чтобы агент разобрал выбранный сигнал.",
            ],
            "card_id": "owner_roi",
            "chat_prompt": "Покажи финансовый вклад ИИ: принятые заказы, допродажи, потери и где нужен оператор.",
        },
    ]


def _today_picture(summary: dict[str, Any], leak: dict[str, Any], owner_summary: dict[str, Any]) -> dict[str, Any]:
    current = summary.get("current") or {}
    changes = summary.get("changes") or {}
    revenue = float(current.get("revenue") or 0)
    orders = int(current.get("orders") or 0)
    avg_check = float(current.get("avg_check") or 0)
    total_leak = float(leak.get("total_leak_kzt") or 0)
    net_roi = float(owner_summary.get("net_roi") or 0)
    forecast = _forecast_range(revenue, orders, summary.get("period"))
    has_orders = orders > 0 or revenue > 0
    if has_orders:
        headline = (
            f"Сегодня {_money(revenue)}, {_format_trend(changes.get('revenue_pct'))}. "
            f"{orders} заказов, средний чек {_money(avg_check)}. {forecast['label']}."
        )
        status = "Есть риски по деньгам" if total_leak > 0 else "День под контролем"
    else:
        headline = "Сегодня ещё нет заказов"
        status = "Проверьте: это начало смены или не приходят данные"
    return {
        "headline": headline,
        "status": status,
        "has_orders": has_orders,
        "forecast": forecast,
        "today": {
            "revenue_kzt": round(revenue, 2),
            "orders": orders,
            "avg_check_kzt": round(avg_check, 2),
            "money_at_risk_kzt": round(total_leak, 2),
            "ai_net_effect_kzt": round(net_roi, 2),
        },
        "comparison": {
            "revenue_pct": changes.get("revenue_pct"),
            "orders_pct": changes.get("orders_pct"),
            "avg_check_pct": changes.get("avg_check_pct"),
            "label": _format_trend(changes.get("revenue_pct")),
        },
        "chat_prompt": "Дай owner-сводку дня: что происходит, почему изменились деньги, где риски и что сделать.",
    }


def _readiness_state(
    summary: dict[str, Any],
    owner_summary: dict[str, Any],
    cards: list[dict[str, Any]],
) -> dict[str, Any]:
    current = summary.get("current") or {}
    orders = int(current.get("orders") or 0)
    revenue = float(current.get("revenue") or 0)
    margin_gap = next((card for card in cards if card.get("id") == "margin_data_gap"), None)
    mode = "runtime" if orders > 0 or revenue > 0 else "onboarding"
    ai_has_money = float(owner_summary.get("accepted_revenue") or 0) or float(owner_summary.get("upsell_revenue") or 0)
    return {
        "mode": mode,
        "items": [
            {
                "label": "Продажи приходят",
                "status": "ok" if orders > 0 or revenue > 0 else "action",
                "text": "Есть заказы за период." if orders > 0 or revenue > 0 else "Нет заказов за период — проверьте смену или создайте тестовый заказ.",
            },
            {
                "label": "Себестоимость заполнена",
                "status": "action" if margin_gap else "ok",
                "text": str(margin_gap.get("summary")) if margin_gap else "Маржа готова к ежедневному контролю.",
            },
            {
                "label": "ИИ считает вклад",
                "status": "ok" if ai_has_money else "watch",
                "text": "Есть финансовый след ИИ." if ai_has_money else "Пока мало событий, эффект ИИ будет точнее после заказов.",
            },
        ],
    }


def _owner_readiness_blocks(summary: dict[str, Any], owner_summary: dict[str, Any], cards: list[dict[str, Any]]) -> dict[str, Any]:
    current = summary.get("current") or {}
    orders = int(current.get("orders") or 0)
    revenue = float(current.get("revenue") or 0)
    has_sales = orders > 0 or revenue > 0
    source = str(summary.get("source") or "orders")
    olap = summary.get("olap") or {}
    margin_gap = next((card for card in cards if card.get("id") == "margin_data_gap"), None)
    onboarding: list[dict[str, Any]] = [
        {
            "id": "connect_iiko",
            "title": "Подключить iiko и продажи",
            "status": "ok" if source == SOURCE_IIKO_OLAP or has_sales else "action",
            "text": "Продажи из iiko приходят в сводку владельца." if source == SOURCE_IIKO_OLAP else "Запустите синхронизацию продаж или примите первый заказ.",
            "action": {"label": "Синхронизировать продажи", "type": "api", "endpoint": "/api/admin/intelligence/iiko-olap-sync?days=3"},
        },
        {
            "id": "first_order",
            "title": "Есть первые продажи",
            "status": "ok" if has_sales else "action",
            "text": "Hub видит выручку и заказы." if has_sales else "Пока нет продаж за период: это может быть начало смены или проблема синхронизации.",
            "action": {"label": "Открыть заказы", "type": "navigate", "target": {"tab": "orders"}},
        },
        {
            "id": "cost_price",
            "title": "Себестоимость для маржи",
            "status": "action" if margin_gap else "ok",
            "text": str(margin_gap.get("summary")) if margin_gap else "Маржа готова к ежедневному контролю.",
            "action": {"label": "Меню и себестоимость", "type": "navigate", "target": {"tab": "menu"}},
        },
    ]
    runtime: list[dict[str, Any]] = []
    if olap and not olap.get("last_sync_ok"):
        runtime.append(
            {
                "id": "olap_sync_failed",
                "title": "Данные продаж iiko требуют проверки",
                "severity": "warning",
                "text": str(olap.get("last_sync_error") or "Последняя синхронизация продаж не подтверждена."),
                "action": {"label": "Синхронизировать продажи", "type": "api", "endpoint": "/api/admin/intelligence/iiko-olap-sync?days=3"},
            }
        )
    if not has_sales:
        runtime.append(
            {
                "id": "no_sales_data",
                "title": "Нет продаж за период",
                "severity": "warning",
                "text": "Если ресторан уже работает, проверьте iiko, WhatsApp и фоновые процессы.",
                "action": {"label": "Проверить проблемы с данными", "type": "navigate", "target": {"tab": "incidents"}},
            }
        )
    if float(owner_summary.get("lost_revenue") or 0) > 0:
        runtime.append(
            {
                "id": "ai_loss",
                "title": "Есть оценённые потери ИИ/операций",
                "severity": "warning",
                "text": f"Потери оцениваются в {_money(owner_summary.get('lost_revenue'))}.",
                "action": {"label": "Разобрать ИИ", "type": "chat", "prompt": "Где сегодня ИИ или оператор потеряли деньги?"},
            }
        )
    return {
        "mode": "runtime" if has_sales else "onboarding",
        "onboarding": onboarding,
        "runtime": runtime[:5],
    }


def _revenue_pulse_card(summary: dict[str, Any]) -> dict[str, Any]:
    current = summary.get("current") or {}
    changes = summary.get("changes") or {}
    revenue_pct = changes.get("revenue_pct")
    orders_pct = changes.get("orders_pct")
    avg_check_pct = changes.get("avg_check_pct")
    revenue = float(current.get("revenue") or 0)
    orders = int(current.get("orders") or 0)
    avg_check = float(current.get("avg_check") or 0)
    why: list[str] = []
    if isinstance(orders_pct, (int, float)) and orders_pct < -5:
        why.append("меньше заказов, чем в прошлом периоде")
    if isinstance(avg_check_pct, (int, float)) and avg_check_pct < -5:
        why.append("просел средний чек")
    if isinstance(changes.get("cancel_rate_pp"), (int, float)) and changes["cancel_rate_pp"] > 2:
        why.append("выросла доля отмен")
    if not why:
        why.append("основной драйвер — текущий поток заказов и средний чек")
    narrative = (
        f"За период {orders} заказов на {revenue:,.0f} ₸; средний чек {avg_check:,.0f} ₸, "
        f"динамика {_format_trend(revenue_pct)}."
    ).replace(",", " ")
    return _card(
        card_id="revenue_pulse",
        title="Выручка",
        headline=f"Сегодня {revenue:,.0f} ₸ — {_format_trend(revenue_pct)}".replace(",", " "),
        summary=f"{orders} заказов, средний чек {avg_check:,.0f} ₸".replace(",", " "),
        dimension="money",
        narrative=narrative,
        severity=_severity_from_delta(revenue_pct if isinstance(revenue_pct, (int, float)) else None),
        action_items=[
            _action_item(
                action_id="open_analytics",
                label="Открыть аналитику продаж",
                action_type="navigate",
                drilldown={"tab": "dashboard", "dashboardTab": "analytics"},
            ),
            _action_item(
                action_id="ask_revenue",
                label="Спросить ИИ про выручку",
                action_type="chat",
                payload={"prompt": "Почему изменилась выручка сегодня?"},
            ),
        ],
        metrics={
            "revenue_kzt": round(revenue, 2),
            "orders": orders,
            "avg_check_kzt": round(avg_check, 2),
            "revenue_pct": revenue_pct,
            "orders_pct": orders_pct,
        },
        why=why,
        actions=["Открыть аналитику продаж", "Спросить ИИ, почему изменилась выручка"],
        evidence={"source": "orders", "period": summary.get("period")},
        drilldown={
            "tab": "dashboard",
            "dashboardTab": "analytics",
            "api": "/api/admin/analytics/sales/overview",
            "label": "Подробная аналитика",
        },
        chat_prompt="Почему изменилась выручка сегодня?",
    )


def _money_risk_card(leak: dict[str, Any]) -> dict[str, Any]:
    total = float(leak.get("total_leak_kzt") or 0)
    recovered = float(leak.get("recovered_today_kzt") or 0)
    breakdown = leak.get("breakdown") or {}
    labels = leak.get("labels") or {}
    top_key = max(breakdown, key=lambda k: float(breakdown.get(k) or 0), default=None) if breakdown else None
    top_label = labels.get(top_key, top_key) if top_key else "операционные потери"
    top_amount = float(breakdown.get(top_key) or 0) if top_key else 0.0
    severity = "info"
    if total >= 50_000:
        severity = "critical"
    elif total >= 15_000:
        severity = "warning"
    headline = f"На кону {total:,.0f} ₸".replace(",", " ")
    if recovered > 0:
        headline = f"{headline}, уже вернули {recovered:,.0f} ₸".replace(",", " ")
    narrative = (
        f"Потенциальные потери {total:,.0f} ₸; главный источник — {top_label}."
        if top_amount
        else "Критичных утечек выручки сейчас не видно."
    ).replace(",", " ")
    action_items: list[dict[str, Any]] = [
        _action_item(
            action_id="open_dashboard",
            label="Открыть дашборд",
            action_type="navigate",
            drilldown={"tab": "dashboard"},
        ),
    ]
    if total >= 5000:
        action_items.append(
            _action_item(
                action_id="recover_drafts",
                label="Вернуть брошенные черновики",
                action_type="navigate",
                drilldown={"tab": "inbox", "inboxTab": "clients"},
            ),
        )
    return _card(
        card_id="money_at_risk",
        title="Деньги на кону",
        headline=headline,
        summary=f"Главный источник: {top_label} ({top_amount:,.0f} ₸)".replace(",", " ") if top_amount else "Сейчас критичных утечек нет",
        dimension="money",
        narrative=narrative,
        severity=severity,
        action_items=action_items,
        metrics={
            "total_leak_kzt": round(total, 2),
            "recovered_today_kzt": round(recovered, 2),
            "top_source_kzt": round(top_amount, 2),
        },
        why=[labels.get(k, k) for k, v in sorted(breakdown.items(), key=lambda item: float(item[1] or 0), reverse=True)[:3] if float(v or 0) > 0],
        actions=["Открыть очередь денег", "Вернуть брошенные черновики"],
        evidence={"source": "revenue_leak"},
        drilldown={
            "tab": "dashboard",
            "label": "Дашборд и действия",
        },
        chat_prompt="Где сегодня теряем больше всего денег?",
    )


def _snapshot_insight(insight: OperationalInsight) -> dict[str, Any]:
    """Copy ORM insight fields before later fallback rollbacks can expire it."""
    return {
        "id": insight.id,
        "title": insight.title,
        "summary": insight.summary,
        "severity": insight.severity,
        "confidence_score": insight.confidence_score,
        "payload_json": insight.payload_json or {},
        "evidence_json": insight.evidence_json or {},
    }


def _insight_card(insight: dict[str, Any]) -> dict[str, Any]:
    payload = insight.get("payload_json") or {}
    hypotheses = payload.get("cause_hypotheses") or []
    actions = payload.get("recommended_actions") or []
    insight_id = insight.get("id")
    title = str(insight.get("title") or "")
    summary = str(insight.get("summary") or "")
    return _card(
        card_id=f"insight_{insight_id}",
        title="Главный инсайт",
        headline=title,
        summary=summary,
        dimension="quality",
        narrative=summary,
        severity=str(insight.get("severity") or "info"),
        action_items=[
            _action_item(
                action_id=f"insight_open_{insight_id}",
                label="Открыть инсайт",
                action_type="navigate",
                drilldown={"tab": "ai_center", "aiCenterTab": "insights", "insight_id": insight_id},
            ),
        ],
        metrics={
            "insight_id": insight_id,
            "confidence_score": insight.get("confidence_score"),
        },
        why=[str(x) for x in hypotheses[:3]],
        actions=[str(x) for x in actions[:3]],
        evidence=insight.get("evidence_json") or payload.get("evidence") or {},
        drilldown={
            "tab": "ai_center",
            "aiCenterTab": "insights",
            "insight_id": insight_id,
            "label": "Все инсайты",
        },
        chat_prompt=f"Объясни подробнее: {title}",
    )


def _owner_roi_card(owner_summary: dict[str, Any]) -> dict[str, Any]:
    net_roi = float(owner_summary.get("net_roi") or 0)
    lost = float(owner_summary.get("lost_revenue") or 0)
    accepted = float(owner_summary.get("accepted_revenue") or 0)
    upsell = float(owner_summary.get("upsell_revenue") or 0)
    severity = "info"
    if net_roi < 0:
        severity = "warning"
    if lost >= 30_000:
        severity = "critical"
    return _card(
        card_id="owner_roi",
        title="Эффект ИИ",
        headline=f"Чистый эффект {net_roi:,.0f} ₸".replace(",", " "),
        summary=f"Принято {accepted:,.0f} ₸, допродажи +{upsell:,.0f} ₸, потери −{lost:,.0f} ₸".replace(",", " "),
        dimension="health",
        narrative=(
            f"ИИ принёс {accepted:,.0f} ₸ подтверждённой выручки, допродал ещё {upsell:,.0f} ₸, "
            f"но потери оцениваются в {lost:,.0f} ₸."
        ).replace(",", " "),
        severity=severity,
        action_items=[
            _action_item(
                action_id="open_owner_intel",
                label="Разборы владельца",
                action_type="navigate",
                drilldown={"tab": "ai_center", "aiCenterTab": "owner_intel"},
            ),
        ],
        metrics={
            "net_roi_kzt": round(net_roi, 2),
            "lost_revenue_kzt": round(lost, 2),
            "upsell_revenue_kzt": round(upsell, 2),
        },
        why=[str(x.get("label") or x.get("title") or x) for x in (owner_summary.get("top_losses") or [])[:2]],
        actions=["Открыть разборы владельца", "Посмотреть цепочку эффекта"],
        evidence={"source": "owner_intelligence", "period": owner_summary.get("period")},
        drilldown={
            "tab": "ai_center",
            "aiCenterTab": "owner_intel",
            "label": "Разборы владельца",
        },
        chat_prompt="Какой чистый эффект дал ИИ за сегодня?",
    )


def _margin_risk_card(owner_summary: dict[str, Any]) -> dict[str, Any] | None:
    preview = owner_summary.get("menu_profit_preview") or {}
    low_margin = preview.get("price_increase_candidates") or preview.get("promote_today") or []
    if not low_margin:
        missing = preview.get("missing_cost_checklist") or []
        if not missing:
            return None
        return _card(
            card_id="margin_data_gap",
            title="Себестоимость",
            headline="Не хватает данных по себестоимости",
            summary=f"Нужно заполнить cost price для {len(missing)} позиций, чтобы точнее считать маржу",
            dimension="quality",
            narrative=f"Без себестоимости по {len(missing)} позициям маржа считается неточно.",
            severity="warning",
            action_items=[
                _action_item(
                    action_id="open_menu_cost",
                    label="Импорт себестоимости",
                    action_type="navigate",
                    drilldown={"tab": "menu"},
                ),
            ],
            metrics={"missing_cost_count": len(missing)},
            why=["без себестоимости маржа и фудкост считаются неточно"],
            actions=["Открыть меню и импорт себестоимости"],
            evidence={"source": "menu_profit_lab"},
            drilldown={
                "tab": "menu",
                "label": "Меню и себестоимость",
            },
            chat_prompt="Какие блюда съедают маржу из-за отсутствия себестоимости?",
        )
    top = low_margin[0] if isinstance(low_margin[0], dict) else {"name": str(low_margin[0])}
    name = str(top.get("name") or top.get("dish_name") or "позиция")
    margin_pct = top.get("margin_pct")
    headline = f"Проверьте маржу: {name}"
    if margin_pct is not None:
        headline = f"{name}: маржа {float(margin_pct):.0f}%"
    return _card(
        card_id="margin_risk",
        title="Маржа меню",
        headline=headline,
        summary="Есть блюда с высокой выручкой и слабой маржой — их стоит пересмотреть",
        dimension="quality",
        narrative=f"{name} даёт выручку, но маржа под вопросом — проверьте цену и себестоимость.",
        severity="warning",
        action_items=[
            _action_item(
                action_id="open_menu_margin",
                label="Маржа меню",
                action_type="navigate",
                drilldown={"tab": "menu"},
            ),
            _action_item(
                action_id="stage_iiko_price",
                label="Подготовить изменение цены в iiko",
                action_type="agent_action",
                confirm_required=True,
                payload={
                    "action_type": "iiko_write_staged",
                    "title": f"Обновить цену: {name}",
                    "summary": "Staged-запрос на изменение цены в iiko после подтверждения.",
                    "payload": {"operation": "menu_price_update", "items": [{"name": name}]},
                },
            ),
        ],
        metrics={"candidate_count": len(low_margin)},
        why=[str((row or {}).get("name") or row) for row in low_margin[:3] if row],
        actions=["Открыть маржу меню", "Спросить ИИ про цену и маржу"],
        evidence={"source": "menu_profit_lab"},
        drilldown={
            "tab": "menu",
            "label": "Меню и маржа",
        },
        chat_prompt="Какие блюда дают выручку, но убивают маржу?",
    )


async def build_executive_hub_payload(
    db: AsyncSession,
    organization_id: int,
    *,
    period: str = "today",
    location_id: int | None = None,
    allowed_location_ids: set[int] | None = None,
    role: str = "owner",
    fast: bool = False,
) -> dict[str, Any]:
    owner_period = period if period in {"today", "7d", "30d"} else "today"
    summary = await _wait_or_fallback(
        db,
        "orders_summary",
        revenue_orders_summary(
            db,
            organization_id,
            period,
            location_id=location_id,
            allowed_location_ids=allowed_location_ids,
        ),
        _empty_sales_summary(period),
        timeout=0.8 if fast else _SUMMARY_TIMEOUT_SEC,
    )
    olap_summary = await _wait_or_fallback(
        db,
        "olap_summary",
        _olap_sales_summary(db, organization_id, period),
        None,
        timeout=0.6 if fast else _OLAP_TIMEOUT_SEC,
    )
    if olap_summary is not None:
        summary = olap_summary

    leak = await _wait_or_fallback(
        db,
        "revenue_leak",
        build_revenue_leak(
            db,
            organization_id,
            location_id=location_id,
            allowed_location_ids=allowed_location_ids,
        ),
        _empty_leak(),
        timeout=0.6 if fast else _LEAK_TIMEOUT_SEC,
    )
    insights = await _wait_or_fallback(
        db,
        "insights",
        list_insights(db, organization_id, limit=5),
        [],
        timeout=0.4 if fast else _INSIGHTS_TIMEOUT_SEC,
    )
    insight_snapshots = [_snapshot_insight(insight) for insight in insights[:2]]
    if fast:
        owner_summary = _empty_owner_summary(owner_period)
    else:
        owner_summary = await _wait_or_fallback(
            db,
            "owner_summary",
            build_owner_intelligence_summary(
                db,
                organization_id,
                location_id=location_id,
                period=owner_period,
                allowed_location_ids=allowed_location_ids,
            ),
            _empty_owner_summary(owner_period),
            timeout=_OWNER_TIMEOUT_SEC,
        )

    cards: list[dict[str, Any]] = [
        _revenue_pulse_card(summary),
        _money_risk_card(leak),
        _owner_roi_card(owner_summary),
    ]
    margin_card = _margin_risk_card(owner_summary)
    if margin_card is not None:
        cards.append(margin_card)
    for insight in insight_snapshots:
        cards.append(_insight_card(insight))

    ops_card = _ops_status_card(summary, leak, owner_summary)
    if ops_card is not None:
        cards.insert(2, ops_card)
    for card in cards:
        card["focused_view"] = _focused_view_for_card(card, summary, leak, owner_summary)
    business_summary = _business_summary(summary, leak, owner_summary)
    today_picture = _today_picture(summary, leak, owner_summary)
    money_drivers = _money_drivers(summary, owner_summary)
    money_at_risk = _money_at_risk_breakdown(leak, owner_summary)
    network_branch = _network_branch_brief(owner_summary)

    if network_branch.get("enabled"):
        network_card = _card(
            card_id="network_branch_intelligence",
            title="Филиалы сети",
            headline=str(network_branch.get("headline") or "Сравнить филиалы"),
            summary=str(network_branch.get("summary") or ""),
            dimension="money",
            severity=str(network_branch.get("severity") or "info"),
            why=[str(row.get("label") or row.get("name") or row) for row in (network_branch.get("drivers") or [])[:3]],
            action_items=network_branch.get("actions") or [],
            drilldown={"tab": "ai_center", "aiCenterTab": "network_benchmark", "label": "Сравнение филиалов"},
            chat_prompt=str(network_branch.get("chat_prompt") or ""),
        )
        network_card["focused_view"] = {
            "title": "Разбор филиалов сети",
            "summary": str(network_branch.get("summary") or ""),
            "kpis": [
                {"label": "Лучший филиал", "value": str((network_branch.get("best_location") or {}).get("organization_name") or (network_branch.get("best_location") or {}).get("name") or "нет данных"), "hint": "по бенчмарку"},
                {"label": "Зона внимания", "value": str((network_branch.get("worst_location") or {}).get("organization_name") or (network_branch.get("worst_location") or {}).get("name") or "нет данных"), "hint": "где просадка"},
                {"label": "Филиалов в сравнении", "value": str(len(network_branch.get("rows") or [])), "hint": "в превью"},
            ],
            "drivers": network_branch.get("drivers") or [],
            "table_title": "Филиалы",
            "table": network_branch.get("rows") or [],
            "actions": network_branch.get("actions") or [],
            "chat_prompt": str(network_branch.get("chat_prompt") or ""),
        }
        cards.append(network_card)

    dimensions = _build_dimension_widgets(cards)
    next_actions = _next_actions_from_cards(cards, business_summary)
    owner_cards = _owner_cards(summary, leak, owner_summary, cards)
    readiness_blocks = _owner_readiness_blocks(summary, owner_summary, cards)

    return {
        "version": 4,
        "mode": "fast" if fast else "full",
        "summary": business_summary,
        "today_picture": today_picture,
        "owner_cards": owner_cards,
        "money_drivers": money_drivers,
        "money_at_risk": money_at_risk,
        "network_branch": network_branch,
        "priority_signals": next_actions,
        "agent_context": {
            "title": "Разбор выбранного сигнала",
            "selected_prompt": today_picture.get("chat_prompt"),
            "empty_state": "Выберите карточку, риск или действие — агент сразу разберёт причины и предложит следующий шаг.",
        },
        "owner_readiness": readiness_blocks,
        "next_actions": next_actions,
        "readiness": _readiness_state(summary, owner_summary, cards),
        "cards": cards[:6],
        "dimensions": dimensions,
        "chat": {
            "endpoint": "/api/admin/intelligence/query",
            "agent_actions_endpoint": "/api/admin/intelligence/agent-actions",
            "role": role,
            "business_questions": questions_for_role(role),
        },
        "period": period,
    }


def _score_from_severity(severity: str) -> int:
    s = (severity or "info").lower()
    if s == "critical":
        return 35
    if s == "warning":
        return 62
    return 88


def _build_dimension_widgets(cards: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_dim: dict[str, list[dict[str, Any]]] = {"health": [], "money": [], "quality": [], "ops": []}
    for card in cards:
        dim = str(card.get("dimension") or "ops")
        if dim in by_dim:
            by_dim[dim].append(card)
    out: dict[str, dict[str, Any]] = {}
    for dim, rows in by_dim.items():
        if not rows:
            out[dim] = {
                "score": 90,
                "severity": "info",
                "narrative": "Пока без сигналов — держим штатный режим.",
                "card_ids": [],
            }
            continue
        worst = sorted(rows, key=lambda c: _score_from_severity(str(c.get("severity") or "info")))[0]
        out[dim] = {
            "score": _score_from_severity(str(worst.get("severity") or "info")),
            "severity": worst.get("severity") or "info",
            "narrative": worst.get("narrative") or worst.get("headline") or "",
            "card_ids": [c.get("id") for c in rows if c.get("id")],
        }
    return out


def _ops_status_card(
    summary: dict[str, Any],
    leak: dict[str, Any],
    owner_summary: dict[str, Any],
) -> dict[str, Any] | None:
    changes = summary.get("changes") or {}
    cancel_pp = changes.get("cancel_rate_pp")
    total_leak = float(leak.get("total_leak_kzt") or 0)
    lost = float(owner_summary.get("lost_revenue") or 0)
    severity = "info"
    if isinstance(cancel_pp, (int, float)) and cancel_pp > 3:
        severity = "warning"
    if total_leak >= 30_000 or lost >= 30_000:
        severity = "critical"
    narrative = "Операционный режим стабильный."
    if severity == "warning":
        narrative = "Растёт доля отмен — проверьте кухню и стоп-лист."
    if severity == "critical":
        narrative = "Высокие потери или отмены — нужна экстренная пауза или разбор очереди."
    return _card(
        card_id="ops_status",
        title="Операции",
        headline="Смена под контролем" if severity == "info" else "Нужно вмешательство на смене",
        summary=narrative,
        dimension="ops",
        narrative=narrative,
        severity=severity,
        metrics={
            "cancel_rate_pp": cancel_pp,
            "total_leak_kzt": round(total_leak, 2),
            "lost_revenue_kzt": round(lost, 2),
        },
        action_items=[
            _action_item(
                action_id="force_close_60",
                label="Закрыть ресторан на 60 мин",
                action_type="agent_action",
                confirm_required=True,
                payload={
                    "action_type": "force_close",
                    "title": "Экстренное закрытие на 60 мин",
                    "summary": "Пауза приёма заказов до подтверждения владельцем.",
                    "payload": {"minutes": 60, "reason": "Executive Hub: операционный риск"},
                },
            ),
            _action_item(
                action_id="open_inbox",
                label="Очередь клиентов",
                action_type="navigate",
                drilldown={"tab": "inbox"},
            ),
        ],
        drilldown={"tab": "dashboard", "label": "Дашборд смены"},
        chat_prompt="Что сейчас мешает смене работать стабильно?",
    )
