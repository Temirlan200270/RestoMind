"""Разбор цены и фильтра типов из ответа номенклатуры iiko."""

from app.services.menu_sync import (
    _category_display_name,
    _dedupe_nomenclature_products,
    _include_product_by_type,
    _merge_group_maps_from_nomenclature,
    _product_category_uuid,
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
    assert _include_product_by_type({"type": "Product"}, True) is True
    assert _include_product_by_type({"type": "Modifier"}, True) is False
    assert _include_product_by_type({}, True) is True
    assert _include_product_by_type({"type": "Modifier"}, False) is True


def test_merge_nested_child_groups() -> None:
    nom = {
        "groups": [
            {
                "id": "root-id",
                "name": "Kitchen",
                "childGroups": [
                    {"id": "sub-id", "name": "Soups", "childGroups": []},
                ],
            }
        ]
    }
    m = _merge_group_maps_from_nomenclature(nom)
    assert m["root-id"] == "Kitchen"
    assert m["sub-id"] == "Soups"


def test_product_category_uuid_from_object_parent_group() -> None:
    assert _product_category_uuid({"parentGroup": {"id": "g-1"}}) == "g-1"


def test_category_display_name_from_parent_group_when_map_missing() -> None:
    """Если id группы нет в дереве groups, имя всё равно берётся из объекта parentGroup."""
    product = {"parentGroup": {"id": "orphan-uuid", "name": "Суши"}}
    assert _category_display_name(product, {}) == "Суши"


def test_category_display_name_prefers_groups_map_over_parent_name() -> None:
    product = {"parentGroup": {"id": "g-1", "name": "Суши"}}
    assert _category_display_name(product, {"g-1": "Из карты групп"}) == "Из карты групп"


def test_dedupe_nomenclature_products_last_wins_order() -> None:
    raw = [
        {"id": "a", "name": "First"},
        {"id": "a", "name": "Second"},
        {"id": "b", "name": "B"},
    ]
    unique, dup = _dedupe_nomenclature_products(raw)
    assert dup == 1
    assert len(unique) == 2
    assert unique[0]["name"] == "Second"
