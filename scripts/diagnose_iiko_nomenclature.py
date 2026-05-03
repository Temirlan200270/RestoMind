#!/usr/bin/env python3
"""
Диагностика ответа POST /api/1/nomenclature (то, что реально идёт в sync меню).
Сырые запросы (токен + nomenclature) без Python-хелперов — ``scripts/post_nomenclature.ps1`` или ``scripts/post_nomenclature.sh``.

Скрипт list_iiko_organizations.py проверяет только /organizations — меню может быть пустым в ответе iiko.

Запуск из корня проекта:
    python scripts/diagnose_iiko_nomenclature.py

Смотрите: сколько категорий/продуктов вернул API и распределение type (если включён IIKO_MENU_SYNC_ONLY_DISH_GOOD).
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from app.integrations.iiko_client import IikoClient  # noqa: E402
from app.services.menu_sync import _include_product_by_type, _merge_group_maps_from_nomenclature  # noqa: E402


async def _run(org_id: str, api_login: str, only_dish_good: bool) -> None:
    async with IikoClient(api_login=api_login) as client:
        data = await client.fetch_menu(org_id)

    groups = data.get("groups") or []
    products = data.get("products") or []
    flat_groups = _merge_group_maps_from_nomenclature(data)

    print("--- POST /api/1/nomenclature ---")
    print(f"organizationId: {org_id}")
    print(f"категорий (groups, верхний уровень списка): {len(groups)}")
    print(f"уникальных id групп после разворота childGroups / productCategories: {len(flat_groups)}")
    print(f"продуктов (products): {len(products)}\n")

    if not products:
        print(
            "Продуктов 0 — синхронизация меню не создаст строки в menu_items.\n"
            "Это не проблема SQLite/PostgreSQL: данных просто нет в ответе API.\n"
            "Проверьте в iiko: выгрузка в облако, лицензия API, что меню привязано к этой организации."
        )
        return

    types = collections.Counter()
    no_name = 0
    included = 0
    skipped_type = 0
    for p in products:
        tid = p.get("id")
        if not tid:
            continue
        t = p.get("type")
        types[str(t) if t is not None else "(пусто)"] += 1
        name = (p.get("name") or "").strip()
        if not name:
            no_name += 1
            continue
        if _include_product_by_type(p, only_dish_good):
            included += 1
        else:
            skipped_type += 1

    print("Распределение product.type (первые 15 значений):")
    for k, v in types.most_common(15):
        print(f"  {k!r}: {v}")
    if len(types) > 15:
        print(f"  ... всего уникальных типов: {len(types)}")
    print()
    print(f"Без имени (будут пропущены): {no_name}")
    print(f"Фильтр only_dish_good={only_dish_good}: пройдёт в sync ≈ {included}, отсечётся по типу ≈ {skipped_type}")
    print()
    if only_dish_good and included == 0 and skipped_type > 0:
        print(
            "Подсказка: в .env установите IIKO_MENU_SYNC_ONLY_DISH_GOOD=false "
            "или приведите типы номенклатуры к Dish/Good в iiko."
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--login", default="", help="IIKO_API_LOGIN (иначе из .env)")
    parser.add_argument("--org", default="", help="IIKO_ORGANIZATION_ID (иначе из .env)")
    args = parser.parse_args()

    from app.core.config import settings  # noqa: E402

    api_login = (args.login or settings.iiko_api_login).strip()
    org_id = (args.org or settings.iiko_organization_id).strip()
    only_dish_good = settings.iiko_menu_sync_only_dish_good

    if not api_login or not org_id:
        print("Нужны IIKO_API_LOGIN и IIKO_ORGANIZATION_ID в .env или флаги --login / --org.", file=sys.stderr)
        sys.exit(1)

    asyncio.run(_run(org_id, api_login, only_dish_good))


if __name__ == "__main__":
    main()
