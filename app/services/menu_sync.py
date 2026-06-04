"""
Синхронизация меню из iiko в локальную БД (PostgreSQL / SQLite).
Скачивает номенклатуру, сопоставляет по полю ``iiko_id`` (UUID из iiko), обновляет ``menu_items``.

Важно: первичный ключ в БД — автоинкремент ``MenuItem.id``; UUID продукта iiko хранится в ``MenuItem.iiko_id``.
"""

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import MenuItem, Organization
from app.integrations.iiko_client import IikoClient

logger = logging.getLogger(__name__)

# Типы номенклатуры iiko Cloud (часто используемые)
_IIKO_TYPES_DISH_GOOD = frozenset({"Dish", "Good", "Product"})


def _iiko_uuid_ref(raw: Any) -> str:
    """UUID из строки или из объекта-ссылки ``{ \"id\": \"...\" }`` (вариант ответа /nomenclature)."""
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw.strip()
    if isinstance(raw, dict):
        for kk in ("id", "Id", "ID"):
            v = raw.get(kk)
            if v is not None and str(v).strip():
                return str(v).strip()
    return ""


def _merge_group_maps_from_nomenclature(nomenclature: dict[str, Any]) -> dict[str, str]:
    """
    id группы → отображаемое имя. Учитывает вложенные ``childGroups`` (верхний уровень ``groups``
    без рекурсии часто даёт только корень — тогда позиции в подгруппах теряют категорию).
    Также подмешивает ``productCategories`` / ``categories``, если они есть в ответе.
    """

    merged: dict[str, str] = {}

    def take(node: dict[str, Any]) -> None:
        gid = str(node.get("id") or "").strip()
        gnm = str(node.get("name") or "").strip() or "Без категории"
        if gid:
            merged[gid] = gnm

    def walk(node: dict[str, Any]) -> None:
        take(node)
        for key in ("childGroups", "childgroups"):
            kids = node.get(key)
            if not isinstance(kids, list):
                continue
            for ch in kids:
                if isinstance(ch, dict):
                    walk(ch)

    for g in nomenclature.get("groups") or []:
        if isinstance(g, dict):
            walk(g)

    for alt_key in ("productCategories", "categories"):
        for c in nomenclature.get(alt_key) or []:
            if isinstance(c, dict):
                take(c)

    return merged


def _product_category_uuid(product: dict[str, Any]) -> str:
    """Идентификатор родительской группы/категории продукта (несколько полей для совместимости API)."""
    for key in ("parentGroup", "ParentGroup"):
        if key in product:
            u = _iiko_uuid_ref(product[key])
            if u:
                return u
    for key in ("groupId", "productCategoryId"):
        u = _iiko_uuid_ref(product.get(key))
        if u:
            return u
    return ""


def _category_display_name(product: dict[str, Any], groups_map: dict[str, str]) -> str:
    """
    Человекочитаемое имя раздела для UI (вкладка «Меню» группирует по ``MenuItem.category``).

    iiko может отдавать только UUID группы без попадания id в дерево ``groups``,
    но при этом вложить название прямо в объект ``parentGroup`` (поля ``id`` и ``name``).
    """
    cid = _product_category_uuid(product)
    if cid:
        g = (groups_map.get(cid) or "").strip()
        if g:
            return g
    for key in ("parentGroup", "ParentGroup"):
        pg = product.get(key)
        if isinstance(pg, dict):
            nm = pg.get("name")
            if nm and str(nm).strip():
                return str(nm).strip()
    for alt in ("groupName", "productCategoryName", "categoryName"):
        v = product.get(alt)
        if v and str(v).strip():
            return str(v).strip()
    return ""


