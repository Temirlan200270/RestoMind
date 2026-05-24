"""
G10.8 / G10.8.1 — scripted 30s «money rescue» demo scene for sales/onboarding.

Counterfactual layer: WITHOUT SYSTEM → loss, WITH SYSTEM → saved.
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

_PHASE_ORDER = ("hook", "tension", "action", "impact", "next", "resolve")

SCENE_PHASES: dict[str, list[dict[str, Any]]] = {
    DEMO_SCENE_MONEY_RESCUE_30S: [
        {"id": "hook", "delay_ms": 0, "label": "Боль"},
        {"id": "tension", "delay_ms": 5000, "label": "Контрфакт"},
        {"id": "action", "delay_ms": 10000, "label": "Вмешательство", "auto_complete": True},
        {"id": "impact", "delay_ms": 15000, "label": "Спасение"},
        {"id": "next", "delay_ms": 20000, "label": "Поток"},
        {"id": "resolve", "delay_ms": 25000, "label": "Закрепление"},
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


def _money_label(amount: float) -> str:
    n = int(round(amount))
    formatted = f"{n:,}".replace(",", " ")
    return f"{formatted} ₸"


def _counterfactual(
    *,
    counterfactual_line: str,
    without_system_line: str = "",
    with_system_line: str = "",
    loss_would_be_kzt: float = DEMO_RESCUE_AMOUNT_KZT,
    urgency_sec: int = 0,
    auto_action_line: str = "",
    risk_increasing: bool = False,
) -> dict[str, Any]:
    return {
        "loss_would_be_kzt": round(float(loss_would_be_kzt), 2),
        "counterfactual_line": counterfactual_line,
        "without_system_line": without_system_line,
        "with_system_line": with_system_line,
        "urgency_sec": int(urgency_sec or 0),
        "auto_action_line": auto_action_line,
        "risk_increasing": bool(risk_increasing),
    }


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
        wait_minutes=4,
        pulse="amber",
        title="Клиент уже почти ушёл…",
        subtitle="WhatsApp · 4 минуты без ответа",
    )


def _tension_item() -> dict[str, Any]:
    return _demo_chat_raw(
        focus_id=_DEMO_FOCUS_ID,
        wait_minutes=5,
        pulse="red",
        title="Клиент уже почти ушёл…",
        subtitle="Без ответа — потеря заказа",
    )


def _next_item() -> dict[str, Any]:
    return _demo_chat_raw(
        focus_id=_DEMO_NEXT_FOCUS_ID,
        wait_minutes=2,
        pulse="amber",
        title="Следующий риск: 2 клиента ждут ответа",
        subtitle="Очередь не останавливается — это поток",
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
    counterfactual: dict[str, Any] | None = None,
    closing_headline: str = "",
    closing_stat: str = "",
    counterfactual_summary: str = "",
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
            "pitch_immersive": True,
            "narrative": _phase_narrative(phase),
            "counterfactual": counterfactual or {},
            "closing_headline": closing_headline,
            "closing_stat": closing_stat,
            "counterfactual_summary": counterfactual_summary,
        },
    }
    return {"ok": True, "organization_id": org_id, **payload}


def _phase_narrative(phase: str) -> str:
    return {
        "hook": "Клиент уже почти ушёл…",
        "tension": "Было бы потеряно 1 200 ₸",
        "action": "✔ Ответ отправлен автоматически",
        "impact": "Клиент возвращён → +1 200 ₸ спасено",
        "next": "Следующая утечка уже идёт",
        "resolve": "Вы уже теряли деньги — мы не дали этому случиться",
    }.get(phase, "")


def build_demo_shift_state(scene_id: str, phase: str, *, org_id: int) -> dict[str, Any]:
    """Canned shift/state payload for demo scene phase."""
    meta = DEMO_SCENES.get(scene_id)
    if meta is None:
        raise KeyError(f"unknown demo scene: {scene_id}")
    phase = str(phase or "").strip().lower()
    if phase not in _PHASE_ORDER:
        raise KeyError(f"unknown demo phase: {phase}")

    amount_label = _money_label(DEMO_RESCUE_AMOUNT_KZT)

    if phase == "hook":
        return _wrap_shift_payload(
            org_id=org_id,
            scene_id=scene_id,
            phase=phase,
            state="S2",
            state_reason="slow_chats_yellow",
            focus_raw=_hook_item(),
            queue_raw=[],
            metrics=_base_metrics(risk_kzt=DEMO_RESCUE_AMOUNT_KZT, recovered_today=0, at_risk=1, queue_size=1),
            live_impact=None,
            priority=92.0,
            counterfactual=_counterfactual(
                counterfactual_line="Без системы клиент уходит через несколько минут",
                without_system_line="Без ответа — клиент уйдёт",
                with_system_line="Система перехватывает риск",
                urgency_sec=45,
                risk_increasing=True,
            ),
        )

    if phase == "tension":
        return _wrap_shift_payload(
            org_id=org_id,
            scene_id=scene_id,
            phase=phase,
            state="S2",
            state_reason="red_chat_exists",
            focus_raw=_tension_item(),
            queue_raw=[],
            metrics=_base_metrics(risk_kzt=DEMO_RESCUE_AMOUNT_KZT, recovered_today=0, at_risk=1, queue_size=1),
            live_impact=None,
            priority=94.0,
            counterfactual=_counterfactual(
                counterfactual_line=f"Было бы потеряно {amount_label}",
                without_system_line=f"−{amount_label} (риск)",
                with_system_line="Система предвосхищает уход",
                urgency_sec=30,
                risk_increasing=True,
            ),
        )

    if phase == "action":
        return _wrap_shift_payload(
            org_id=org_id,
            scene_id=scene_id,
            phase=phase,
            state="S2",
            state_reason="red_chat_exists",
            focus_raw=_tension_item(),
            queue_raw=[],
            metrics=_base_metrics(risk_kzt=DEMO_RESCUE_AMOUNT_KZT, recovered_today=0, at_risk=1, queue_size=1),
            live_impact=None,
            priority=94.0,
            auto_complete=True,
            counterfactual=_counterfactual(
                counterfactual_line=f"Без системы: −{amount_label}",
                without_system_line=f"Без ответа — потеря {amount_label}",
                with_system_line="✔ Ответ отправлен автоматически",
                auto_action_line="✔ Ответ отправлен автоматически",
                urgency_sec=15,
            ),
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
        live["outcome_prefix"] = f"Было бы потеряно {amount_label}"
        live["outcome_emotion"] = "Клиент возвращён"
        live["impact_money"] = f"+{amount_label} спасено"
        live["impact_text"] = f"Клиент возвращён → +{amount_label} спасено"
        live["counterfactual_flash"] = True
        live["loss_would_be_kzt"] = DEMO_RESCUE_AMOUNT_KZT
        live["loss_flash_line"] = f"−{amount_label} (риск)"
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
            counterfactual=_counterfactual(
                counterfactual_line=f"Было бы потеряно {amount_label} → не потеряно",
                without_system_line=f"−{amount_label} (риск)",
                with_system_line=f"+{amount_label} спасено",
            ),
        )

    if phase == "next":
        return _wrap_shift_payload(
            org_id=org_id,
            scene_id=scene_id,
            phase=phase,
            state="S2",
            state_reason="slow_chats_yellow",
            focus_raw=_next_item(),
            queue_raw=[],
            metrics=_base_metrics(
                risk_kzt=DEMO_RESCUE_AMOUNT_KZT * 2,
                recovered_today=DEMO_RESCUE_AMOUNT_KZT,
                at_risk=2,
                queue_size=2,
            ),
            live_impact=None,
            priority=88.0,
            counterfactual=_counterfactual(
                counterfactual_line="Следующая утечка уже идёт",
                without_system_line="Без системы — поток потерь",
                with_system_line="Система держит поток под контролем",
            ),
        )

    # resolve — closing frame 25–30s
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
            at_risk=1,
            queue_size=1,
        ),
        live_impact=None,
        priority=0.0,
        closing_headline="Система автоматически спасает потерянные заказы",
        closing_stat="В среднем: +12–18% восстановленных продаж",
        counterfactual_summary="Вы уже теряли деньги — мы просто не дали этому случиться",
        counterfactual=_counterfactual(
            counterfactual_line="Вы уже теряли деньги — мы просто не дали этому случиться",
            without_system_line="Каждый день — незаметные потери",
            with_system_line="Предотвращённая потеря = реальная выручка",
        ),
    )
