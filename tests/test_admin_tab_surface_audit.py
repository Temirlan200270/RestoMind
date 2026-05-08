"""Маркеры и клиентский аудит видимости «болезненных» вкладок админки (меню / аналитика / профиль)."""

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_tab_surfaces_present_in_templates():
    menu = (REPO / "app" / "templates" / "screens" / "_tab_menu.html").read_text(encoding="utf-8")
    assert 'data-rm-tab-surface="menu"' in menu
    assert 'x-show="currentTab === \'menu\'"' in menu
    assert 'template x-if="currentTab === \'menu\'"' not in menu

    analytics = (REPO / "app" / "templates" / "screens" / "_tab_analytics.html").read_text(encoding="utf-8")
    assert 'data-rm-tab-surface="dashboard-analytics"' in analytics

    restaurant = (REPO / "app" / "templates" / "screens" / "_tab_settings_restaurant.html").read_text(
        encoding="utf-8"
    )
    assert 'data-rm-tab-surface="settings-restaurant"' in restaurant


def test_analytics_included_from_dashboard_not_admin_root():
    admin_html = (REPO / "app" / "templates" / "admin.html").read_text(encoding="utf-8")
    dashboard = (REPO / "app" / "templates" / "screens" / "_tab_dashboard.html").read_text(encoding="utf-8")
    assert '_tab_analytics.html' in dashboard
    # Не дублируем include на корневом admin.html (аналитика внутри дашборда).
    lines = [ln.strip() for ln in admin_html.splitlines() if "_tab_analytics.html" in ln]
    assert lines == []


def test_admin_app_has_tab_surface_audit_helpers():
    js = (REPO / "app" / "static" / "js" / "admin-app.js").read_text(encoding="utf-8")
    assert "function adminIsDomElementVisible(" in js
    assert "function adminTabSurfaceAuditTarget(" in js
    assert "_auditActiveTabSurface(" in js
    assert "afterLoadTabData" in js
    assert '[data-rm-tab-surface="menu"]' in js
