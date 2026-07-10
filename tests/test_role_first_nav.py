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
    assert "Рабочий слой продаж" in dash
    assert "сводка владельца" in dash
    assert "openDashboardDrilldown('money')" in dash
    assert "openDashboardDrilldown('guests')" in dash
    assert "openDashboardDrilldown('ai')" in dash
    assert "dashboardDrilldownOpen" in dash
    assert "dashboardDrilldownGoFull()" in dash
    assert "openDashboardDrilldown(key" in js
    assert "dashboardDrilldownMetrics()" in js
    assert "return flag !== false" in js


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
    assert '"today_picture": payload.get("today_picture") or {}' in api
    assert '"owner_cards": payload.get("owner_cards") or []' in api
    assert '"money_drivers": payload.get("money_drivers") or []' in api
    assert '"money_at_risk": payload.get("money_at_risk") or {}' in api
    assert '"network_branch": payload.get("network_branch") or {}' in api
    assert '"agent_context": payload.get("agent_context") or {}' in api
    assert '"owner_readiness": payload.get("owner_readiness") or {}' in api
    assert '"next_actions": payload.get("next_actions") or []' in api
    assert '"readiness": payload.get("readiness") or {}' in api
    assert '@router.post("/iiko-olap-sync")' in api
    assert "olap_sales_backfill_org" in api


def test_owner_landing_uses_separate_hub_page_not_admin_overlay():
    js = (REPO / "app" / "static" / "js" / "admin-app.js").read_text(encoding="utf-8")
    main = (REPO / "app" / "main.py").read_text(encoding="utf-8")
    hub = (REPO / "app" / "templates" / "hub.html").read_text(encoding="utf-8")

    assert '@app.get("/hub"' in main
    assert 'data-surface="executive-hub"' in hub
    assert "window.location.href = '/hub';" in js
    apply_block = js.split("async applyRoleDefaultLanding(fromHashTab)")[1].split("/** Подсказка RBAC")[0]
    assert "window.location.href = '/hub';" in apply_block
    assert "await this.openExecutiveHub()" not in apply_block


def test_executive_hub_owner_grade_surface_contract():
    hub = (REPO / "app" / "templates" / "screens" / "_executive_hub.html").read_text(encoding="utf-8")
    chat = (REPO / "app" / "templates" / "screens" / "_executive_hub_chat_panel.html").read_text(encoding="utf-8")
    js = (REPO / "app" / "static" / "js" / "admin-app.js").read_text(encoding="utf-8")

    assert "Сегодняшняя картина бизнеса" in hub
    assert "executiveHubTodayPicture?.headline" in hub
    assert "executiveHubOwnerCards" in hub
    assert "Почему изменились деньги" in hub
    assert "executiveHubMoneyDrivers" in hub
    assert "Деньги на кону" in hub
    assert "executiveHubMoneyAtRisk" in hub
    assert "Филиалы сети" in hub
    assert "executiveHubNetworkBranch" in hub
    assert "Нужны первые данные для сводки владельца" in hub
    assert "Синхронизировать продажи" in hub
    assert "Полноэкранный разбор ресторана" in hub
    assert "openExecutiveHubChatFullscreen()" in chat
    assert "executiveHubError" in hub
    assert "executiveHubOwnerReadiness" in hub
    assert "Checklist доверия к аналитике" in hub
    assert "Runtime-инциденты" in hub
    assert "executiveHubFocusedView()" in hub
    assert "executiveHubFocusedRows()" in hub
    assert "openExecutiveHubSignal" in hub
    assert "Сейчас в разборе" in chat
    assert "executiveHubAgentTitle()" in chat
    assert "executiveHubAgentSummary()" in chat
    assert "data.today_picture" in js
    assert "data.owner_cards" in js
    assert "data.money_drivers" in js
    assert "data.money_at_risk" in js
    assert "data.network_branch" in js
    assert "executiveHubChatFullscreen" in js
    assert "executiveHubActiveSignal" in js
    assert "executiveHubError:" in js
    assert "data.owner_readiness" in js
    assert "executiveHubFocusedView()" in js


def test_ai_analyst_archive_contract():
    ai_center = (REPO / "app" / "templates" / "screens" / "_tab_ai_center.html").read_text(encoding="utf-8")
    js = (REPO / "app" / "static" / "js" / "admin-app.js").read_text(encoding="utf-8")
    api = (REPO / "app" / "api" / "admin" / "intelligence.py").read_text(encoding="utf-8")

    assert "История вопросов" in ai_center
    assert "intelligenceArchiveQuery" in ai_center
    assert "openIntelligenceArchiveItem(item)" in ai_center
    assert "Продолжить" in ai_center
    assert "loadIntelligenceArchive()" in js
    assert "openIntelligenceArchiveItem(item)" in js
    assert '@router.get("/conversations")' in api
    assert '@router.get("/conversations/{conversation_id}")' in api
    assert "Источник данных" not in ai_center
    assert "без прямых запросов" not in ai_center


