"""Разбор цены и фильтра типов из ответа номенклатуры iiko."""

from app.services.menu_sync import (
    _include_product_by_type,
    extract_price_from_iiko_product,
)


def test_price_size_prices_current() -> None:
    p = {
        "sizePrices": [{"price": {"currentPrice": 1990.0}}],
    }
    assert extract_price_from_iiko_product(p) == 1990.0


def test_price_price_categories_number() -> None:
    p = {"priceCategories": [{"price": 700}]}
    assert extract_price_from_iiko_product(p) == 700.0


def test_include_dish_good_filter() -> None:
    assert _include_product_by_type({"type": "Dish"}, True) is True
    assert _include_product_by_type({"type": "Good"}, True) is True
    assert _include_product_by_type({"type": "Modifier"}, True) is False
    assert _include_product_by_type({}, True) is True
    assert _include_product_by_type({"type": "Modifier"}, False) is True
