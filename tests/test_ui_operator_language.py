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


def test_team_settings_operator_language():
    team = _read("app", "templates", "screens", "_tab_settings_team.html")
    assert "StaffMind onboarding" not in team
    assert "Должность (StaffMind)" not in team
    assert "Сессий StaffMind" not in team
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


def test_dashboard_sales_peak_opens_analytics_subtab():
    dash = _read("app", "templates", "screens", "_tab_dashboard.html")
    assert "Пик продаж сегодня" in dash
    assert "navigateToTab('dashboard', { dashboardTab: 'analytics' })" in dash
    assert "setAnalyticsDensity('advanced'); navigateToTab('dashboard')" not in dash


def test_operator_queue_no_dev_resolved_labels():
    html = _read("app", "templates", "screens", "_tab_operator_queue.html")
    assert "Неразрешённые" not in html
    assert "Разрешённые" not in html
    assert "В работе" in html
    assert "Закрытые" in html


def test_shift_control_no_raw_state_leak():
    html = _read("app", "templates", "screens", "_tab_shift_control.html")
    js = JS.read_text(encoding="utf-8")
    assert 'x-text="shiftState.state"' not in html
    assert 'x-text="shiftState.presentation.state_reason"' not in html
    assert "shiftStateLabel(" in html
    assert "shiftStateLabel(" in js
    assert "shiftStateReasonLabel(" in html
    assert "shiftStateReasonLabel(" in js
