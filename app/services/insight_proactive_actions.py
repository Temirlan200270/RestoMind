"""Map OperationalInsight → agent action proposals for proactive delivery."""

from __future__ import annotations

from typing import Any

from app.db.models import BusinessRecommendation, OperationalInsight


def _dish_price_candidates(insight: OperationalInsight) -> list[dict[str, Any]]:
    evidence = insight.evidence_json or (insight.payload_json or {}).get("evidence") or {}
    items: list[dict[str, Any]] = []
    for block in evidence.get("layers") or []:
        if not isinstance(block, dict) or block.get("level") != "dish":
            continue
        for dish in block.get("items") or []:
            if not isinstance(dish, dict):
                continue
            name = str(dish.get("name") or dish.get("dish") or "").strip()
            delta = float(dish.get("revenue_delta") or 0)
            if name and delta < 0:
                items.append({"label": name, "revenue_delta": delta})
    return items[:5]


def build_proactive_action_from_insight(insight: OperationalInsight) -> dict[str, Any] | None:
    """Return action spec for propose_agent_action, or None if no automated action."""
    itype = (insight.insight_type or "").strip()
    severity = (insight.severity or "").strip().lower()
    if severity not in {"critical", "warning"}:
        return None

    if itype in {"revenue_drop", "sales_revenue_drop", "orders_drop"}:
        dishes = _dish_price_candidates(insight)
        if dishes:
            return {
                "action_type": "iiko_write_staged",
                "title": "Скорректировать цены в iiko",
                "summary": insight.summary or insight.title,
                "payload": {
                    "operation": "menu_price_update",
                    "items": [{"label": d["label"]} for d in dishes],
                    "source_insight_type": itype,
                },
            }
        return {
            "action_type": "upsell_rule_create",
            "title": "Допродажа для восстановления выручки",
            "summary": insight.summary or insight.title,
            "payload": {
                "trigger_category": "Основные блюда",
                "suggest_category": "Напитки",
                "trigger_mode": "missing_category",
            },
        }

    if itype in {"cancellations_up", "cancellation_surge", "ai_message_drop"}:
        return {
            "action_type": "force_close",
            "title": "Пауза приёма заказов на 30 мин",
            "summary": insight.summary or insight.title,
            "payload": {
                "minutes": 30,
                "reason": f"Proactive: {insight.title}",
            },
        }

    return None


def build_proactive_action_from_recommendation(rec: BusinessRecommendation) -> dict[str, Any] | None:
    """Return action spec for actionable BusinessRecommendation rows in digests."""
    rec_type = (rec.recommendation_type or "").strip().lower()
    if rec_type != "upsell_pair":
        return None
    return {
        "action_type": "upsell_rule_create",
        "title": rec.title or "Правило допродаж",
        "summary": (rec.body or rec.title or "").strip(),
        "payload": {
            "trigger_category": "Основные блюда",
            "suggest_category": "Напитки",
            "trigger_mode": "missing_category",
        },
    }