def _dedupe_nomenclature_products(raw: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """
    На один ``id`` может приходиться несколько записей в ``products``.
    Последний по порядку в массиве выигрывает — иначе завышается счётчик «обновлено» при том же числе строк в БД.
    """
    by_id: dict[str, dict[str, Any]] = {}
    ordered_ids: list[str] = []
    for p in raw:
        sid = str(p.get("id") or "").strip()
        if not sid:
            continue
        if sid not in by_id:
            ordered_ids.append(sid)
        by_id[sid] = p
    unique_list = [by_id[i] for i in ordered_ids]
    rows_with_id = sum(1 for p in raw if str(p.get("id") or "").strip())
    merged_dup = max(0, rows_with_id - len(unique_list))
    return unique_list, merged_dup


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
    """
    Если only_dish_good — оставляем Dish/Good/Product; если тип не указан, включаем (совместимость с API).
    ``Product`` встречается в части аккаунтов iiko как продаваемая позиция.
    """
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
    restomind_organization_id: int | None = None,
    prune_missing: bool = False,
    prune_mode: str = "archive",
    prune_legacy: bool = False,
) -> dict[str, Any]:
    """
    Полный цикл синхронизации: iiko API → таблица ``menu_items``.

    Сопоставление по **UUID iiko** в поле ``iiko_id`` (не по ``MenuItem.id``).

    Returns:
        Статистика: created, updated, skipped*, archived/deleted, total …
        * поля skip_no_id / skip_deleted / skip_type / skip_no_name — почему часть строк API не попала в БД.
    """
    filt = (
        settings.iiko_menu_sync_only_dish_good
        if only_dish_and_good is None
        else only_dish_and_good
    )

    async with IikoClient(api_login=api_login) as client:
        nomenclature = await client.fetch_menu(organization_id)

    groups_map = _merge_group_maps_from_nomenclature(nomenclature)
    raw_products: list[dict[str, Any]] = [
        p for p in (nomenclature.get("products") or []) if isinstance(p, dict)
    ]
    products, api_products_dup_merged = _dedupe_nomenclature_products(raw_products)
    sync_seen_at = datetime.now(timezone.utc)

    if restomind_organization_id is None:
        org_ids = (
            await db.execute(
                select(Organization.id).where(
                    Organization.iiko_organization_id == organization_id,
                    Organization.is_active.is_(True),
                ),
            )
        ).scalars().all()
        if len(org_ids) != 1:
            raise ValueError(
                "restomind_organization_id is required when iiko organization cannot be "
                "resolved to exactly one active Organization",
            )
        restomind_organization_id = int(org_ids[0])

    q = select(MenuItem)
    q = q.where(
        or_(
            MenuItem.organization_id == restomind_organization_id,
            MenuItem.organization_id.is_(None),
        )
    )
    existing_result = await db.execute(q)
    existing_by_iiko: dict[str, MenuItem] = {}
    for mi in existing_result.scalars().all():
        if mi.iiko_id:
            existing_by_iiko[str(mi.iiko_id).strip()] = mi

    created = 0
    updated = 0
    archived = 0
    deleted = 0
    skip_no_id = 0
    skip_deleted = 0
    skip_type = 0
    skip_no_name = 0
    active_iiko_ids: set[str] = set()

    for product in products:
        sid_raw = product.get("id")
        sid = str(sid_raw).strip() if sid_raw is not None else ""
        if not sid:
            skip_no_id += 1
            continue

        if bool(product.get("isDeleted")):
            skip_deleted += 1
            continue

        if not _include_product_by_type(product, filt):
            skip_type += 1
            continue

        name = (product.get("name") or "").strip()
        if not name:
            skip_no_name += 1
            continue

        price = extract_price_from_iiko_product(product)
        category_name = _category_display_name(product, groups_map)
        active_iiko_ids.add(sid)

        raw_desc = product.get("description")
        description = (raw_desc if isinstance(raw_desc, str) else str(raw_desc or "")) or ""
        image_links = product.get("imageLinks") or []
        image_url = image_links[0] if image_links else None

        existing = existing_by_iiko.get(sid)

        if existing:
            if restomind_organization_id is not None and existing.organization_id is None:
                existing.organization_id = restomind_organization_id
            existing.name = name
            existing.category = category_name
            existing.description = description
            existing.price = price
            existing.image_url = image_url
            existing.iiko_id = sid
            existing.source = "iiko"
            existing.last_seen_iiko_sync_at = sync_seen_at
            existing.is_archived = False
            existing.archived_at = None
            updated += 1
        else:
            item = MenuItem(
                organization_id=restomind_organization_id,
                iiko_id=sid,
                name=name,
                category=category_name,
                description=description,
                price=price,
                image_url=image_url,
                is_available=True,
                source="iiko",
                last_seen_iiko_sync_at=sync_seen_at,
                is_archived=False,
            )
            db.add(item)
            existing_by_iiko[sid] = item
            created += 1

    prune_mode_norm = (prune_mode or "archive").strip().lower()
    if prune_mode_norm not in {"archive", "delete"}:
        prune_mode_norm = "archive"
    if prune_missing and restomind_organization_id is not None:
        candidates = []
        for item in existing_by_iiko.values():
            item_org = getattr(item, "organization_id", None)
            if item_org is not None and int(item_org) != int(restomind_organization_id):
                continue
            sid = (item.iiko_id or "").strip()
            if not sid or sid in active_iiko_ids:
                continue
            source = (getattr(item, "source", None) or "legacy").strip().lower()
            if source == "iiko" or (prune_legacy and source == "legacy"):
                candidates.append(item)

        if prune_mode_norm == "delete":
            stale_ids = [int(item.id) for item in candidates if getattr(item, "id", None) is not None]
            if stale_ids:
                await db.execute(delete(MenuItem).where(MenuItem.id.in_(stale_ids)))
                deleted = len(stale_ids)
        else:
            for item in candidates:
                if not bool(getattr(item, "is_archived", False)):
                    archived += 1
                item.is_archived = True
                item.archived_at = sync_seen_at
                item.is_available = False

    await db.flush()

    skipped_total = skip_no_id + skip_deleted + skip_type + skip_no_name
    stats = {
        "created": created,
        "updated": updated,
        "archived": archived,
        "deleted": deleted,
        "skipped": skipped_total,
        "skip_no_id": skip_no_id,
        "skip_deleted": skip_deleted,
        "skip_type": skip_type,
        "skip_no_name": skip_no_name,
        "total": created + updated,
        "api_products_raw": len(raw_products),
        "api_products": len(products),
        "api_products_dup_merged": api_products_dup_merged,
        "api_group_nodes": len(groups_map),
        "filter_only_dish_good": bool(filt),
        "prune_missing": bool(prune_missing),
        "prune_mode": prune_mode_norm,
        "prune_legacy": bool(prune_legacy),
    }
    logger.info(
        "Синхронизация меню: создано %d, обновлено %d, пропущено всего %d "
        "(no_id=%d deleted=%d type=%d noname=%d), API products raw=%d unique=%d dup_merged=%d, групп=%d",
        created,
        updated,
        skipped_total,
        skip_no_id,
        skip_deleted,
        skip_type,
        skip_no_name,
        len(raw_products),
        len(products),
        api_products_dup_merged,
        len(groups_map),
    )
    if filt and skip_type > 0:
        logger.warning(
            "%d позиций отфильтровано по type (режим только Dish/Good/Product). "
            "Если это заказываемые блюда — отключите IIKO_MENU_SYNC_ONLY_DISH_GOOD или проверьте тип номенклатуры в iiko.",
            skip_type,
        )
    return stats


