"""Role-based business question catalog for Copilot."""

from __future__ import annotations

from typing import Any

BUSINESS_QUESTIONS: list[dict[str, Any]] = [
    {
        "role": "owner",
        "persona": "owner",
        "use_case": "daily_roi_control",
        "question": "Сколько заработали вчера и где отклонение от нормы?",
        "tools": ["get_revenue_summary", "compare_periods", "get_anomalies", "get_data_quality_status"],
    },
    {
        "role": "owner",
        "persona": "owner",
        "use_case": "profitability",
        "question": "Какие блюда дают выручку, но просаживают маржу?",
        "tools": ["get_low_margin_high_revenue_dishes", "get_food_cost_margin", "get_top_dishes"],
    },
    {
        "role": "manager",
        "persona": "manager",
        "use_case": "shift_control",
        "question": "В какие часы сегодня слабые продажи и что проверить?",
        "tools": ["get_hourly_heatmap", "get_anomalies", "get_stock_alerts"],
    },
    {
        "role": "manager",
        "persona": "manager",
        "use_case": "staff_kpi",
        "question": "Кто из официантов сегодня/за неделю лучший по выручке?",
        "tools": ["get_waiter_kpi", "get_revenue_summary"],
    },
    {
        "role": "network",
        "persona": "network",
        "use_case": "standardization",
        "question": "Какие категории ведут себя нестабильно и почему?",
        "tools": ["get_category_breakdown", "compare_periods", "get_anomalies", "find_related_memory_events"],
    },
    {
        "role": "franchise",
        "persona": "franchise",
        "use_case": "supplier_risk",
        "question": "Есть ли риск по поставщикам и какие блюда зависят от них?",
        "tools": ["get_supplier_exposure", "get_stock_alerts", "get_low_margin_high_revenue_dishes"],
    },
]


def questions_for_role(role: str | None) -> list[dict[str, Any]]:
    target = (role or "owner").strip().lower()
    rows = [row for row in BUSINESS_QUESTIONS if row["role"] == target]
    return rows or [row for row in BUSINESS_QUESTIONS if row["role"] == "owner"]


def tools_for_role_question(role: str | None, question: str) -> list[str]:
    q = (question or "").lower()
    scored: list[tuple[int, dict[str, Any]]] = []
    for row in questions_for_role(role):
        score = sum(1 for token in row["question"].lower().split() if len(token) >= 4 and token in q)
        scored.append((score, row))
    scored.sort(key=lambda x: x[0], reverse=True)
    if scored and scored[0][0] > 0:
        return list(scored[0][1]["tools"])
    return []
