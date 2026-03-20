"""
Синхронизация меню из iiko в PostgreSQL.
Скачивает номенклатуру, сопоставляет с таблицей menu_items и обновляет/добавляет позиции.
"""

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MenuItem
from app.integrations.iiko_client import IikoClient

logger = logging.getLogger(__name__)


async def sync_menu_from_iiko(
    db: AsyncSession,
    api_login: str,
    organization_id: str,
) -> dict[str, int]:
    """
    Полный цикл синхронизации: iiko API → PostgreSQL.

    Returns:
        Статистика: {"created": N, "updated": N, "total": N}
    """
    async with IikoClient(api_login=api_login) as client:
        nomenclature = await client.get_nomenclature(organization_id)

    # Построить словарь категорий (id → name) для удобного маппинга
    groups: dict[str, str] = {}
    for group in nomenclature.get("groups", []):
        group_id = group.get("id", "")
        group_name = group.get("name", "Без категории")
        if group_id:
            groups[group_id] = group_name

    products: list[dict[str, Any]] = nomenclature.get("products", [])

    # Загружаем все существующие позиции одним запросом (вместо N запросов в цикле)
    existing_result = await db.execute(select(MenuItem))
    existing_by_iiko: dict[str, MenuItem] = {
        mi.iiko_id: mi for mi in existing_result.scalars().all() if mi.iiko_id
    }

    created = 0
    updated = 0

    for product in products:
        iiko_id = product.get("id")
        if not iiko_id:
            continue

        name = product.get("name", "").strip()
        if not name:
            continue

        price = 0.0
        size_prices = product.get("sizePrices", [])
        if size_prices:
            price_info = size_prices[0].get("price", {})
            price = float(price_info.get("currentPrice", 0))

        category_id = product.get("parentGroup", "")
        category_name = groups.get(category_id, "")
        description = product.get("description") or ""
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
            created += 1

    await db.flush()

    stats = {"created": created, "updated": updated, "total": created + updated}
    logger.info(
        "Синхронизация меню завершена: создано %d, обновлено %d, всего %d",
        created, updated, stats["total"],
    )
    return stats


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

    # Собираем UUID продуктов в стоп-листе
    stopped_ids: set[str] = set()
    terminal_groups = stop_data.get("terminalGroupStopLists", [])
    for tg in terminal_groups:
        for item_list in tg.get("items", []):
            for item in item_list.get("items", []):
                product_id = item.get("productId", "")
                if product_id:
                    stopped_ids.add(product_id)

    if not stopped_ids:
        logger.info("Стоп-лист пуст — все позиции доступны")
        return {"stopped": 0, "restored": 0}

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
        "Стоп-листы: остановлено %d, восстановлено %d",
        stopped_count, restored_count,
    )
    return {"stopped": stopped_count, "restored": restored_count}
