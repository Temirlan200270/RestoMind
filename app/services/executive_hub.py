"""Executive Hub — narrative cards over existing intelligence/analytics layers."""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import OperationalInsight
from app.services.copilot.business_questions import questions_for_role
from app.services.intelligence import list_insights, revenue_orders_summary
from app.services.owner_intelligence import build_owner_intelligence_summary
from app.services.revenue_leak import build_revenue_leak


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


def _card(
    *,
    card_id: str,
    title: str,
    headline: str,
    summary: str,
    severity: str = "info",
    metrics: dict[str, Any] | None = None,
    why: list[str] | None = None,
    actions: list[str] | None = None,
    evidence: dict[str, Any] | None = None,
    drilldown: dict[str, Any] | None = None,
    chat_prompt: str | None = None,
) -> dict[str, Any]:
    return {
        "id": card_id,
        "title": title,
        "headline": headline,
        "summary": summary,
        "severity": severity,
        "metrics": metrics or {},
        "why": why or [],
        "actions": actions or [],
        "evidence": evidence or {},
        "drilldown": drilldown or {},
        "chat_prompt": chat_prompt or headline,
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
    return _card(
        card_id="revenue_pulse",
        title="Выручка",
        headline=f"Сегодня {revenue:,.0f} ₸ — {_format_trend(revenue_pct)}".replace(",", " "),
        summary=f"{orders} заказов, средний чек {avg_check:,.0f} ₸".replace(",", " "),
        severity=_severity_from_delta(revenue_pct if isinstance(revenue_pct, (int, float)) else None),
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
    return _card(
        card_id="money_at_risk",
        title="Деньги на кону",
        headline=headline,
        summary=f"Главный источник: {top_label} ({top_amount:,.0f} ₸)".replace(",", " ") if top_amount else "Сейчас критичных утечек нет",
        severity=severity,
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


def _insight_card(insight: OperationalInsight) -> dict[str, Any]:
    payload = insight.payload_json or {}
    hypotheses = payload.get("cause_hypotheses") or []
    actions = payload.get("recommended_actions") or []
    return _card(
        card_id=f"insight_{insight.id}",
        title="Главный инсайт",
        headline=insight.title,
        summary=insight.summary,
        severity=str(insight.severity or "info"),
        metrics={
            "insight_id": insight.id,
            "confidence_score": insight.confidence_score,
        },
        why=[str(x) for x in hypotheses[:3]],
        actions=[str(x) for x in actions[:3]],
        evidence=insight.evidence_json or payload.get("evidence") or {},
        drilldown={
            "tab": "ai_center",
            "aiCenterTab": "insights",
            "insight_id": insight.id,
            "label": "Все инсайты",
        },
        chat_prompt=f"Объясни подробнее: {insight.title}",
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
        severity=severity,
        metrics={
            "net_roi_kzt": round(net_roi, 2),
            "lost_revenue_kzt": round(lost, 2),
            "upsell_revenue_kzt": round(upsell, 2),
        },
        why=[str(x.get("label") or x.get("title") or x) for x in (owner_summary.get("top_losses") or [])[:2]],
        actions=["Открыть Owner Intelligence", "Посмотреть ROI-цепочку"],
        evidence={"source": "owner_intelligence", "period": owner_summary.get("period")},
        drilldown={
            "tab": "ai_center",
            "aiCenterTab": "owner_intel",
            "label": "Owner Intelligence",
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
            severity="warning",
            metrics={"missing_cost_count": len(missing)},
            why=["без себестоимости Menu Profit Lab занижает риск по марже"],
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
        severity="warning",
        metrics={"candidate_count": len(low_margin)},
        why=[str((row or {}).get("name") or row) for row in low_margin[:3] if row],
        actions=["Открыть Menu Profit Lab", "Спросить ИИ про цену и маржу"],
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
) -> dict[str, Any]:
    owner_period = period if period in {"today", "7d", "30d"} else "today"
    summary, leak, insights, owner_summary = await asyncio.gather(
        revenue_orders_summary(
            db,
            organization_id,
            period,
            location_id=location_id,
            allowed_location_ids=allowed_location_ids,
        ),
        build_revenue_leak(
            db,
            organization_id,
            location_id=location_id,
            allowed_location_ids=allowed_location_ids,
        ),
        list_insights(db, organization_id, limit=5),
        build_owner_intelligence_summary(
            db,
            organization_id,
            location_id=location_id,
            period=owner_period,
            allowed_location_ids=allowed_location_ids,
        ),
    )

    cards: list[dict[str, Any]] = [
        _revenue_pulse_card(summary),
        _money_risk_card(leak),
        _owner_roi_card(owner_summary),
    ]
    margin_card = _margin_risk_card(owner_summary)
    if margin_card is not None:
        cards.append(margin_card)
    for insight in insights[:2]:
        cards.append(_insight_card(insight))

    return {
        "cards": cards[:6],
        "chat": {
            "endpoint": "/api/admin/intelligence/query",
            "role": role,
            "business_questions": questions_for_role(role),
        },
        "period": period,
    }
