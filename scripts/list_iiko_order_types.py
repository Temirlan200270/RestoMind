#!/usr/bin/env python3
"""
Список типов заказов iiko Cloud для deliveries/create (orderTypeId / orderServiceType).

Нужен UUID для IIKO_ORDER_TYPE_ID, иначе iiko вернёт 400:
  "Only one of this fields required: orderTypeId or orderServiceType."

Запуск из корня проекта:
    python scripts/list_iiko_order_types.py
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from app.integrations.iiko_client import IikoClient  # noqa: E402


def _pick_items(payload: dict) -> list[dict]:
    """
    iiko может вернуть разные структуры. Пытаемся вытащить единый список items.
    """
    if not isinstance(payload, dict):
        return []

    # Чаще всего: { "orderTypes": [ ... ] }
    if isinstance(payload.get("orderTypes"), list):
        lst = [x for x in payload["orderTypes"] if isinstance(x, dict)]
        # В некоторых ответах orderTypes = [{ organizationId, items: [...] }]
        if lst and isinstance(lst[0].get("items"), list):
            out: list[dict] = []
            for w in lst:
                items = w.get("items")
                if isinstance(items, list):
                    out.extend([x for x in items if isinstance(x, dict)])
            return out
        return lst

    # Иногда: { "orderTypesByOrganization": [ { organizationId, items: [...] } ] }
    wrappers = payload.get("orderTypesByOrganization")
    if isinstance(wrappers, list):
        out: list[dict] = []
        for w in wrappers:
            if not isinstance(w, dict):
                continue
            items = w.get("items")
            if isinstance(items, list):
                out.extend([x for x in items if isinstance(x, dict)])
        return out

    # fallback: первый список-значение в словаре
    for v in payload.values():
        if isinstance(v, list) and v and isinstance(v[0], dict):
            return [x for x in v if isinstance(x, dict)]

    return []


async def _run(org_id: str, api_login: str, *, raw: bool) -> None:
    async with IikoClient(api_login=api_login) as client:
        data = await client.get_delivery_order_types([org_id])

    items = _pick_items(data)
    if not items:
        print("Не удалось распознать список типов заказов в ответе iiko.")
        print("Сырой ответ:\n")
        print(data)
        sys.exit(1)

    if raw:
        print("Сырой ответ iiko:\n")
        print(data)
        print("\n---\n")

    print("Типы заказов iiko (скопируйте id нужного в IIKO_ORDER_TYPE_ID):\n")
    printed = 0
    for it in items:
        oid = it.get("id", "")
        name = it.get("name", "")
        svc = it.get("orderServiceType", it.get("serviceType", ""))
        active = it.get("isActive", it.get("active", None))
        default = it.get("isDefaultForServiceType", it.get("default", None))

        if not oid and not name:
            # Похоже, мы выбрали не тот список из ответа — покажем сырьё.
            print("⚠️ Неожиданный формат элемента (нет id/name). Включите --raw и пришлите вывод.")
            print(f"Элемент: {it}\n")
            continue

        print(f"  id: {oid}")
        print(f"  name: {name}")
        if svc:
            print(f"  orderServiceType: {svc}")
        if active is not None:
            print(f"  isActive: {active}")
        if default is not None:
            print(f"  isDefaultForServiceType: {default}")
        print()
        printed += 1

    if printed == 0:
        print("Не удалось вывести ни одного типа заказа. Запусти с --raw и пришли вывод.", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--login", default="", help="IIKO_API_LOGIN")
    parser.add_argument("--org", default="", help="IIKO_ORGANIZATION_ID")
    parser.add_argument("--raw", action="store_true", help="Показать сырой JSON-ответ iiko")
    args = parser.parse_args()

    from app.core.config import settings  # noqa: E402

    api_login = (args.login or settings.iiko_api_login).strip()
    org_id = (args.org or settings.iiko_organization_id).strip()
    if not api_login or not org_id:
        print("Нужны IIKO_API_LOGIN и IIKO_ORGANIZATION_ID в .env или --login / --org.", file=sys.stderr)
        sys.exit(1)

    asyncio.run(_run(org_id, api_login, raw=args.raw))


if __name__ == "__main__":
    main()

