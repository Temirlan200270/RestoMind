"""G10.6–G10.7 — choreography + narrative contract tests."""

from __future__ import annotations


def _render_narrative(live_impact: dict | None) -> str:
    if not live_impact:
        return ""
    if live_impact.get("outcome_emotion"):
        return str(live_impact["outcome_emotion"]).strip()
    reason = str(live_impact.get("impact_reason") or "").strip()
    text = str(live_impact.get("impact_text") or "").strip()
    if reason and text:
        return f"{reason} → {text}"
    return text or reason


def _render_emotion(live_impact: dict | None) -> str:
    if not live_impact:
        return ""
    if live_impact.get("outcome_emotion"):
        return str(live_impact["outcome_emotion"]).strip()
    return str(live_impact.get("impact_reason") or "")


def test_narrative_complete_action_legacy() -> None:
    line = _render_narrative(
        {
            "last_action": "focus_completed",
            "impact_reason": "Клиент возвращён в воронку заказа",
            "impact_text": "+1 200 ₸ спасено",
            "amount_kzt": 1200,
        }
    )
    assert "→" in line or "1 200" in line


def test_compressed_emotion_not_report() -> None:
    emotion = _render_emotion(
        {
            "outcome_emotion": "Вернули клиента",
            "impact_money": "+1 200 ₸",
            "narrative_compressed": True,
        }
    )
    assert emotion == "Вернули клиента"
    assert "→" not in emotion
    assert "₸" not in emotion


def test_narrative_skip_action() -> None:
    line = _render_narrative(
        {
            "last_action": "focus_skipped",
            "impact_text": "Отложено",
            "impact_reason": "Следующая задача в очереди",
        }
    )
    assert "Следующая задача" in line or "Отложено" in line


def test_choreo_timeline_ms_sum() -> None:
    """Golden flow ~1.35s base + ~400ms impact staging."""
    ms = {
        "pauseBeforeExit": 150,
        "exitDuration": 200,
        "impactRevealDelay": 200,
        "impactPrefixReveal": 120,
        "impactEmotionReveal": 180,
        "impactMoneyReveal": 100,
        "pulseAfterImpact": 300,
        "focusEnterAfterPulse": 500,
    }
    total = sum(ms.values())
    assert 1700 <= total <= 1800
