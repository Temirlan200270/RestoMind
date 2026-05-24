"""
G10.8 — scripted 30s «money rescue» demo scene for sales/onboarding.

Loss → tension → auto-action → live impact → next risk.
Fixed narrative strings only (no LLM). GET-only — safe for demo sessions (POST blocked).
"""

from __future__ import annotations

from typing import Any

from app.services.shift_state_engine import (
    _build_compressed_actions,
    _build_predictive_scene,
    _build_presentation,
    _focus_payload,
    _split_queue_items,
    build_live_impact_payload,
)

DEMO_SCENE_MONEY_RESCUE_30S = "money_rescue_30s"

DEMO_RESCUE_AMOUNT_KZT = 1200.0
_DEMO_FOCUS_ID = "demo-scene-chat-001"
_DEMO_NEXT_FOCUS_ID = "demo-scene-chat-002"

_PHASE_ORDER = ("hook", "tension", "action", "impact", "next")

SCENE_PHASES: dict[str, list[dict[str, Any]]] = {
    DEMO_SCENE_MONEY_RESCUE_30S: [
        {"id": "hook", "delay_ms": 0, "label": "Боль"},
        {"id": "tension", "delay_ms": 5000, "label": "Напряжение"},
        {"id": "action", "delay_ms": 10000, "label": "Действие", "auto_complete": True},
        {"id": "impact", "delay_ms": 15000, "label": "Спасение"},
        {"id": "next", "delay_ms": 25000, "label": "Следующий риск"},
    ],
}

DEMO_SCENES: dict[str, dict[str, Any]] = {
    DEMO_SCENE_MONEY_RESCUE_30S: {
        "title": "Спасение денег за 30 секунд",
        "tagline": "Потеря → вмешательство → возврат денег",
        "total_ms": 30000,
        "phases": SCENE_PHASES[DEMO_SCENE_MONEY_RESCUE_30S],
        "primary_kind": "slow_chat",
    },
}


def list_demo_shift_scenes() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for scene_id, meta in DEMO_SCENES.items():
        out.append(
            {
                "id": scene_id,
                "title": meta["title"],
                "tagline": meta["tagline"],
                "total_ms": meta["total_ms"],
                "phases": list(meta["phases"]),
            }
        )
    return out


def _demo_chat_raw(*, wait_minutes: int, pulse: str, focus_id: str, title: str, subtitle: str) -> dict[str, Any]:
    return {
        "id": focus_id,
        "kind": "slow_chat",
        "title": title,
        "subtitle": subtitle,
        "amount_kzt": DEMO_RESCUE_AMOUNT_KZT,
        "wait_minutes": wait_minutes,
        "pulse": pulse,
        "phone": "+77001234567",
        "priority_score": 92.0,
        "actions": [
            {
                "label": "Ответить",
                "type": "navigate",
                "tab": "chats",
                "phone": "+77001234567",
            },
        ],
    }


def _hook_item() -> dict[str, Any]:
    return _demo_chat_raw(
        focus_id=_DEMO_FOCUS_ID,
        wait_minutes=3,
        pulse="amber",
        title="Клиент ждёт ответа",
        subtitle="WhatsApp · уже 3 минуты без ответа",
    )


def _tension_item() -> dict[str, Any]:
    return _demo_chat_raw(
        focus_id=_DEMO_FOCUS_ID,
        wait_minutes=5,
        pulse="red",
        title="Клиент ждёт ответа",
        subtitle="WhatsApp · риск ухода растёт",
    )


def _next_item() -> dict[str, Any]:
    return _demo_chat_raw(
        focus_id=_DEMO_NEXT_FOCUS_ID,
        wait_minutes=2,
        pulse="amber",
        title="Следующий риск: 2 клиента ждут ответа",
        subtitle="Очередь не останавливается",
    )


def _base_metrics(*, risk_kzt: float, recovered_today: float, at_risk: int, queue_size: int) -> dict[str, Any]:
    return {
        "risk_kzt": risk_kzt,
        "active_risk_kzt": risk_kzt,
        "saved_today_kzt": recovered_today,
        "confirmed_revenue_today_kzt": recovered_today,
        "recovered_today_kzt": recovered_today,
        "focus_completed_today": 1 if recovered_today > 0 else 0,
        "at_risk_count": at_risk,
        "queue_size": queue_size,
        "queue_size_active": queue_size,
        "shift_empty_focus_while_risk_positive": 0,
        "excluded_skip": 0,
        "excluded_next": 0,
        "excluded_done": 0,
    }


