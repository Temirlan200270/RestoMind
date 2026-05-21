"""Focus-Driven OS Sprint 4 — Command Bar (Ctrl+K) smoke markers."""

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_admin_command_bar_mixin_wired_in_js():
    js = (REPO / "app" / "static" / "js" / "admin-app.js").read_text(encoding="utf-8")
    assert "function adminMixinCommandBar()" in js
    assert "commandBarOpen" in js
    assert "commandQuery" in js
    assert "parseCommand(" in js
    assert "executeCommand(" in js
    assert "handleCommandBarKeydown(" in js
    assert "adminMixinCommandBar()," in js
    assert "window.adminCommandBar" in js
    assert "ADMIN_COMMAND_DEFINITIONS" in js


def test_command_bar_template_included():
    modals = (REPO / "app" / "templates" / "screens" / "_modals.html").read_text(encoding="utf-8")
    assert "components/_command_bar.html" in modals
    bar = (REPO / "app" / "templates" / "components" / "_command_bar.html").read_text(encoding="utf-8")
    assert 'id="command-bar-input"' in bar
    assert "commandBarOpen" in bar


def test_command_prefix_parsing_leak_red_force_close():
    js = (REPO / "app" / "static" / "js" / "admin-app.js").read_text(encoding="utf-8")
    assert "prefix: '/leak'" in js
    assert "prefix: '/red'" in js
    assert "prefix: '/force-close'" in js
    assert "function adminParseCommand(query)" in js
    assert "adminCommandBarSuggestions" in js


def test_command_bar_css_tokens_present():
    css = (REPO / "src" / "css" / "admin-input.css").read_text(encoding="utf-8")
    assert ".ds-command-bar-overlay" in css
    assert ".ds-command-bar-panel" in css
    assert ".ds-command-bar-input" in css


def test_admin_html_keyboard_handlers():
    admin = (REPO / "app" / "templates" / "admin.html").read_text(encoding="utf-8")
    assert "handleCommandBarKeydown($event)" in admin
    assert "handleGlobalKeydown($event)" in admin
