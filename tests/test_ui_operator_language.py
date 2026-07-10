"""Rule 8 (CONVENTIONS): admin UI speaks operator language, not dev jargon."""

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCREENS = REPO / "app" / "templates" / "screens"
COMPONENTS = REPO / "app" / "templates" / "components"
JS = REPO / "app" / "static" / "js" / "admin-app.js"


def _read(*parts: str) -> str:
    return (REPO.joinpath(*parts)).read_text(encoding="utf-8")


def test_dashboard_no_task_queue_block_for_owners():
    dash = _read("app", "templates", "screens", "_tab_dashboard.html")
    assert "Очередь задач" not in dash
    assert "Воркер:" not in dash


def test_dashboard_no_dev_jargon_in_revenue_leak():
    dash = _read("app", "templates", "screens", "_tab_dashboard.html")
    assert "DRAFT" not in dash
    assert "AOV" not in dash


def test_dashboard_operator_escalation_labels():
    dash = _read("app", "templates", "screens", "_tab_dashboard.html")
    assert "Передано оператору сегодня" in dash
    assert "Доля диалогов с оператором" in dash
    assert "эскалац" not in dash.lower()


def test_inbox_operator_language_no_action_queue():
    inbox = _read("app", "templates", "screens", "_tab_inbox.html")
    assert "Action Queue" not in inbox
    assert "Очередь помощи" in inbox


def test_header_shows_compact_operational_status_with_full_title():
    header = _read("app", "templates", "screens", "_header.html")
    js = JS.read_text(encoding="utf-8")
    assert "headerOperationalText()" in header
    assert "headerOperationalTitle()" in header
    assert "headerOperationalEmoji()" in header
    assert "headerOperationalBadgeClass()" in js
    assert "headerOperationalTitle()" in js
    assert "if (p.is_kitchen_open) return 'Открыто';" in js


def test_marketing_no_final_mile_label():
    marketing = _read("app", "templates", "screens", "_tab_marketing.html")
    assert "LOYALTY_ENABLED" not in marketing
    assert "База гостей из iiko" in marketing


def test_marketing_loyalty_no_env_var_names():
    marketing = _read("app", "templates", "screens", "_tab_marketing.html")
    assert "LOYALTY_ENABLED" not in marketing
    assert "LOYALTY_POINTS_PER_KZT" not in marketing
    assert "Программа настраивается администратором RestoMind" in marketing


def test_owner_screens_no_escalation_word():
    owner_files = [
        SCREENS / "_tab_analytics.html",
        SCREENS / "_tab_ai_center.html",
        SCREENS / "_tab_intelligence.html",
        COMPONENTS / "_chat_guest_context.html",
        SCREENS / "_modals.html",
        SCREENS / "_tab_settings_connections.html",
        SCREENS / "_tab_settings_restaurant.html",
    ]
    for path in owner_files:
        text = path.read_text(encoding="utf-8").lower()
        assert "эскалац" not in text, f"dev term in {path.name}"


def test_chat_diagnostic_id_is_action_not_visible_raw_trace():
    guest = (COMPONENTS / "_chat_guest_context.html").read_text(encoding="utf-8")
    js = JS.read_text(encoding="utf-8")
    assert "Скопировать ID диагностики" in guest
    assert "copyActiveChatDiagnosticId()" in guest
    assert 'x-text="activeChatTraceId"' not in guest
    assert ':title="activeChatTraceId"' not in guest
    assert "Control Plane" not in guest
    assert "Цепочка trace" not in guest
    assert "copyActiveChatDiagnosticId()" in js
    assert "navigator.clipboard.writeText(id)" in js
    assert "'Control Plane'" not in js


def test_admin_js_event_labels_operator_language():
    js = JS.read_text(encoding="utf-8")
    assert "'ai.escalated': 'Бот позвал оператора'" in js
    assert "'escalation_spike': 'Много запросов оператору'" in js
    assert "Эскалация" not in js


def test_final_mile_tab_operator_language():
    html = _read("app", "templates", "screens", "_tab_ai_center.html")
    js = JS.read_text(encoding="utf-8")
    assert "Daily OS Digest" not in html
    assert "Voice AI" not in html
    assert "Сводка дня" in html
    assert "Голосовой бот" in html
    assert "Журнал звонков" in html
    assert "Закупки" in html
    assert "voiceAiEnabledDraft ? 'вкл' : 'выкл'" in html
    assert "voiceCallModeLabel" in js


