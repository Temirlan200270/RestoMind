"""
Синхронизация меню из iiko в локальную БД (PostgreSQL / SQLite).
Скачивает номенклатуру, сопоставляет по полю ``iiko_id`` (UUID из iiko), обновляет ``menu_items``.

Важно: первичный ключ в БД — автоинкремент ``MenuItem.id``; UUID продукта iiko хранится в ``MenuItem.iiko_id``.
"""

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import MenuItem
from app.integrations.iiko_client import IikoClient

logger = logging.getLogger(__name__)

# Типы номенклатуры iiko Cloud (часто используемые)
_IIKO_TYPES_DISH_GOOD = frozenset({"Dish", "Good"})


def extract_price_from_iiko_product(product: dict[str, Any]) -> float:
    """
    Извлекает цену из ответа ``/nomenclature`` (разные версии API: sizePrices, priceCategories).
    """
    # Вариант 1: sizePrices (актуально для многих выгрузок)
    size_prices = product.get("sizePrices") or []
    if size_prices and isinstance(size_prices[0], dict):
        price_obj = size_prices[0].get("price")
        if isinstance(price_obj, dict):
            v = price_obj.get("currentPrice")
            if v is not None:
                return float(v)
        if isinstance(price_obj, (int, float)):
            return float(price_obj)

    # Вариант 2: priceCategories (альтернативный формат iiko)
    price_cats = product.get("priceCategories") or []
    if price_cats and isinstance(price_cats[0], dict):
        v = price_cats[0].get("price")
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, dict) and v.get("currentPrice") is not None:
            return float(v["currentPrice"])

    raw = product.get("price")
    if isinstance(raw, (int, float)):
        return float(raw)

    return 0.0


def _include_product_by_type(product: dict[str, Any], only_dish_good: bool) -> bool:
    """Если only_dish_good — оставляем Dish/Good; если тип не указан, включаем (совместимость с API)."""
    if not only_dish_good:
        return True
    t = product.get("type")
    if t is None or t == "":
        return True
    return str(t) in _IIKO_TYPES_DISH_GOOD


async def sync_menu_from_iiko(
    db: AsyncSession,
    api_login: str,
    organization_id: str,
    *,
    only_dish_and_good: bool | None = None,
) -> dict[str, int]:
    """
    Полный цикл синхронизации: iiko API → таблица ``menu_items``.

    Сопоставление по **UUID iiko** в поле ``iiko_id`` (не по ``MenuItem.id``).

    Returns:
        Статистика: created, updated, skipped (опционально), total
    """
    filt = (
        settings.iiko_menu_sync_only_dish_good
        if only_dish_and_good is None
        else only_dish_and_good
    )

    async with IikoClient(api_login=api_login) as client:
        nomenclature = await client.fetch_menu(organization_id)

    groups: dict[str, str] = {}
    for group in nomenclature.get("groups", []):
        group_id = group.get("id", "")
        group_name = group.get("name", "Без категории")
        if group_id:
            groups[group_id] = group_name

    products: list[dict[str, Any]] = nomenclature.get("products", [])

    existing_result = await db.execute(select(MenuItem))
    existing_by_iiko: dict[str, MenuItem] = {
        mi.iiko_id: mi for mi in existing_result.scalars().all() if mi.iiko_id
    }

    created = 0
    updated = 0
    skipped = 0

    for product in products:
        iiko_id = product.get("id")
        if not iiko_id:
            continue

        if not _include_product_by_type(product, filt):
            skipped += 1
            continue

        name = (product.get("name") or "").strip()
        if not name:
            skipped += 1
            continue

        price = extract_price_from_iiko_product(product)
        category_id = product.get("parentGroup", "")
        category_name = groups.get(category_id, "")
        raw_desc = product.get("description")
        description = (raw_desc if isinstance(raw_desc, str) else str(raw_desc or "")) or ""
        image_links = product.get("imageLinks") or []
        image_url = image_links[0] if image_links else None

        existing = existing_by_iiko.get(iiko_id)

        if existing:
            existing.name = name
            existing.category = category_name
            existing.description = description
            existing.price = price
            existing.image_url = image_url
            existing.is_available = True
            updated += 1
        else:
            item = MenuItem(
                iiko_id=iiko_id,
                name=name,
                category=category_name,
                description=description,
                price=price,
                image_url=image_url,
                is_available=True,
            )
            db.add(item)
            existing_by_iiko[iiko_id] = item
            created += 1

    await db.flush()

    stats = {
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "total": created + updated,
    }
    logger.info(
        "Синхронизация меню завершена: создано %d, обновлено %d, пропущено %d, всего %d",
        created, updated, skipped, stats["total"],
    )
    return stats


async def sync_iiko_menu_to_db(db: AsyncSession) -> dict[str, int]:
    """
    Синхронизация меню с учётными данными из ``settings`` (.env).
    Удобно для админки и скриптов.
    """
    login = (settings.iiko_api_login or "").strip()
    org = (settings.iiko_organization_id or "").strip()
    if not login or not org:
        raise ValueError("Задайте IIKO_API_LOGIN и IIKO_ORGANIZATION_ID в .env")
    return await sync_menu_from_iiko(db, login, org)


async def sync_stop_lists(
    db: AsyncSession,
    api_login: str,
    organization_id: str,
) -> dict[str, int]:
    """
    Синхронизация стоп-листов из iiko.
    Ставит is_available=False для позиций, которых нет в наличии.

    Returns:
        Статистика: {"stopped": N, "restored": N}
    """
    async with IikoClient(api_login=api_login) as client:
        stop_data = await client.get_stop_lists([organization_id])

    stopped_ids: set[str] = set()
    terminal_groups = stop_data.get("terminalGroupStopLists", [])
    for tg in terminal_groups:
        for item_list in tg.get("items", []):
            for item in item_list.get("items", []):
                product_id = item.get("productId", "")
                if product_id:
                    stopped_ids.add(product_id)

    # Важно: при пустом ответе iiko нельзя делать ранний return — иначе позиции,
    # ранее помеченные стопом локально, никогда не вернутся в продажу.
    logger.info(
        "Стоп-листы iiko: организация=%s…, групп терминалов=%d, уникальных productId в стопе=%d",
        (organization_id or "")[:8],
        len(terminal_groups),
        len(stopped_ids),
    )

    result = await db.execute(select(MenuItem))
    all_items = result.scalars().all()

    stopped_count = 0
    restored_count = 0

    for item in all_items:
        if item.iiko_id in stopped_ids:
            if item.is_available:
                item.is_available = False
                stopped_count += 1
        else:
            if not item.is_available:
                item.is_available = True
                restored_count += 1

    await db.flush()

    logger.info(
        "Стоп-листы применены к БД: в стоп добавлено %d, восстановлено в продажу %d",
        stopped_count, restored_count,
    )
    return {"stopped": stopped_count, "restored": restored_count}
