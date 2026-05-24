"""Soft presentation caps for read-only demo explore (after pitch)."""

from __future__ import annotations

from typing import Any

DEMO_EXPLORE_MAX_RISK_KZT = 12_000.0
DEMO_EXPLORE_MAX_AT_RISK = 3


def soften_demo_explore_shift_state(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Демо-осмотр: не показываем 90k+ «всё горит» — умеренный риск для walkthrough.
    Pitch-сцена использует canned state и не проходит через этот слой.
    """
    out = dict(payload)
    metrics = dict(out.get("metrics") or {})
    risk = float(metrics.get("risk_kzt") or 0)
    if risk > DEMO_EXPLORE_MAX_RISK_KZT:
        metrics["risk_kzt"] = DEMO_EXPLORE_MAX_RISK_KZT
        active = float(metrics.get("active_risk_kzt") or 0)
        metrics["active_risk_kzt"] = min(active, DEMO_EXPLORE_MAX_RISK_KZT)
    metrics["at_risk_count"] = min(int(metrics.get("at_risk_count") or 0), DEMO_EXPLORE_MAX_AT_RISK)
    metrics["queue_size"] = min(int(metrics.get("queue_size") or 0), DEMO_EXPLORE_MAX_AT_RISK + 1)
    metrics["queue_size_active"] = min(int(metrics.get("queue_size_active") or 0), DEMO_EXPLORE_MAX_AT_RISK + 1)
    out["metrics"] = metrics

    state = str(out.get("state") or "")
    if state in ("S1", "S5"):
        out["state"] = "S2"

    focus = out.get("focus")
    if isinstance(focus, dict):
        focus = dict(focus)
        amount = float(focus.get("amount_kzt") or focus.get("risk_kzt") or 0)
        if amount > 5_000:
            focus["amount_kzt"] = 5_000.0
        if str(focus.get("pulse") or "") == "red":
            focus["pulse"] = "amber"
        out["focus"] = focus

    pred = out.get("predictive_scene")
    if isinstance(pred, dict):
        pred = dict(pred)
        if str(pred.get("tension_level") or "") in ("critical", "imminent"):
            pred["tension_level"] = "elevated"
        out["predictive_scene"] = pred

    return out
