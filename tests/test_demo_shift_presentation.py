"""Demo explore presentation caps (G10.8.1)."""

from app.services.demo_shift_presentation import (
    DEMO_EXPLORE_MAX_RISK_KZT,
    soften_demo_explore_shift_state,
)


def test_soften_demo_explore_caps_risk_and_state() -> None:
    raw = {
        "state": "S5",
        "metrics": {
            "risk_kzt": 92_000.0,
            "active_risk_kzt": 88_000.0,
            "at_risk_count": 9,
            "queue_size": 12,
            "queue_size_active": 11,
        },
        "focus": {
            "amount_kzt": 29_900.0,
            "pulse": "red",
        },
        "predictive_scene": {"tension_level": "critical"},
    }
    out = soften_demo_explore_shift_state(raw)
    assert out["state"] == "S2"
    assert out["metrics"]["risk_kzt"] == DEMO_EXPLORE_MAX_RISK_KZT
    assert out["metrics"]["at_risk_count"] <= 3
    assert out["focus"]["pulse"] == "amber"
    assert float(out["focus"]["amount_kzt"]) <= 5_000
    assert out["predictive_scene"]["tension_level"] == "elevated"
