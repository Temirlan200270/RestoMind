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


def test_header_shows_full_operational_status():
    header = _read("app", "templates", "screens", "_header.html")
    js = JS.read_text(encoding="utf-8")
    assert "headerOperationalText()" in header
    assert "headerOperationalEmoji()" in header
    assert "headerOperationalBadgeClass()" in js


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
