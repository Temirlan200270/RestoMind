"""Control Plane Phase 3 — minimal replay harness (golden scenarios + scorecard)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

GOLDEN_SCENARIOS: list[dict[str, Any]] = [
    {
        "id": "order_confirm_yes",
        "title": "Подтверждение заказа «Да»",
        "user_text": "Да, оформляйте",
        "expected_intent": "order",
        "tags": ["ordering", "confirm"],
    },
    {
        "id": "booking_request",
        "title": "Бронь на вечер",
        "user_text": "Хочу забронировать стол на 19:00 на 4 человека",
        "expected_intent": "book",
        "tags": ["booking"],
    },
    {
        "id": "faq_hours",
        "title": "FAQ — часы работы",
        "user_text": "До скольки вы работаете?",
        "expected_intent": "faq",
        "tags": ["faq"],
    },
]


@dataclass(frozen=True)
class ReplayScorecard:
    scenario_id: str
    expected_intent: str
    actual_intent: str
    intent_match: bool
    has_items: bool
    notes: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "expected_intent": self.expected_intent,
            "actual_intent": self.actual_intent,
            "intent_match": self.intent_match,
            "has_items": self.has_items,
            "notes": self.notes,
        }


def list_golden_scenarios() -> list[dict[str, Any]]:
    return list(GOLDEN_SCENARIOS)


def get_golden_scenario(scenario_id: str) -> dict[str, Any] | None:
    sid = str(scenario_id or "").strip()
    for row in GOLDEN_SCENARIOS:
        if row.get("id") == sid:
            return dict(row)
    return None


def score_replay_response(
    *,
    scenario_id: str,
    expected_intent: str,
    ai_response: dict[str, Any] | None,
) -> ReplayScorecard:
    """Score a replay / test-bot response against golden expectations."""
    actual_intent = str((ai_response or {}).get("intent") or "").strip().lower()
    expected = str(expected_intent or "").strip().lower()
    items = (ai_response or {}).get("items") or []
    has_items = isinstance(items, list) and len(items) > 0
    match = actual_intent == expected
    notes = "ok" if match else f"expected {expected}, got {actual_intent or 'empty'}"
    return ReplayScorecard(
        scenario_id=scenario_id,
        expected_intent=expected,
        actual_intent=actual_intent,
        intent_match=match,
        has_items=has_items,
        notes=notes,
    )
