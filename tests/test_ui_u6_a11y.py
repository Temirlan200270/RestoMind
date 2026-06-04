"""Регрессия Phase U6: разметка канбана и drawer для a11y / клавиатуры."""

from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def test_kanban_columns_have_data_attribute_and_handler():
    # После декомпозиции `admin.html` на `{% include %}` нужная разметка живёт в конкретном экране.
    # Проверяем именно таб заказов, где расположен канбан.
    orders_tab = (REPO / "app" / "templates" / "screens" / "_tab_orders.html").read_text(encoding="utf-8")
    assert orders_tab.count("data-kanban-col") == 7
    assert "handleKanbanKeydown" in orders_tab
    assert 'role="tab"' in orders_tab


def test_drawer_macro_has_heading_for_aria():
    drawer = (REPO / "app" / "templates" / "components" / "_drawer.html").read_text(encoding="utf-8")
    assert 'aria-labelledby="{{ id }}-title"' in drawer
    assert 'id="{{ id }}-title"' in drawer
    assert "<h2 " in drawer


def test_admin_css_includes_u6_safe_area_rules():
    css = (REPO / "app" / "static" / "css" / "admin.css").read_text(encoding="utf-8")
    assert "safe-area-inset-bottom" in css
