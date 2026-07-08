"""Role-first Admin IA (Sprint 5 pivot) — role matrix, smart operator landing, analytics density."""

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_role_nav_constants_and_helpers_in_js():
    js = (REPO / "app" / "static" / "js" / "admin-app.js").read_text(encoding="utf-8")
    assert "const ADMIN_ROLE_TABS" in js
    assert "function adminTabVisibleForRole" in js
    assert "function adminResolveOperatorLandingTab" in js
    assert "window.adminRoleNav" in js
    assert "isTabVisibleForRole(tabId)" in js
    assert "resolveOperatorLandingTab()" in js
    assert "applyRoleDefaultLanding" in js
    assert "analyticsDensity:" in js
    assert "setAnalyticsDensity(next)" in js
    assert "shiftIsCalmEmpty()" in js


def test_sidebar_filters_by_role():
    sidebar = (REPO / "app" / "templates" / "screens" / "_sidebar.html").read_text(encoding="utf-8")
    assert "isTabShownInSidebar(i)" in sidebar
    assert "isTabInCurrentMode" not in sidebar


def test_mode_bar_removed_from_header():
    header = (REPO / "app" / "templates" / "screens" / "_header.html").read_text(encoding="utf-8")
    assert "mode_bar(" not in header
    admin = (REPO / "app" / "templates" / "admin.html").read_text(encoding="utf-8")
    assert "_mode_bar.html" not in admin


def test_dashboard_analytics_density_toggle():
    dash = (REPO / "app" / "templates" / "screens" / "_tab_dashboard.html").read_text(encoding="utf-8")
    assert "setAnalyticsDensity('normal')" in dash
    assert "setAnalyticsDensity('advanced')" in dash
    assert "Подробная аналитика" in dash
    assert "Обзор" in dash
    assert dash.count("Подробная аналитика") == 1
    assert "analyticsDensity === 'normal'" in dash
    analytics = (REPO / "app" / "templates" / "screens" / "_tab_analytics.html").read_text(encoding="utf-8")
    assert "analyticsDensity === 'advanced'" in analytics


def test_owner_command_center_and_dashboard_drilldown():
    dash = (REPO / "app" / "templates" / "screens" / "_tab_dashboard.html").read_text(encoding="utf-8")
    js = (REPO / "app" / "static" / "js" / "admin-app.js").read_text(encoding="utf-8")
    assert "data-owner-command-center" not in dash
    assert "data-owner-legacy-sales-summary" not in dash
    assert "Рабочий слой продаж" not in dash
    assert "openDashboardDrilldown('money')" in dash
    assert "openDashboardDrilldown('guests')" in dash
    assert "openDashboardDrilldown('ai')" in dash
    assert "dashboardDrilldownOpen" in dash
    assert "dashboardDrilldownGoFull()" in dash
    assert "openDashboardDrilldown(key" in js
    assert "dashboardDrilldownMetrics()" in js
    assert "return flag === true" in js


def test_ai_center_is_source_layer_not_daily_landing():
    ai_center = (REPO / "app" / "templates" / "screens" / "_tab_ai_center.html").read_text(encoding="utf-8")
    assert "data-ai-center-source-lab" not in ai_center
    assert "Source layer" not in ai_center
    assert "data-ai-center-business-archive" in ai_center
    assert "navigateToTab('dashboard', { dashboardTab: 'overview' })" in ai_center


def test_executive_hub_has_owner_scope_switcher():
    hub = (REPO / "app" / "templates" / "screens" / "_executive_hub.html").read_text(encoding="utf-8")
    api = (REPO / "app" / "api" / "admin" / "intelligence.py").read_text(encoding="utf-8")
    assert "executive-hub-location-scope" in hub
    assert "selectedLocationId" in hub
    assert "setSelectedLocation($event.target.value); loadExecutiveHub()" in hub
    assert "Вся сеть" in hub
    assert "Все точки" in hub
    assert '"summary": payload.get("summary") or {}' in api
    assert '"next_actions": payload.get("next_actions") or []' in api
    assert '"readiness": payload.get("readiness") or {}' in api


def test_operator_keeps_only_execution_tabs():
    js = (REPO / "app" / "static" / "js" / "admin-app.js").read_text(encoding="utf-8")
    assert "operator: Object.freeze(['shift', 'inbox', 'orders', 'chats', 'bookings'])" in js
    assert "const ADMIN_OPERATOR_SECONDARY_TABS = Object.freeze(['orders', 'chats', 'bookings'])" in js
    assert "canOpenExecutiveHub()" in js
    assert "return this.effectiveStaffRole() !== 'operator';" in js


def test_shift_calm_empty_cta():
    shift = (REPO / "app" / "templates" / "screens" / "_tab_shift_control.html").read_text(encoding="utf-8")
    assert "shiftIsCalmEmpty()" in shift
    assert "navigateToTab('inbox')" in shift
    assert "navigateToTab('chats')" in shift


def test_bottom_nav_role_aware():
    bottom = (REPO / "app" / "templates" / "screens" / "_bottom_nav.html").read_text(encoding="utf-8")
    assert "isTabVisibleForRole(" in bottom
    assert "bottomNavMoreTabActive()" in bottom
    assert "effectiveStaffRole() === 'operator'" in bottom
    assert "@click=\"navigateToTab('inbox')\"" in bottom


def test_shift_polling_helpers_in_js():
    js = (REPO / "app" / "static" / "js" / "admin-app.js").read_text(encoding="utf-8")
    assert "shouldPollShiftStateBadge()" in js
    assert "_syncShiftStatePolling()" in js
    assert "_afterAuthTabBootstrap()" in js
    assert "_persistAnalyticsDensity()" in js


def test_demo_login_starts_pitch_scene():
    js = (REPO / "app" / "static" / "js" / "admin-app.js").read_text(encoding="utf-8")
    demo_block = js.split("async submitDemoLogin()")[1].split("async loadOrgProfile()")[0]
    assert "startDemoShiftScene" in demo_block
    assert "DEMO_SHIFT_SCENE_DEFAULT" in demo_block
    assert "currentTab = 'dashboard'" not in demo_block


def test_admin_resolve_operator_landing_tab():
    def resolve(shift_state):
        ss = shift_state or {}
        metrics = ss.get("metrics") or {}
        if float(metrics.get("risk_kzt") or 0) > 0:
            return "shift"
        focus = ss.get("focus") or {}
        if focus.get("id"):
            return "shift"
        return "inbox"

    assert resolve({"metrics": {"risk_kzt": 0}, "focus": {}}) == "inbox"
    assert resolve({"metrics": {"risk_kzt": 100}, "focus": {}}) == "shift"
    assert resolve({"metrics": {"risk_kzt": 0}, "focus": {"id": "x1"}}) == "shift"
