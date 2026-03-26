"""
Тесты валидации заказов (validate_order, build_menu_context).
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.ai_schemas import OrderItem
from app.services.order_logic import (
    ValidatedOrder,
    build_menu_context,
    build_summary_text_from_stored_items,
    detect_payment_method_from_text,
    load_available_menu,
    validate_order,
)


@pytest.mark.asyncio
async def test_validate_known_items(db_with_menu: AsyncSession) -> None:
    """Все позиции есть в меню — заказ валиден."""
    items = [
        OrderItem(name="Плов", quantity=2),
        OrderItem(name="Капучино", quantity=1),
    ]
    menu = await load_available_menu(db_with_menu)
    result = await validate_order(items, menu_items=menu)

    assert len(result.valid_items) == 2
    assert len(result.unknown_items) == 0
    assert result.total_price == 2790.0 * 2 + 1190.0


@pytest.mark.asyncio
async def test_validate_unknown_items(db_with_menu: AsyncSession) -> None:
    """Неизвестная позиция попадает в unknown_items."""
    items = [
        OrderItem(name="Плов", quantity=1),
        OrderItem(name="Единорог на гриле", quantity=1),
    ]
    menu = await load_available_menu(db_with_menu)
    result = await validate_order(items, menu_items=menu)

    assert len(result.valid_items) == 1
    assert "Единорог на гриле" in result.unknown_items


@pytest.mark.asyncio
async def test_validate_empty_items(db_with_menu: AsyncSession) -> None:
    """Пустой список — нет ошибки, нет позиций."""
    menu = await load_available_menu(db_with_menu)
    result = await validate_order([], menu_items=menu)

    assert result.valid_items == []
    assert result.total_price == 0.0


@pytest.mark.asyncio
async def test_validate_fuzzy_match(db_with_menu: AsyncSession) -> None:
    """Fuzzy matching: 'капучинно' → 'Капучино'."""
    items = [OrderItem(name="капучинно", quantity=1)]
    menu = await load_available_menu(db_with_menu)
    result = await validate_order(items, menu_items=menu)

    assert len(result.valid_items) == 1
    assert result.total_price == 1190.0


@pytest.mark.asyncio
async def test_validate_iiko_id_preserved(db_with_menu: AsyncSession) -> None:
    """iiko_id из меню сохраняется в validated items."""
    items = [OrderItem(name="Лагман", quantity=1)]
    menu = await load_available_menu(db_with_menu)
    result = await validate_order(items, menu_items=menu)

    assert result.valid_items[0]["iiko_id"] == "uuid-lagman"


@pytest.mark.asyncio
async def test_validate_exclude_ingredients(db_with_menu: AsyncSession) -> None:
    """exclude_ingredients сохраняются в валидированном заказе."""
    items = [OrderItem(name="Плов", quantity=1, exclude_ingredients=["лук", "морковь"])]
    menu = await load_available_menu(db_with_menu)
    result = await validate_order(items, menu_items=menu)

    assert result.valid_items[0]["exclude_ingredients"] == ["лук", "морковь"]
    assert "без лук" in result.summary_text


@pytest.mark.asyncio
async def test_validate_all_unknown_returns_empty(db_with_menu: AsyncSession) -> None:
    """Все позиции неизвестны → valid_items пуст."""
    items = [
        OrderItem(name="Жареный вулкан", quantity=1),
        OrderItem(name="Суп из радуги", quantity=2),
    ]
    menu = await load_available_menu(db_with_menu)
    result = await validate_order(items, menu_items=menu)

    assert result.valid_items == []
    assert len(result.unknown_items) == 2
    assert result.total_price == 0.0


@pytest.mark.asyncio
async def test_load_available_excludes_unavailable(db_with_menu: AsyncSession) -> None:
    """load_available_menu не возвращает позиции с is_available=False."""
    menu = await load_available_menu(db_with_menu)
    names = [m.name for m in menu]
    assert "Маргарита" not in names
    assert "Плов" in names


@pytest.mark.asyncio
async def test_build_menu_context(db_with_menu: AsyncSession) -> None:
    """build_menu_context формирует текст с ценами и iiko_id."""
    menu = await load_available_menu(db_with_menu)
    context = build_menu_context(menu)

    assert "Плов" in context
    assert "2790" in context
    assert "[id: uuid-plov]" in context
    assert "Маргарита" not in context


@pytest.mark.asyncio
async def test_validate_with_mock_menu_fallback() -> None:
    """Если menu_items пуст — fallback на MOCK_MENU."""
    items = [OrderItem(name="Плов", quantity=1)]
    result = await validate_order(items, menu_items=[])

    assert len(result.valid_items) == 1
    assert result.total_price == 2790.0


def test_detect_payment_cash_card_remote() -> None:
    assert detect_payment_method_from_text("наличными") == "cash"
    assert detect_payment_method_from_text("Картой привезите терминал") == "card"
    assert detect_payment_method_from_text("каспи переведу") == "remote"
    assert detect_payment_method_from_text("онлайн ссылкой") == "remote"
    assert detect_payment_method_from_text("хммм") is None


def test_detect_payment_multilingual() -> None:
    assert detect_payment_method_from_text("Картамен төлеу") == "card"
    assert detect_payment_method_from_text("By cash please") == "cash"
    assert detect_payment_method_from_text("naqd pul") == "cash"
    assert detect_payment_method_from_text("Payme or Kaspi") == "remote"
    assert detect_payment_method_from_text("payment link") == "remote"


def test_detect_payment_no_false_positive_potato() -> None:
    """«Карт» в составе «картошка» не считаем картой."""
    assert detect_payment_method_from_text("Добавьте картошку фри") is None


def test_build_summary_from_stored_items() -> None:
    j: dict = {
        "items": [
            {"name": "Плов", "quantity": 2, "item_total": 5580.0},
        ],
    }
    s = build_summary_text_from_stored_items(j)
    assert "Плов" in s
    assert "5580" in s