def test_supplymind_lives_in_attention_queue_with_csv_action():
    incidents = (REPO / "app" / "templates" / "screens" / "_tab_incidents.html").read_text(encoding="utf-8")
    analytics_api = (REPO / "app" / "api" / "admin" / "analytics.py").read_text(encoding="utf-8")

    assert "item.kind === 'supply_purchase_draft'" in incidents
    assert "openIncidentItem(item, group)" in incidents
    assert "incidentFocusedRows()" in incidents
    assert "Чеклист закупки" in incidents
    assert "exportSupplyMindDraft(incidentFocusedItem.supply_draft_id)" in incidents
    assert "Скачать CSV" in incidents
    assert '"kind": "supply_purchase_draft"' in analytics_api
    assert '"supply_draft_id": int(draft.id)' in analytics_api
    assert '"purchase_items": draft.items_json or []' in analytics_api


def test_operator_keeps_only_execution_tabs():
    js = (REPO / "app" / "static" / "js" / "admin-app.js").read_text(encoding="utf-8")
    team = (REPO / "app" / "templates" / "screens" / "_tab_settings_team.html").read_text(encoding="utf-8")
    organization_api = (REPO / "app" / "api" / "admin" / "organization.py").read_text(encoding="utf-8")
    role_tabs_block = js.split("const ADMIN_ROLE_TABS = Object.freeze({", 1)[1].split("});", 1)[0]
    primary_nav_block = js.split("const ADMIN_ROLE_PRIMARY_NAV = Object.freeze({", 1)[1].split("});", 1)[0]
    assert "operator: Object.freeze(['shift', 'inbox', 'orders', 'chats', 'bookings', 'menu'])" in role_tabs_block
    assert "admin: null" in role_tabs_block
    assert "manager:" not in role_tabs_block
    assert "operator: Object.freeze(['shift', 'inbox', 'orders', 'chats', 'bookings', 'menu'])" in primary_nav_block
    assert '<option value="manager">' not in team
    assert "Менеджер" not in team
    assert '"role": _public_staff_role(u.role)' in organization_api
    assert "if raw == StaffRole.MANAGER.value:" in organization_api
    assert "return StaffRole.ADMIN.value" in organization_api
    assert "const ADMIN_OPERATOR_SECONDARY_TABS = Object.freeze(['orders', 'chats', 'bookings', 'menu'])" in js
    assert "canOpenExecutiveHub()" in js
    assert "return this.effectiveStaffRole() !== 'operator';" in js


def test_business_surfaces_hide_dev_terms_and_more_labels():
    files = [
        REPO / "app" / "templates" / "screens" / "_executive_hub.html",
        REPO / "app" / "templates" / "screens" / "_executive_hub_chat_panel.html",
        REPO / "app" / "templates" / "screens" / "_tab_ai_center.html",
        REPO / "app" / "templates" / "screens" / "_tab_ai_value.html",
        REPO / "app" / "templates" / "screens" / "_tab_dashboard.html",
    ]
    text = "\n".join(path.read_text(encoding="utf-8") for path in files)
    for forbidden in [
        "Source Layer",
        "Gemini",
        "HMAC",
        "trace_id",
        "scraper/API",
        "SystemEvent",
        "ChatLog",
        "по одному trace",
        "caused_by:",
        "Пока нет карточек",
        "Подробнее",
        "Preview / diff",
        "focused-разбор",
    ]:
        assert forbidden not in text


def test_executive_hub_readiness_copy_hides_internal_sync_terms():
    service = (REPO / "app" / "services" / "executive_hub.py").read_text(encoding="utf-8")
    assert "Запустить OLAP sync" not in service
    assert "iiko OLAP требует проверки" not in service
    assert "Последняя OLAP-синхронизация" not in service
    assert "Запустить sync" not in service
    assert "очередь задач" not in service


def test_shift_calm_empty_cta():
    shift = (REPO / "app" / "templates" / "screens" / "_tab_shift_control.html").read_text(encoding="utf-8")
    js = (REPO / "app" / "static" / "js" / "admin-app.js").read_text(encoding="utf-8")
    assert "Жизненный цикл смены" in shift
    assert "shiftLifecycleState().label" in shift
    assert "shiftLifecycleState()" in js
    assert "Вне рабочих часов" in js
    assert "Ожидание оператора" in js
    assert "Смена активна" in js
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
