"""
G10.8 / G10.8.1 / G10.8.2 — scripted 30s demo scenes for sales/onboarding.

Counterfactual layer: WITHOUT SYSTEM → loss, WITH SYSTEM → saved.
Fixed narrative strings only (no LLM). GET-only — safe for demo sessions (POST blocked).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from app.services.shift_state_engine import (
    _build_compressed_actions,
    _build_predictive_scene,
    _build_presentation,
    _focus_payload,
    _split_queue_items,
    build_live_impact_payload,
)

DEMO_SCENE_MONEY_RESCUE_30S = "money_rescue_30s"
DEMO_SCENE_BOOKING_RESCUE_30S = "booking_rescue_30s"

DEMO_RESCUE_AMOUNT_KZT = 1200.0
DEMO_BOOKING_RESCUE_AMOUNT_KZT = 8500.0

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
    DEMO_SCENE_BOOKING_RESCUE_30S: [
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
    DEMO_SCENE_BOOKING_RESCUE_30S: {
        "title": "Спасение брони за 30 секунд",
        "tagline": "No-show → подтверждение → стол сохранён",
        "total_ms": 30000,
        "phases": SCENE_PHASES[DEMO_SCENE_BOOKING_RESCUE_30S],
        "primary_kind": "booking_at_risk",
    },
}


@dataclass(frozen=True)
class _SceneRuntime:
    amount_kzt: float
    impact_kind: str
    hook_reason: str
    tension_reason: str
    next_reason: str
    narratives: dict[str, str]
    resolve_headline: str
    resolve_stat: str
    resolve_summary: str
    hook_item: Callable[[], dict[str, Any]]
    tension_item: Callable[[], dict[str, Any]]
    next_item: Callable[[], dict[str, Any]]
    impact_emotion: str
    impact_saved_line: str
    auto_action_line: str


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
                "primary_kind": meta.get("primary_kind"),
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
    loss_would_be_kzt: float,
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


def _demo_chat_raw(
    *,
    amount_kzt: float,
    wait_minutes: int,
    pulse: str,
    focus_id: str,
    title: str,
    subtitle: str,
) -> dict[str, Any]:
    return {
        "id": focus_id,
        "kind": "slow_chat",
        "title": title,
        "subtitle": subtitle,
        "amount_kzt": amount_kzt,
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


def _demo_booking_raw(
    *,
    amount_kzt: float,
    focus_id: str,
    title: str,
    subtitle: str,
    wait_minutes: int = 0,
) -> dict[str, Any]:
    return {
        "id": focus_id,
        "kind": "booking_at_risk",
        "title": title,
        "subtitle": subtitle,
        "amount_kzt": amount_kzt,
        "wait_minutes": wait_minutes,
        "phone": "+77007654321",
        "booking_id": 9001,
        "priority_score": 90.0,
        "actions": [
            {"label": "Брони", "type": "navigate", "tab": "bookings"},
            {"label": "Написать гостю", "type": "navigate", "tab": "chats", "phone": "+77007654321"},
        ],
    }


def _money_runtime() -> _SceneRuntime:
    focus_a = "demo-scene-chat-001"
    focus_b = "demo-scene-chat-002"
    amt = DEMO_RESCUE_AMOUNT_KZT
    label = _money_label(amt)

    return _SceneRuntime(
        amount_kzt=amt,
        impact_kind="slow_chat",
        hook_reason="slow_chats_yellow",
        tension_reason="red_chat_exists",
        next_reason="slow_chats_yellow",
        narratives={
            "hook": "Клиент уже почти ушёл…",
            "tension": f"Было бы потеряно {label}",
            "action": "✔ Ответ отправлен автоматически",
            "impact": f"Клиент возвращён → +{label} спасено",
            "next": "Следующая утечка уже идёт",
            "resolve": "Вы уже теряли деньги — мы не дали этому случиться",
        },
        resolve_headline="Система автоматически спасает потерянные заказы",
        resolve_stat="В среднем: +12–18% восстановленных продаж",
        resolve_summary="Вы уже теряли деньги — мы просто не дали этому случиться",
        hook_item=lambda: _demo_chat_raw(
            amount_kzt=amt,
            focus_id=focus_a,
            wait_minutes=4,
            pulse="amber",
            title="Клиент уже почти ушёл…",
            subtitle="WhatsApp · 4 минуты без ответа",
        ),
        tension_item=lambda: _demo_chat_raw(
            amount_kzt=amt,
            focus_id=focus_a,
            wait_minutes=5,
            pulse="red",
            title="Клиент уже почти ушёл…",
            subtitle="Без ответа — потеря заказа",
        ),
        next_item=lambda: _demo_chat_raw(
            amount_kzt=amt,
            focus_id=focus_b,
            wait_minutes=2,
            pulse="amber",
            title="Следующий риск: 2 клиента ждут ответа",
            subtitle="Очередь не останавливается — это поток",
        ),
        impact_emotion="Клиент возвращён",
        impact_saved_line=f"Клиент возвращён → +{label} спасено",
        auto_action_line="✔ Ответ отправлен автоматически",
    )


def _booking_runtime() -> _SceneRuntime:
    focus_a = "demo-scene-booking-001"
    focus_b = "demo-scene-booking-002"
    amt = DEMO_BOOKING_RESCUE_AMOUNT_KZT
    label = _money_label(amt)

    return _SceneRuntime(
        amount_kzt=amt,
        impact_kind="booking_at_risk",
        hook_reason="booking_at_risk",
        tension_reason="booking_at_risk",
        next_reason="booking_at_risk",
        narratives={
            "hook": "Стол на 19:00 без подтверждения…",
            "tension": f"Было бы потеряно ~{label} (пустой стол)",
            "action": "✔ Подтверждение отправлено автоматически",
            "impact": f"Бронь подтверждена → +{label} спасено",
            "next": "Следующий риск: ещё 2 брони без ответа",
            "resolve": "Столы не простаивают — система держит брони под контролем",
        },
        resolve_headline="Система автоматически спасает брони от no-show",
        resolve_stat="Меньше пустых столов в пиковые часы",
        resolve_summary="Без подтверждения стол часто остаётся пустым — мы не даём этому случиться",
        hook_item=lambda: _demo_booking_raw(
            amount_kzt=amt,
            focus_id=focus_a,
            title="Стол на 19:00 без подтверждения…",
            subtitle="4 гостя · сегодня · no-show риск",
        ),
        tension_item=lambda: _demo_booking_raw(
            amount_kzt=amt,
            focus_id=focus_a,
            title="Стол на 19:00 без подтверждения…",
            subtitle="Без ответа — пустой стол и потеря выручки",
            wait_minutes=15,
        ),
        next_item=lambda: _demo_booking_raw(
            amount_kzt=amt,
            focus_id=focus_b,
            title="Следующий риск: 2 брони без ответа",
            subtitle="Поток броней не останавливается",
        ),
        impact_emotion="Бронь подтверждена",
        impact_saved_line=f"Бронь подтверждена → +{label} спасено",
        auto_action_line="✔ Подтверждение отправлено автоматически",
    )


def _scene_runtime(scene_id: str) -> _SceneRuntime:
    if scene_id == DEMO_SCENE_BOOKING_RESCUE_30S:
        return _booking_runtime()
    if scene_id == DEMO_SCENE_MONEY_RESCUE_30S:
        return _money_runtime()
    raise KeyError(f"unknown demo scene: {scene_id}")


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
    runtime: _SceneRuntime,
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
            "narrative": runtime.narratives.get(phase, ""),
            "counterfactual": counterfactual or {},
            "closing_headline": closing_headline,
            "closing_stat": closing_stat,
            "counterfactual_summary": counterfactual_summary,
        },
    }
    return {"ok": True, "organization_id": org_id, **payload}


def build_demo_shift_state(scene_id: str, phase: str, *, org_id: int) -> dict[str, Any]:
    """Canned shift/state payload for demo scene phase."""
    if scene_id not in DEMO_SCENES:
        raise KeyError(f"unknown demo scene: {scene_id}")
    phase = str(phase or "").strip().lower()
    if phase not in _PHASE_ORDER:
        raise KeyError(f"unknown demo phase: {phase}")

    runtime = _scene_runtime(scene_id)
    amount = runtime.amount_kzt
    amount_label = _money_label(amount)

    if phase == "hook":
        return _wrap_shift_payload(
            org_id=org_id,
            scene_id=scene_id,
            phase=phase,
            runtime=runtime,
            state="S2",
            state_reason=runtime.hook_reason,
            focus_raw=runtime.hook_item(),
            queue_raw=[],
            metrics=_base_metrics(risk_kzt=amount, recovered_today=0, at_risk=1, queue_size=1),
            live_impact=None,
            priority=92.0,
            counterfactual=_counterfactual(
                loss_would_be_kzt=amount,
                counterfactual_line=runtime.narratives["hook"],
                without_system_line="Без системы риск растёт",
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
            runtime=runtime,
            state="S2",
            state_reason=runtime.tension_reason,
            focus_raw=runtime.tension_item(),
            queue_raw=[],
            metrics=_base_metrics(risk_kzt=amount, recovered_today=0, at_risk=1, queue_size=1),
            live_impact=None,
            priority=94.0,
            counterfactual=_counterfactual(
                loss_would_be_kzt=amount,
                counterfactual_line=f"Было бы потеряно {amount_label}",
                without_system_line=f"−{amount_label} (риск)",
                with_system_line="Система предвосхищает потерю",
                urgency_sec=30,
                risk_increasing=True,
            ),
        )

    if phase == "action":
        return _wrap_shift_payload(
            org_id=org_id,
            scene_id=scene_id,
            phase=phase,
            runtime=runtime,
            state="S2",
            state_reason=runtime.tension_reason,
            focus_raw=runtime.tension_item(),
            queue_raw=[],
            metrics=_base_metrics(risk_kzt=amount, recovered_today=0, at_risk=1, queue_size=1),
            live_impact=None,
            priority=94.0,
            auto_complete=True,
            counterfactual=_counterfactual(
                loss_would_be_kzt=amount,
                counterfactual_line=f"Без системы: −{amount_label}",
                without_system_line=f"Без действия — потеря {amount_label}",
                with_system_line=runtime.auto_action_line,
                auto_action_line=runtime.auto_action_line,
                urgency_sec=15,
            ),
        )

    if phase == "impact":
        live = build_live_impact_payload(
            last_action="focus_completed",
            kind=runtime.impact_kind,
            amount_kzt=amount,
            wait_minutes=5,
            pulse="red",
        )
        live["impact_reason"] = runtime.impact_emotion
        live["outcome_prefix"] = f"Было бы потеряно {amount_label}"
        live["outcome_emotion"] = runtime.impact_emotion
        live["impact_money"] = f"+{amount_label} спасено"
        live["impact_text"] = runtime.impact_saved_line
        live["counterfactual_flash"] = True
        live["loss_would_be_kzt"] = amount
        live["loss_flash_line"] = f"−{amount_label} (риск)"
        return _wrap_shift_payload(
            org_id=org_id,
            scene_id=scene_id,
            phase=phase,
            runtime=runtime,
            state="S3",
            state_reason="calm_low_risk",
            focus_raw=None,
            queue_raw=[],
            metrics=_base_metrics(risk_kzt=0, recovered_today=amount, at_risk=0, queue_size=0),
            live_impact=live,
            priority=0.0,
            counterfactual=_counterfactual(
                loss_would_be_kzt=amount,
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
            runtime=runtime,
            state="S2",
            state_reason=runtime.next_reason,
            focus_raw=runtime.next_item(),
            queue_raw=[],
            metrics=_base_metrics(
                risk_kzt=amount * 2,
                recovered_today=amount,
                at_risk=2,
                queue_size=2,
            ),
            live_impact=None,
            priority=88.0,
            counterfactual=_counterfactual(
                loss_would_be_kzt=amount,
                counterfactual_line=runtime.narratives["next"],
                without_system_line="Без системы — поток потерь",
                with_system_line="Система держит поток под контролем",
            ),
        )

    return _wrap_shift_payload(
        org_id=org_id,
        scene_id=scene_id,
        phase=phase,
        runtime=runtime,
        state="S3",
        state_reason="calm_low_risk",
        focus_raw=None,
        queue_raw=[],
        metrics=_base_metrics(risk_kzt=0, recovered_today=amount, at_risk=1, queue_size=1),
        live_impact=None,
        priority=0.0,
        closing_headline=runtime.resolve_headline,
        closing_stat=runtime.resolve_stat,
        counterfactual_summary=runtime.resolve_summary,
        counterfactual=_counterfactual(
            loss_would_be_kzt=amount,
            counterfactual_line=runtime.resolve_summary,
            without_system_line="Каждый день — незаметные потери",
            with_system_line="Предотвращённая потеря = реальная выручка",
        ),
    )