async def sync_iiko_menu_to_db(db: AsyncSession) -> dict[str, Any]:
    """
    Синхронизация меню с учётными данными из ``settings`` (.env).
    Удобно для админки и скриптов.
    """
    login = (settings.iiko_api_login or "").strip()
    org = (settings.iiko_organization_id or "").strip()
    if not login or not org:
        raise ValueError("Задайте IIKO_API_LOGIN и IIKO_ORGANIZATION_ID в .env")
    return await sync_menu_from_iiko(db, login, org)


def _collect_stopped_product_ids_from_iiko_response(
    stop_data: dict[str, Any],
    terminal_group_id: str,
) -> tuple[set[str], int, int]:
    """
    Собирает productId из ответа POST /api/1/stop_lists.

    Структура iiko: terminalGroupStopLists[] → { organizationId, items: [
      { terminalGroupId, items: [ { productId, balance } ] }
    ] }.

    Если ``terminal_group_id`` непустой — учитывается только эта терминальная группа
    (одна точка вместо объединения стопов всех заведений сети).
    """
    want_filter = bool((terminal_group_id or "").strip())
    want = (terminal_group_id or "").strip().lower()

    stopped_ids: set[str] = set()
    blocks_total = 0
    blocks_used = 0

    for org_wrapper in stop_data.get("terminalGroupStopLists") or []:
        for block in org_wrapper.get("items") or []:
            if not isinstance(block, dict):
                continue
            blocks_total += 1
            bid = (block.get("terminalGroupId") or "").strip().lower()
            if want_filter and bid != want:
                continue
            blocks_used += 1
            for row in block.get("items") or []:
                if not isinstance(row, dict):
                    continue
                product_id = row.get("productId") or ""
                if product_id:
                    stopped_ids.add(product_id)

    return stopped_ids, blocks_total, blocks_used