def _wrap_shift_payload(
    *,
    org_id: int,
    scene_id: str,
    phase: str,
    state: str,
    state_reason: str,
    focus_raw: dict[str, Any] | None,
    queue_raw: list[dict[str, Any]],
    metrics: dict[str, Any],
    live_impact: dict[str, Any] | None,
    priority: float,
    auto_complete: bool = False,
) -> dict[str, Any]:
    has_focus = focus_raw is not None
    ownership = "mine" if has_focus else "none"
    focus_item = _focus_payload(focus_raw, reason="demo_scene") if focus_raw else None
    queue_preview = [_focus_payload(it, reason="queue") for it in queue_raw]
    shift_input = _split_queue_items([*( [focus_raw] if focus_raw else []), *queue_raw])
    risk_kzt = float(metrics.get("risk_kzt") or 0)

    payload: dict[str, Any] = {
        "location_id": None,
        "state": state,
        "priority_score": round(priority, 2),
        "focus": focus_item,
        "queue": queue_preview,
        "metrics": metrics,
        "actions": [],
        "compressed_actions": _build_compressed_actions(
            focus_item,
            has_focus=has_focus,
            ownership=ownership,  # type: ignore[arg-type]
        ),
        "live_impact": live_impact,
        "predictive_scene": _build_predictive_scene(focus_item, state=state, risk_kzt=risk_kzt),
        "presentation": _build_presentation(
            state=state,
            shift_input=shift_input,
            all_items=[*( [focus_raw] if focus_raw else []), *queue_raw],
            active_items=[*( [focus_raw] if focus_raw else []), *queue_raw],
            has_focus=has_focus,
            empty_reason=None if has_focus else "action_queue_cleared",
            projection_gap=False,
            state_reason=state_reason,
            ownership=ownership,  # type: ignore[arg-type]
            operator_id="demo",
            skipped=0,
            next_ids=0,
            done=0,
        ),
        "demo_scene": {
            "id": scene_id,
            "phase": phase,
            "auto_complete": auto_complete,
            "fullscreen": True,
            "narrative": _phase_narrative(phase),
        },
    }
    return {"ok": True, "organization_id": org_id, **payload}


def _phase_narrative(phase: str) -> str:
    return {
        "hook": "Клиент уже 3 минуты ждёт ответ…",
        "tension": "Риск ухода ↑",
        "action": "Оператор нажимает «Готово»",
        "impact": "Клиент возвращён → деньги спасены",
        "next": "Система продолжает зарабатывать",
    }.get(phase, "")


def build_demo_shift_state(scene_id: str, phase: str, *, org_id: int) -> dict[str, Any]:
    """Canned shift/state payload for demo scene phase."""
    meta = DEMO_SCENES.get(scene_id)
    if meta is None:
        raise KeyError(f"unknown demo scene: {scene_id}")
    phase = str(phase or "").strip().lower()
    if phase not in _PHASE_ORDER:
        raise KeyError(f"unknown demo phase: {phase}")

    if phase in ("hook", "tension", "action"):
        item = _hook_item() if phase == "hook" else _tension_item()
        auto = phase == "action"
        if phase == "action":
            item = _tension_item()
        return _wrap_shift_payload(
            org_id=org_id,
            scene_id=scene_id,
            phase=phase,
            state="S2",
            state_reason="red_chat_exists" if phase != "hook" else "slow_chats_yellow",
            focus_raw=item,
            queue_raw=[],
            metrics=_base_metrics(risk_kzt=DEMO_RESCUE_AMOUNT_KZT, recovered_today=0, at_risk=1, queue_size=1),
            live_impact=None,
            priority=92.0,
            auto_complete=auto,
        )

    if phase == "impact":
        live = build_live_impact_payload(
            last_action="focus_completed",
            kind="slow_chat",
            amount_kzt=DEMO_RESCUE_AMOUNT_KZT,
            wait_minutes=5,
            pulse="red",
        )
        live["impact_reason"] = "Клиент возвращён"
        live["outcome_emotion"] = "Продажа спасена"
        return _wrap_shift_payload(
            org_id=org_id,
            scene_id=scene_id,
            phase=phase,
            state="S3",
            state_reason="calm_low_risk",
            focus_raw=None,
            queue_raw=[],
            metrics=_base_metrics(
                risk_kzt=0,
                recovered_today=DEMO_RESCUE_AMOUNT_KZT,
                at_risk=0,
                queue_size=0,
            ),
            live_impact=live,
            priority=0.0,
        )

    # next
    nxt = _next_item()
    return _wrap_shift_payload(
        org_id=org_id,
        scene_id=scene_id,
        phase=phase,
        state="S2",
        state_reason="slow_chats_yellow",
        focus_raw=nxt,
        queue_raw=[],
        metrics=_base_metrics(
            risk_kzt=DEMO_RESCUE_AMOUNT_KZT * 2,
            recovered_today=DEMO_RESCUE_AMOUNT_KZT,
            at_risk=2,
            queue_size=2,
        ),
        live_impact=None,
        priority=88.0,
    )
