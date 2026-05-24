"""Websocket org-scoped publish + replay harness smoke."""

from __future__ import annotations

from app.services.replay_harness import get_golden_scenario, list_golden_scenarios, score_replay_response


def test_publish_org_event_module_exports() -> None:
    from app.services.events import publish_org_event, _org_channel

    assert _org_channel(42) == "admin_events:org:42"
    assert callable(publish_org_event)


def test_replay_harness_golden_scenarios() -> None:
    rows = list_golden_scenarios()
    assert len(rows) >= 3
    one = get_golden_scenario("order_confirm_yes")
    assert one is not None
    score = score_replay_response(
        scenario_id="order_confirm_yes",
        expected_intent="order",
        ai_response={"intent": "order", "items": [{"name": "test"}]},
    )
    assert score.intent_match is True
    assert score.has_items is True