def test_ai_center_more_expands_tabs_without_clipped_dropdown():
    html = _read("app", "templates", "screens", "_tab_ai_center.html")
    js = JS.read_text(encoding="utf-8")
    assert "showAiCenterExtendedTabs()" in html
    assert "x-show=\"!aiCenterShowExtendedTabs\"" in html
    assert "<details x-show=\"!aiCenterShowExtendedTabs\"" not in html
    assert "showAiCenterExtendedTabs()" in js
    assert "aiCenterExtendedTabs" in js


def test_ai_center_uses_business_language_not_module_names():
    ai_center = _read("app", "templates", "screens", "_tab_ai_center.html")
    header = _read("app", "templates", "screens", "_header.html")
    dashboard = _read("app", "templates", "screens", "_tab_dashboard.html")
    settings_bot = _read("app", "templates", "screens", "_tab_settings_bot_test.html")
    intelligence = _read("app", "templates", "screens", "_tab_intelligence.html")
    smart_sales = _read("app", "templates", "screens", "_tab_settings_smart_sales.html")
    business_surface = "\n".join([ai_center, header, dashboard, settings_bot, intelligence, smart_sales])
    for term in ["Owner Intelligence", "Вклад ИИ", "Автопилот", "Финал", "Gemini"]:
        assert term not in business_surface
    for phrase in ["Основания и drilldown", "co-occurrence", "Топ связок (upsell)", "Пока нет принятых связок"]:
        assert phrase not in business_surface
    assert "Разборы владельца" in business_surface
    assert "Эффект ИИ" in business_surface
    assert "Решения" in business_surface
    assert "Закупки и голос" in business_surface
    assert "AI API через ваш бэкенд" in business_surface


def test_purchase_checklist_lives_in_attention_queue_contract():
    analytics_api = _read("app", "api", "admin", "analytics.py")
    assert 'group_id="purchase_checklist"' in analytics_api
    assert 'title="Закупка требует подтверждения"' in analytics_api
    assert '"tab": "ai_center"' in analytics_api
    assert '"aiCenterTab": "final_mile"' in analytics_api
    assert 'target["aiCenterTab"] = ac' in analytics_api


def test_team_settings_operator_language():
    team = _read("app", "templates", "screens", "_tab_settings_team.html")
    assert "StaffMind onboarding" not in team
    assert "Должность (StaffMind)" not in team
    assert "Сессий StaffMind" not in team
    assert '<option value="manager">' not in team
    assert "Менеджер" not in team
    assert "Администратор" not in team
    assert "Оператор" in team
    assert "Владелец" in team
    assert "Обучение сотрудников" in team
    assert "Сессий обучения пока нет" in team
    assert 'placeholder="cashier"' not in team


def test_analytics_no_dev_field_names_in_tooltips():
    analytics = _read("app", "templates", "screens", "_tab_analytics.html")
    assert "accepted_revenue_kzt" not in analytics
    assert "recommendation_trace" not in analytics
    assert "AI Profit" not in analytics
    assert "Выручка от ИИ" in analytics
    assert "принятые рекомендации" in analytics


def test_analytics_sales_heatmap_uses_business_language():
    analytics = _read("app", "templates", "screens", "_tab_analytics.html")
    assert "ETL" not in analytics
    assert "heatmap выручки" not in analytics
    assert "запустите worker" not in analytics
    assert "ночного sync" not in analytics
    assert "upsell ИИ" not in analytics
    assert "вклад upsell" not in analytics
    assert "допродажи ИИ" in analytics
    assert "Пока нет данных" not in analytics
    assert "Нужна синхронизация продаж" in analytics
    assert "Синхронизировать продажи" in analytics
    assert "Нет почасовых продаж из iiko" in analytics
    assert "Последнее обновление" in analytics


def test_dashboard_sales_peak_opens_analytics_subtab():
    dash = _read("app", "templates", "screens", "_tab_dashboard.html")
    js = JS.read_text(encoding="utf-8")
    assert "Пик продаж сегодня" in dash
    assert "openDashboardDrilldown('sales_peak')" in dash
    assert "this.dashboardDrilldownKey === 'sales_peak'" in js
    assert "return { tab: 'dashboard', dashboardTab: 'analytics' }" in js
    assert "setAnalyticsDensity('advanced'); navigateToTab('dashboard')" not in dash


