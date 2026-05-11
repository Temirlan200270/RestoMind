"""E11 Strategy Engine — явные правила."""

from app.services.sales_strategy_engine import (
    apply_engine_rules_first,
    rule_close_after_recommendation_trace_cap,
)


def test_rule_trace_cap_returns_close_order() -> None:
    meta = {
        "recommendation_trace": [
            {"offered": "Чай"},
            {"offered": "Самса"},
        ],
    }
    d = rule_close_after_recommendation_trace_cap(meta)
    assert d is not None
    assert d.goal == "close_order"


def test_apply_engine_rules_first_empty_trace() -> None:
    assert apply_engine_rules_first({}) is None


def test_apply_engine_rules_first_respects_trace_cap() -> None:
    meta = {"recommendation_trace": [{}, {}]}
    d = apply_engine_rules_first(meta)
    assert d is not None
    assert d.goal == "close_order"