async def sync_stop_lists(
    db: AsyncSession,
    api_login: str,
    organization_id: str,
    *,
    terminal_group_id: str | None = None,
    menu_organization_id: int | None = None,
) -> dict[str, int]:
    """
    Синхронизация стоп-листов из iiko.
    Ставит is_available=False для позиций, которых нет в наличии.

    Если в .env задан ``IIKO_TERMINAL_GROUP_ID`` (или передан ``terminal_group_id``),
    стоп применяется **только** к этой терминальной группе; иначе — объединение
    стопов по всем группам организации (старое поведение).

    Returns:
        stopped, restored, terminal_groups_total, terminal_groups_used (счётчики блоков в ответе API)
    """
    tg = (
        (terminal_group_id if terminal_group_id is not None else settings.iiko_terminal_group_id)
        or ""
    ).strip()

    async with IikoClient(api_login=api_login) as client:
        stop_data = await client.get_stop_lists([organization_id])

    stopped_ids, blocks_total, blocks_used = _collect_stopped_product_ids_from_iiko_response(
        stop_data, tg,
    )

    if tg and blocks_used == 0 and blocks_total > 0:
        logger.warning(
            "Стоп-листы: IIKO_TERMINAL_GROUP_ID=%s… не совпал ни с одной из %d терминальных групп в ответе — стоп пуст, все локальные позиции будут в продаже. Проверьте UUID в .env (скрипт scripts/list_iiko_terminal_groups.py).",
            tg[:8],
            blocks_total,
        )

    # Важно: при пустом ответе iiko нельзя делать ранний return — иначе позиции,
    # ранее помеченные стопом локально, никогда не вернутся в продажу.
    logger.info(
        "Стоп-листы iiko: организация=%s…, блоков терм.групп=%d, учтено блоков=%s, "
        "фильтр по группе=%s, уникальных productId в стопе=%d",
        (organization_id or "")[:8],
        blocks_total,
        str(blocks_used) if tg else "все",
        (tg[:8] + "…") if tg else "нет",
        len(stopped_ids),
    )

    qm = select(MenuItem)
    if menu_organization_id is not None:
        qm = qm.where(
            or_(
                MenuItem.organization_id == menu_organization_id,
                MenuItem.organization_id.is_(None),
            ),
            MenuItem.is_archived.is_(False),
        )
    else:
        qm = qm.where(MenuItem.is_archived.is_(False))
    result = await db.execute(qm)
    all_items = result.scalars().all()

    stopped_count = 0
    restored_count = 0

    for item in all_items:
        if menu_organization_id is not None and item.organization_id is None:
            item.organization_id = menu_organization_id

        next_available = (item.iiko_id or "") not in stopped_ids
        if item.is_available != next_available:
            item.is_available = next_available
            if next_available:
                restored_count += 1
            else:
                stopped_count += 1

    await db.flush()

    logger.info(
        "Стоп-листы применены к БД: в стоп добавлено %d, восстановлено в продажу %d",
        stopped_count, restored_count,
    )
    logger.info(
        "Стоп-листы: org=%s items_scanned=%d stopped_ids=%d",
        str(menu_organization_id) if menu_organization_id is not None else "None",
        len(all_items),
        len(stopped_ids),
    )
    return {
        "stopped": stopped_count,
        "restored": restored_count,
        "terminal_groups_total": blocks_total,
        "terminal_groups_used": blocks_used,
    }
