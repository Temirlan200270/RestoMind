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


def _money(value: float | int | None) -> str:
    return f"{float(value or 0):,.0f} ₸".replace(",", " ")


def _metric(label: str, value: str, *, hint: str = "", severity: str = "info") -> dict[str, str]:
    return {"label": label, "value": value, "hint": hint, "severity": severity}


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


def _insight_card(insight: OperationalInsight) -> dict[str, Any]:
    payload = insight.payload_json or {}
    hypotheses = payload.get("cause_hypotheses") or []
    actions = payload.get("recommended_actions") or []
    return _card(
        card_id=f"insight_{insight.id}",
        title="Главный инсайт",
        headline=insight.title,
        summary=insight.summary,
        dimension="quality",
        narrative=insight.summary,
        severity=str(insight.severity or "info"),
        action_items=[
            _action_item(
                action_id=f"insight_open_{insight.id}",
                label="Открыть инсайт",
                action_type="navigate",
                drilldown={"tab": "ai_center", "aiCenterTab": "insights", "insight_id": insight.id},
            ),
        ],
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
        dimension="health",
        narrative=(
            f"ИИ принёс {accepted:,.0f} ₸ подтверждённой выручки, допродал ещё {upsell:,.0f} ₸, "
            f"но потери оцениваются в {lost:,.0f} ₸."
        ).replace(",", " "),
        severity=severity,
        action_items=[
            _action_item(
                action_id="open_owner_intel",
                label="Owner Intelligence",
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
        dimension="quality",
        narrative=f"{name} даёт выручку, но маржа под вопросом — проверьте цену и себестоимость.",
        severity="warning",
        action_items=[
            _action_item(
                action_id="open_menu_margin",
                label="Menu Profit Lab",
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

    ops_card = _ops_status_card(summary, leak, owner_summary)
    if ops_card is not None:
        cards.insert(2, ops_card)
    dimensions = _build_dimension_widgets(cards)
    business_summary = _business_summary(summary, leak, owner_summary)
    next_actions = _next_actions_from_cards(cards, business_summary)

    return {
        "version": 3,
        "summary": business_summary,
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
