"""G10.8 — demo scene UI hooks."""

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_login_has_30s_demo_button() -> None:
    login = (REPO / "app" / "templates" / "screens" / "_login.html").read_text(encoding="utf-8")
    assert "submitDemoLoginWithScene('money_rescue_30s')" in login
    assert "30 сек" in login


def test_admin_app_demo_scene_engine() -> None:
    js = (REPO / "app" / "static" / "js" / "admin-app.js").read_text(encoding="utf-8")
    assert "DEMO_SHIFT_SCENE_DEFAULT" in js
    assert "startDemoShiftScene(" in js
    assert "stopDemoShiftScene(" in js
    assert "_runDemoSceneAutoComplete" in js
    assert "demoSceneActive" in js


def test_shift_tab_demo_banner() -> None:
    shift = (REPO / "app" / "templates" / "screens" / "_tab_shift_control.html").read_text(encoding="utf-8")
    assert "rm-demo-scene-banner" in shift
    assert "demoSceneNarrativeLine()" in shift


def test_admin_shell_demo_scene_class() -> None:
    admin = (REPO / "app" / "templates" / "admin.html").read_text(encoding="utf-8")
    assert "rm-demo-scene" in admin
    assert "demoSceneActive" in admin
