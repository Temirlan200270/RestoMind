"""G10.8 / G10.8.1 — scripted demo shift scene + counterfactual pitch."""

from __future__ import annotations

import pytest

from app.services.demo_shift_scene import (
    DEMO_SCENE_MONEY_RESCUE_30S,
    DEMO_RESCUE_AMOUNT_KZT,
    build_demo_shift_state,
    list_demo_shift_scenes,
)


def test_list_demo_shift_scenes() -> None:
    scenes = list_demo_shift_scenes()
    assert len(scenes) >= 1
    assert scenes[0]["id"] == DEMO_SCENE_MONEY_RESCUE_30S
    assert scenes[0]["total_ms"] == 30000
    assert any(p["id"] == "hook" for p in scenes[0]["phases"])
    assert any(p["id"] == "resolve" for p in scenes[0]["phases"])


@pytest.mark.parametrize(
    "phase",
    ["hook", "tension", "action", "impact", "next", "resolve"],
)
def test_build_demo_shift_state_phases(phase: str) -> None:
    payload = build_demo_shift_state(DEMO_SCENE_MONEY_RESCUE_30S, phase, org_id=501)
    assert payload["ok"] is True
    assert payload["organization_id"] == 501
    assert payload["demo_scene"]["id"] == DEMO_SCENE_MONEY_RESCUE_30S
    assert payload["demo_scene"]["phase"] == phase
    assert payload["demo_scene"]["fullscreen"] is True
    assert payload["demo_scene"]["pitch_immersive"] is True


def test_hook_has_almost_left_focus_and_counterfactual() -> None:
    payload = build_demo_shift_state(DEMO_SCENE_MONEY_RESCUE_30S, "hook", org_id=1)
    focus = payload["focus"]
    assert focus is not None
    assert focus["kind"] == "slow_chat"
    assert focus["wait_minutes"] == 4
    assert "почти ушёл" in focus["title"].lower()
    cf = payload["demo_scene"]["counterfactual"]
    assert cf["loss_would_be_kzt"] == DEMO_RESCUE_AMOUNT_KZT
    assert cf["risk_increasing"] is True
    assert cf["urgency_sec"] >= 30
    assert payload["live_impact"] is None


def test_tension_escalates_loss_counterfactual() -> None:
    payload = build_demo_shift_state(DEMO_SCENE_MONEY_RESCUE_30S, "tension", org_id=1)
    focus = payload["focus"]
    assert focus is not None
    assert focus["wait_minutes"] >= 5
    assert focus["pulse"] == "red"
    cf = payload["demo_scene"]["counterfactual"]
    assert "потеряно" in cf["counterfactual_line"].lower()
    assert cf["urgency_sec"] == 30


def test_action_has_auto_action_line() -> None:
    payload = build_demo_shift_state(DEMO_SCENE_MONEY_RESCUE_30S, "action", org_id=1)
    assert payload["demo_scene"]["auto_complete"] is True
    cf = payload["demo_scene"]["counterfactual"]
    assert "автоматически" in cf["auto_action_line"].lower()


def test_impact_has_counterfactual_live_impact() -> None:
    payload = build_demo_shift_state(DEMO_SCENE_MONEY_RESCUE_30S, "impact", org_id=1)
    assert payload["focus"] is None
    live = payload["live_impact"]
    assert live is not None
    assert live["last_action"] == "focus_completed"
    assert live["counterfactual_flash"] is True
    assert live["loss_would_be_kzt"] == DEMO_RESCUE_AMOUNT_KZT
    assert "потеряно" in live["outcome_prefix"].lower()
    assert live["outcome_emotion"] == "Клиент возвращён"
    assert "спасено" in live["impact_money"].lower()
    assert payload["metrics"]["recovered_today_kzt"] == 1200.0


def test_next_shows_follow_up_risk() -> None:
    payload = build_demo_shift_state(DEMO_SCENE_MONEY_RESCUE_30S, "next", org_id=1)
    focus = payload["focus"]
    assert focus is not None
    assert "2 клиента" in focus["title"]


def test_resolve_has_closing_copy() -> None:
    payload = build_demo_shift_state(DEMO_SCENE_MONEY_RESCUE_30S, "resolve", org_id=1)
    assert payload["focus"] is None
    ds = payload["demo_scene"]
    assert "автоматически" in ds["closing_headline"].lower()
    assert "12" in ds["closing_stat"]
    assert ds["counterfactual_summary"]


def test_unknown_scene_raises() -> None:
    with pytest.raises(KeyError):
        build_demo_shift_state("unknown_scene", "hook", org_id=1)


def test_unknown_phase_raises() -> None:
    with pytest.raises(KeyError):
        build_demo_shift_state(DEMO_SCENE_MONEY_RESCUE_30S, "invalid", org_id=1)


@pytest.mark.asyncio
async def test_demo_shift_scene_api_requires_demo_or_debug(asgi_memory_client) -> None:
    client, _ = asgi_memory_client
    res = await client.get("/api/admin/demo/shift-scene/money_rescue_30s/state?phase=hook")
    assert res.status_code in (401, 403)


@pytest.mark.asyncio
async def test_demo_shift_scene_api_with_app_debug(asgi_memory_client, monkeypatch) -> None:
    from app.core.config import settings
    from app.core.passwords import hash_password
    from app.db.models import Organization, StaffRole, StaffUser

    monkeypatch.setattr(settings, "app_debug", True)

    client, session_factory = asgi_memory_client

    async with session_factory() as db:
        org = Organization(name="Debug Org", slug="debug-scene", is_active=True)
        db.add(org)
        await db.flush()
        staff = StaffUser(
            organization_id=int(org.id),
            email="debug-scene@test.local",
            password_hash=hash_password("secret"),
            role=StaffRole.OPERATOR.value,
            is_active=True,
        )
        db.add(staff)
        await db.commit()

    login = await client.post(
        "/api/admin/auth/login",
        json={"username": "debug-scene@test.local", "password": "secret"},
    )
    assert login.status_code == 200

    res = await client.get("/api/admin/demo/shift-scene/money_rescue_30s/state?phase=impact")
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["demo_scene"]["phase"] == "impact"
    assert data["live_impact"]["counterfactual_flash"] is True
    assert "спасено" in data["live_impact"]["impact_money"].lower()

    resolve = await client.get("/api/admin/demo/shift-scene/money_rescue_30s/state?phase=resolve")
    assert resolve.status_code == 200
    resolve_data = resolve.json()
    assert resolve_data["demo_scene"]["closing_headline"]
