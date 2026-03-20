"""
Бизнес-логика заказов.
Валидация позиций по таблице MenuItem в БД.
MOCK_MENU используется как fallback, если таблица пуста.
Нечёткий поиск (fuzzy matching) через difflib.
"""

import logging
from dataclasses import dataclass
from difflib import get_close_matches

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.plovxana_menu import build_mock_menu_dict
from app.db.models import MenuItem
from app.schemas.ai_schemas import OrderItem

logger = logging.getLogger(__name__)

# Fallback, если таблица menu_items пуста (меню ПловXана из app/data/plovxana_menu.py)
MOCK_MENU: dict[str, float] = build_mock_menu_dict()
# Короткие синонимы для распознавания речи / тестов
MOCK_MENU.setdefault("плов", MOCK_MENU.get("плов праздничный баранина", 2790.0))
MOCK_MENU.setdefault("лагман", MOCK_MENU.get("лагман от шеф-повара", 2790.0))


@dataclass
class ValidatedOrder:
    """Результат валидации заказа."""

    valid_items: list[dict]
    unknown_items: list[str]
    total_price: float
    summary_text: str


@dataclass
class MenuEntry:
    """Запись справочника меню: цена + iiko UUID."""

    price: float
    iiko_id: str | None


async def load_available_menu(db: AsyncSession) -> list[MenuItem]:
    """Один запрос на весь цикл обработки — загрузка доступных позиций."""
    result = await db.execute(
        select(MenuItem)
        .where(MenuItem.is_available.is_(True))
        .order_by(MenuItem.category, MenuItem.name)
    )
    return list(result.scalars().all())


def _build_menu_lookup(
    db_items: list[MenuItem],
) -> dict[str, MenuEntry]:
    """Строит lookup из уже загруженных MenuItem. Fallback на MOCK_MENU."""
    if not db_items:
        logger.warning("Таблица menu_items пуста — используется MOCK_MENU")
        return {
            name: MenuEntry(price=price, iiko_id=None)
            for name, price in MOCK_MENU.items()
        }

    lookup: dict[str, MenuEntry] = {}
    for mi in db_items:
        lookup[mi.name.lower().strip()] = MenuEntry(
            price=float(mi.price), iiko_id=mi.iiko_id,
        )
    return lookup


async def validate_order(
    items: list[OrderItem],
    menu_items: list[MenuItem] | None = None,
    db: AsyncSession | None = None,
) -> ValidatedOrder:
    """
    Проверяет каждую позицию заказа по таблице MenuItem в БД.
    Принимает готовый список menu_items (чтобы не дублировать запрос)
    или db-сессию для ленивой загрузки. Fallback на MOCK_MENU.
    """
    if menu_items is None and db is not None:
        menu_items = await load_available_menu(db)
    menu_lookup = _build_menu_lookup(menu_items or [])
    menu_names = list(menu_lookup.keys())

    valid_items: list[dict] = []
    unknown_items: list[str] = []
    fuzzy_matched: list[str] = []
    total_price = 0.0

    for item in items:
        name_lower = item.name.lower().strip()
        entry = menu_lookup.get(name_lower)

        # Нечёткий поиск, если точного совпадения нет
        if entry is None and menu_names:
            matches = get_close_matches(name_lower, menu_names, n=1, cutoff=0.6)
            if matches:
                matched_name = matches[0]
                entry = menu_lookup[matched_name]
                fuzzy_matched.append(f"{item.name} → {matched_name}")
                logger.info("Fuzzy match: '%s' → '%s'", name_lower, matched_name)

        if entry is not None:
            item_total = entry.price * item.quantity
            total_price += item_total
            valid_items.append({
                "name": item.name,
                "quantity": item.quantity,
                "price_per_unit": entry.price,
                "item_total": item_total,
                "iiko_id": entry.iiko_id,
                "exclude_ingredients": item.exclude_ingredients,
            })
        else:
            unknown_items.append(item.name)

    lines = []
    for vi in valid_items:
        exclude_str = ""
        if vi["exclude_ingredients"]:
            exclude_str = f" (без {', '.join(vi['exclude_ingredients'])})"
        lines.append(
            f"  • {vi['name']} × {vi['quantity']} — {vi['item_total']:.0f} ₸{exclude_str}"
        )

    summary = "\n".join(lines)
    if fuzzy_matched:
        summary += "\n\n🔍 Уточнено автоматически: " + "; ".join(fuzzy_matched)
    if unknown_items:
        summary += f"\n\n⚠️ Не нашёл в меню: {', '.join(unknown_items)}"
    summary += f"\n\n💰 Итого: {total_price:.0f} ₸"

    logger.info(
        "Валидация заказа: %d найдено, %d не найдено, итого %.2f ₸",
        len(valid_items), len(unknown_items), total_price,
    )

    return ValidatedOrder(
        valid_items=valid_items,
        unknown_items=unknown_items,
        total_price=total_price,
        summary_text=summary,
    )


def build_menu_context(db_items: list[MenuItem]) -> str:
    """
    Формирует текстовое описание меню для System Prompt.
    AI использует его, чтобы знать реальные блюда и цены.
    Принимает уже загруженный список MenuItem.
    """
    if not db_items:
        lines = [f"- {name}: {price:.0f} ₸" for name, price in MOCK_MENU.items()]
        return "\n".join(lines)

    current_category = ""
    lines: list[str] = []
    for item in db_items:
        if item.category != current_category:
            current_category = item.category
            lines.append(f"\n## {current_category}")
        iiko_tag = f" [id: {item.iiko_id}]" if item.iiko_id else ""
        lines.append(f"- {item.name}: {float(item.price):.0f} ₸{iiko_tag}")

    return "\n".join(lines)
