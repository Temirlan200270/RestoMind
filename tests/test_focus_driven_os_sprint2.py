"""Focus-Driven OS Sprint 2 — shift split, Context Dock, mobile staged nav."""

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_shift_context_dock_templates_exist():
    chat = REPO / "app" / "templates" / "screens" / "_shift_focus_chat.html"
    order = REPO / "app" / "templates" / "screens" / "_shift_focus_order.html"
    assert chat.is_file()
    assert order.is_file()
    chat_text = chat.read_text(encoding="utf-8")
    order_text = order.read_text(encoding="utf-8")
    assert "shiftDockOpenChat" in chat_text
    assert "shiftDockRunFocusAction" in order_text
    assert "Состав корзины" in order_text


def test_shift_tab_split_and_includes():
    tab = (REPO / "app" / "templates" / "screens" / "_tab_shift_control.html").read_text(encoding="utf-8")
    assert "ds-shift-split" in tab
    assert "_shift_focus_chat.html" in tab
    assert "_shift_focus_order.html" in tab
    assert "_focus_card.html" in tab
    assert "focus_card()" in tab
    focus_macro = (REPO / "app" / "templates" / "components" / "_focus_card.html").read_text(encoding="utf-8")
    assert "openShiftContext" in focus_macro
    assert "backToShiftFocus" in tab
    assert "mobileActiveScreen" in tab
    assert "shiftFocusShowsChatDock" in tab
    assert "shiftFocusShowsOrderDock" in tab


def test_focus_card_spec_and_mapper():
    spec = (REPO / "docs" / "FOCUS_CARD_SPEC.md").read_text(encoding="utf-8")
    macro = (REPO / "app" / "templates" / "components" / "_focus_card.html").read_text(encoding="utf-8")
    js = (REPO / "app" / "static" / "js" / "admin-app.js").read_text(encoding="utf-8")
    assert "adminFocusCardFromShiftState" in spec
    assert "focusCardView()" in macro
    assert "function adminFocusCardFromShiftState" in js
    assert "focusCardFromShiftState()" in js
    assert "openMoneyQueueItemViaShift" in js


def test_operator_scene_shell_v2_markers():
    sidebar = (REPO / "app" / "templates" / "screens" / "_sidebar.html").read_text(encoding="utf-8")
    inbox = (REPO / "app" / "templates" / "screens" / "_tab_inbox.html").read_text(encoding="utf-8")
    js = (REPO / "app" / "static" / "js" / "admin-app.js").read_text(encoding="utf-8")
    assert "navItemDisplayLabel" in sidebar
    assert "isNavExecutionPrimary" in sidebar
    assert "openInboxShiftHero" in inbox
    assert "runMoneyQueueAction(act, item)" in inbox
    assert "navItemDisplayLabel" in js
    assert "shouldRouteMoneyQueueViaShift" in js


def test_shift_staged_nav_mixin_wired():
    js = (REPO / "app" / "static" / "js" / "admin-app.js").read_text(encoding="utf-8")
    assert "function adminMixinShiftStagedNav()" in js
    assert "adminMixinShiftStagedNav()," in js
    assert "mobileActiveScreen: 'focus'" in js
    assert "openShiftContext()" in js
    assert "backToShiftFocus()" in js
    assert "shiftHasContextDock()" in js


def test_shift_split_css_tokens_present():
    css = (REPO / "src" / "css" / "admin-input.css").read_text(encoding="utf-8")
    assert ".ds-shift-split" in css
    assert ".ds-shift-focus-pane" in css
    assert ".ds-shift-context-pane" in css
    assert ".ds-shift-pane--hidden-mobile" in css
    assert ".ds-shift-staged-back" in css
