"""Strategy Engine: правила до LLM, без смены схемы intent."""

from app.db.models import MenuItem
from app.services.sales_strategy import (
    StrategyDecision,
    build_sales_strategy,
    format_strategy_for_prompt,
)


def _menu() -> list[MenuItem]:
    return [
        MenuItem(name="Плов", category="Горячее", price=2790.0, is_available=True, iiko_id="uuid-plov"),
        MenuItem(name="Капучино", category="Кофе", price=1190.0, is_available=True, iiko_id="uuid-cap"),
        MenuItem(name="Салат ачичук", category="Салаты", price=890.0, is_available=True, iiko_id="uuid-ach"),
    ]


def _menu_pairing_only() -> list[MenuItem]:
    """Без напитков в каталоге — срабатывает связка плов → ачичук."""
    return [
        MenuItem(name="Плов", category="Горячее", price=2790.0, is_available=True, iiko_id="uuid-plov"),
        MenuItem(name="Салат ачичук", category="Салаты", price=890.0, is_available=True, iiko_id="uuid-ach"),
    ]


def test_build_sales_strategy_close_after_two_trace_entries() -> None:
    meta = {
        "recommendation_trace": [
            {"offered": "X", "is_recommendation": True},
            {"offered": "Y", "is_recommendation": True},
        ],
    }
    d = build_sales_strategy(
        [{"name": "Плов", "quantity": 1, "iiko_id": "uuid-plov", "category": "Горячее"}],
        3000.0,
        meta,
        _menu(),
    )
    assert d.goal == "close_order"
    assert "НЕ предлагай" in d.restriction


def test_build_sales_strategy_pairing_plov_to_achichuk() -> None:
    d = build_sales_strategy(
        [{"name": "Плов", "quantity": 1, "iiko_id": "uuid-plov", "category": "Горячее"}],
        2790.0,
        {},
        _menu_pairing_only(),
    )
    assert d.goal == "upsell"
    assert "ачичук" in (d.target_name_hints[0] or "").lower()
    assert "uuid-ach" in d.target_iiko_ids


def test_build_sales_strategy_rejected_iiko_id_blocks_pairing() -> None:
    """Отказ по UUID не предлагает ту же позицию снова (связка плов → ачичук)."""
    meta = {"upsell_rejected_iiko_ids": ["uuid-ach"]}
    d = build_sales_strategy(
        [{"name": "Плов", "quantity": 1, "iiko_id": "uuid-plov", "category": "Горячее"}],
        2790.0,
        meta,
        _menu_pairing_only(),
    )
    assert d.goal == "close_order"
    assert "uuid-ach" not in d.target_iiko_ids


def test_format_strategy_for_prompt_non_empty() -> None:
    t = format_strategy_for_prompt(
        StrategyDecision(goal="upsell", target_name_hints=["Чай"], restriction="Один совет."),
    )
    assert "Стратегия продаж" in t
    assert "upsell" in t