def test_operator_queue_no_dev_resolved_labels():
    html = _read("app", "templates", "screens", "_tab_operator_queue.html")
    assert "Неразрешённые" not in html
    assert "Разрешённые" not in html
    assert "В работе" in html
    assert "Закрытые" in html


def test_settings_restaurant_human_integration_labels():
    html = _read("app", "templates", "screens", "_tab_settings_restaurant.html")
    assert "WhatsApp для гостей" in html
    assert "Чат команды в Telegram" in html
    assert "Для техспециалиста" in html
    assert "WhatsApp Phone ID" not in html.split("Для техспециалиста")[0]


def test_settings_connections_hide_background_job_jargon():
    html = _read("app", "templates", "screens", "_tab_settings_connections.html")
    for term in [
        "Очередь задач",
        "Воркер:",
        "MENU_RAG_ENABLED",
        "menu_items",
        "эмбеддинг",
        "БД",
        "store_id)",
        "department_id (",
        "Пароль API",
        "JSON (только для dev/staging)",
    ]:
        assert term not in html
    assert "Фоновые обновления" in html
    assert "Обновить меню и стоп-листы сейчас" in html
    assert "Для техспециалиста: поиск по меню" in html
    assert "История обновлений" in html


def test_settings_purge_modal_uses_business_names_not_table_names():
    html = _read("app", "templates", "screens", "_modals.html")
    purge_block = html.split('id="settings-purge-title"', 1)[1].split('x-show="settingsPurgeError"', 1)[0]
    assert "users" not in purge_block
    assert "menu_items" not in purge_block
    assert "Не удаляются:</strong> клиенты, меню и организации." in purge_block


def test_shift_control_no_raw_state_leak():
    html = _read("app", "templates", "screens", "_tab_shift_control.html")
    js = JS.read_text(encoding="utf-8")
    assert 'x-text="shiftState.state"' not in html
    assert 'x-text="shiftState.presentation.state_reason"' not in html
    assert "'Режим ' + (shiftState?.state" not in html
    assert "shiftStatusHeadline()" in html
    assert "shiftStateLabel(" in js
    assert "shiftStateReasonLabel(" in js


def test_orders_hint_no_kanban_jargon():
    html = _read("app", "templates", "screens", "_tab_orders.html")
    assert "канбан" not in html.lower()
    assert "По этапам" in html


def test_operations_density_toggle_not_exposed_in_header():
    header = _read("app", "templates", "screens", "_header.html")
    js = JS.read_text(encoding="utf-8")
    assert "canToggleOperationsDensity()" not in header
    assert "setOperationsDensity(" not in header
    assert "restomind_density:operations" in js
    assert "operationsCompactEnabled()" in js


def test_header_single_org_no_duplicate_name():
    header = _read("app", "templates", "screens", "_header.html")
    assert "available_organizations || []).length <= 1" not in header
    sidebar = _read("app", "templates", "screens", "_sidebar.html")
    assert "orgProfile?.name" in sidebar


def test_marketing_draft_helper_when_form_incomplete():
    marketing = _read("app", "templates", "screens", "_tab_marketing.html")
    assert "Укажите название и текст сообщения" in marketing
    assert "!form.name || !form.message_text" in marketing


def test_search_shortcut_onboarding_hint():
    header = _read("app", "templates", "screens", "_header.html")
    assert "searchShortcut" in header
    assert "dismissUiHint('searchShortcut')" in header
    assert "Ctrl K" in header


def test_bookings_sidebar_collapses_when_empty():
    bookings = _read("app", "templates", "screens", "_tab_bookings.html")
    assert "bookingsSidebarOpen()" in bookings
    assert "Справка по бронированию" in bookings


def test_shell_v2_focus_card_in_shift_tab():
    shift = _read("app", "templates", "screens", "_tab_shift_control.html")
    assert "focus_card()" in shift
    assert "focusCardView()" not in shift or "_focus_card.html" in shift


def test_inbox_operator_secondary_copy():
    inbox = _read("app", "templates", "screens", "_tab_inbox.html")
    assert "Расширенный список рисков" in inbox
    assert "openInboxShiftHero" in inbox
