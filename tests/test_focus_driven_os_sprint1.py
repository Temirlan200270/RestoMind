"""Focus-Driven OS Sprint 1 — shell markers (Mode Engine, Mode Bar, sidebar filter)."""

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_admin_mode_engine_wired_in_js():
    js = (REPO / "app" / "static" / "js" / "admin-app.js").read_text(encoding="utf-8")
    assert "function adminMixinModeEngine()" in js
    assert "window.adminModeEngine" in js
    assert "isTabInCurrentMode(tabId)" in js
    assert "ADMIN_MODE_TABS" in js
    assert "adminMixinModeEngine()," in js


def test_sidebar_filters_by_current_mode():
    sidebar = (REPO / "app" / "templates" / "screens" / "_sidebar.html").read_text(encoding="utf-8")
    assert "isTabInCurrentMode(i.id)" in sidebar


def test_mode_bar_css_tokens_present():
    css = (REPO / "src" / "css" / "admin-input.css").read_text(encoding="utf-8")
    assert ".ds-mode-bar" in css
    assert ".ds-mode-bar-btn" in css
    assert ".ds-mode-bar-indicator" in css
    assert ".ds-status-shift" in css
    assert ".ds-status-control" in css
    assert ".ds-status-intelligence" in css
    assert "--color-mode-shift" in css
