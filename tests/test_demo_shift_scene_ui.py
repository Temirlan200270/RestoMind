"""G10.8 — demo scene UI hooks."""

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_login_has_unified_demo_button() -> None:
    login = (REPO / "app" / "templates" / "screens" / "_login.html").read_text(encoding="utf-8")
    assert "submitDemoLogin()" in login
    assert "submitDemoLoginWithScene" not in login
    assert "Посмотреть демо" in login
    assert login.count("submitDemoLogin()") == 1


def test_admin_app_demo_pitch_unified() -> None:
    js = (REPO / "app" / "static" / "js" / "admin-app.js").read_text(encoding="utf-8")
    demo_block = js.split("async submitDemoLogin()")[1].split("async loadOrgProfile()")[0]
    assert "DEMO_SHIFT_SCENE_DEFAULT" in demo_block
    assert "startDemoShiftScene" in demo_block
    assert "replayDemoPitchScene" in js
    assert "resolve" in js
    assert "demoSceneCounterfactualLine" in js
    assert "playDemoSuccessTick" in js
    assert "shiftLiveImpactLossFlashLine" in js
    assert "skipShiftReload" in js


def test_shift_tab_counterfactual_pitch_ui() -> None:
    shift = (REPO / "app" / "templates" / "screens" / "_tab_shift_control.html").read_text(encoding="utf-8")
    assert "ds-demo-counterfactual-banner" in shift
    assert "ds-demo-resolve-card" in shift
    assert "ds-demo-action-confirm" in shift
    assert "demoSceneResolveVisible()" in shift


def test_header_hides_readiness_in_demo() -> None:
    header = (REPO / "app" / "templates" / "screens" / "_header.html").read_text(encoding="utf-8")
    assert "!isDemoSession" in header
    assert "Готовность" in header


def test_focus_card_hides_actions_in_pitch() -> None:
    card = (REPO / "app" / "templates" / "components" / "_focus_card.html").read_text(encoding="utf-8")
    assert "demoScenePitchImmersive()" in card


def test_shift_tab_demo_banner() -> None:
    shift = (REPO / "app" / "templates" / "screens" / "_tab_shift_control.html").read_text(encoding="utf-8")
    assert "rm-demo-scene-banner" in shift
    assert "demoSceneNarrativeLine()" in shift


def test_admin_shell_demo_scene_class() -> None:
    admin = (REPO / "app" / "templates" / "admin.html").read_text(encoding="utf-8")
    assert "rm-demo-scene" in admin
    assert "demoSceneActive" in admin
