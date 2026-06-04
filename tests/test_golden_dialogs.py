"""Golden Dialog Evals — эталонные сценарии без вызова LLM."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.schemas.ai_schemas import AIBrainResponse
from app.services.decision_engine import DecisionEngine
from app.services.fulfillment_infer import enrich_ai_fulfillment_from_message
from app.services.upsell_safety_gate import (
    UpsellSafetyContext,
    is_order_start_without_items,
    is_recommendation_request,
    should_suppress_upsell,
    strip_upsell_from_ai_response,
)

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "golden_dialogs"


def _load_cases() -> list[dict]:
    path = _FIXTURES / "scenarios.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    return raw if isinstance(raw, list) else []


@pytest.mark.parametrize("case", _load_cases(), ids=lambda c: c.get("id", "?"))
def test_golden_dialog_expectations(case: dict) -> None:
    """Проверяет детерминированные guard-слои на эталонных репликах."""
    user_msg = str(case.get("user_message") or "")
    ai_raw = case.get("ai_response") or {}
    ai = AIBrainResponse.model_validate(ai_raw)

    if case.get("fulfillment_infer"):
        ai = enrich_ai_fulfillment_from_message(
            ai, user_msg, has_draft=bool(case.get("has_draft")),
        )
        exp = case["fulfillment_infer"]
        if "order_type" in exp:
            assert ai.order_type == exp["order_type"]
        if "pickup_time_note_contains" in exp:
            assert exp["pickup_time_note_contains"] in (ai.pickup_time_note or "")

    if case.get("order_start"):
        assert is_order_start_without_items(user_msg) is bool(case["order_start"])

    if case.get("recommendation_request"):
        assert is_recommendation_request(user_msg) is bool(case["recommendation_request"])

    if case.get("upsell_suppressed"):
        ctx = UpsellSafetyContext(
            user_message=user_msg,
            order_meta=case.get("order_meta") or {},
            intent=ai.intent,
        )
        assert should_suppress_upsell(ctx) is True
        stripped = strip_upsell_from_ai_response(ai)
        assert stripped.is_recommendation is False

    if case.get("decision_engine_block"):
        de = DecisionEngine()

        class _Ctx:
            pass

        ctx = _Ctx()
        ctx.draft_row = None
        if case.get("has_draft"):
            class _Draft:
                items_json = {"items": [{"name": "Плов", "quantity": 1}]}

            ctx.draft_row = _Draft()

        result = de._check_empty_order(ai, ctx, user_message=user_msg)  # noqa: SLF001
        if case.get("expect_no_empty_order_block"):
            assert result is None
            return
        assert result is not None
        if case.get("expect_friendly_start"):
            assert "С радостью помогу" in (result.detail or "")
