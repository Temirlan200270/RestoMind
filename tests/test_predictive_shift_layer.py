"""G10.7 — Predictive Shift Layer tests."""

from __future__ import annotations

from app.services.shift_state_engine import (
    build_live_impact_payload,
    derive_focus_anticipation,
)


def test_anticipation_imminent_slow_chat() -> None:
    ant = derive_focus_anticipation(
        {"kind": "slow_chat", "wait_minutes": 16, "pulse": "red", "amount_kzt": 3000},
    )
    assert ant["tension_level"] == "imminent"
    assert ant["pre_attention"] is True
    assert "почти" in ant["anticipation_text"].lower() or "уш" in ant["anticipation_text"].lower()
    assert ant["predictive_prefix"]


def test_live_impact_compressed_complete() -> None:
    payload = build_live_impact_payload(
        last_action="focus_completed",
        kind="slow_chat",
        amount_kzt=1200,
        wait_minutes=10,
        pulse="red",
    )
    assert payload["narrative_compressed"] is True
    assert payload["outcome_emotion"] == "Вернули клиента"
    assert payload["outcome_prefix"] == "Клиент уже почти ушёл…"
    assert payload["impact_money"] == "+1 200 ₸"
    assert payload["animation"] == "pulse_green"


def test_live_impact_skip_compressed() -> None:
    payload = build_live_impact_payload(
        last_action="focus_skipped",
        kind="abandoned_draft",
        wait_minutes=12,
    )
    assert payload["outcome_emotion"] == "Отложили — следующая задача"
    assert payload["animation"] == "fade_shrink"
