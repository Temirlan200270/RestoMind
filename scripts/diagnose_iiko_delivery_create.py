#!/usr/bin/env python3
"""
Диагностика iiko deliveries/create для конкретного заведения.

Что делает:
1) Проверяет API-логин.
2) Показывает доступные organization / terminal groups / order types.
3) Показывает несколько productId из номенклатуры.
4) Отправляет тестовый create (или custom payload из файла) и печатает
   детальную ошибку iiko.

Примеры:
  python scripts/diagnose_iiko_delivery_create.py
  python scripts/diagnose_iiko_delivery_create.py --login "..." --org "..."
  python scripts/diagnose_iiko_delivery_create.py --payload-file payload.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

import httpx

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from app.core.config import settings  # noqa: E402
from app.integrations.iiko_client import IikoClient  # noqa: E402


def _prompt(label: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{label}{suffix}: ").strip()
    return value or default


def _pick_from_list(items: list[dict[str, Any]], key: str, title: str) -> str:
    if not items:
        return ""
    print(f"\n{title}:")
    for idx, item in enumerate(items[:20], start=1):
        ident = str(item.get(key, "")).strip()
        name = str(item.get("name", "")).strip()
        if name:
            print(f"  {idx:>2}. {name} ({ident})")
        else:
            print(f"  {idx:>2}. {ident}")
    raw = input("Введите номер из списка (Enter = пропустить): ").strip()
    if not raw:
        return ""
    try:
        pos = int(raw)
    except ValueError:
        return ""
    if 1 <= pos <= len(items[:20]):
        return str(items[pos - 1].get(key, "")).strip()
    return ""


def _print_http_error(exc: httpx.HTTPStatusError) -> None:
    status = exc.response.status_code if exc.response is not None else 0
    print(f"\n❌ iiko вернул HTTP {status}")
    body_text = ""
    try:
        body = exc.response.json()
        print(json.dumps(body, ensure_ascii=False, indent=2))
        body_text = json.dumps(body, ensure_ascii=False).lower()
    except Exception:
        body_text = (exc.response.text or "")[:4000].lower() if exc.response is not None else ""
        print((exc.response.text or "")[:4000] if exc.response is not None else str(exc))

    hints: list[str] = []
    if "ordertypeid" in body_text or "orderservicetype" in body_text:
        hints.append("Проверьте orderTypeId: используйте scripts/list_iiko_order_types.py и подставьте UUID из вашего аккаунта.")
    if "terminalgroup" in body_text:
        hints.append("Проверьте terminalGroupId: scripts/list_iiko_terminal_groups.py и UUID именно нужной точки.")
    if "phone" in body_text:
        hints.append("Проверьте phone: нужен валидный E.164, например +77001234567.")
    if "product" in body_text or "item" in body_text:
        hints.append("Проверьте productId: он должен быть из номенклатуры этой же organizationId.")
    if hints:
        print("\nПодсказки:")
        for h in hints:
            print(f"  - {h}")


def _extract_order_type_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    order_types = payload.get("orderTypes")
    if isinstance(order_types, list):
        rows = [x for x in order_types if isinstance(x, dict)]
        if rows and isinstance(rows[0].get("items"), list):
            out: list[dict[str, Any]] = []
            for w in rows:
                items = w.get("items")
                if isinstance(items, list):
                    out.extend([x for x in items if isinstance(x, dict)])
            return out
        return rows
    wrappers = payload.get("orderTypesByOrganization")
    if isinstance(wrappers, list):
        out2: list[dict[str, Any]] = []
        for w in wrappers:
            if not isinstance(w, dict):
                continue
            items = w.get("items")
            if isinstance(items, list):
                out2.extend([x for x in items if isinstance(x, dict)])
        return out2
    return []


def _extract_terminal_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for key in ("terminalGroups", "terminalGroupsInSleep"):
        wrappers = payload.get(key)
        if not isinstance(wrappers, list):
            continue
        for wrapper in wrappers:
            if not isinstance(wrapper, dict):
                continue
            items = wrapper.get("items")
            if isinstance(items, list):
                out.extend([x for x in items if isinstance(x, dict)])
    return out


def _extract_products(payload: dict[str, Any]) -> list[dict[str, Any]]:
    products = payload.get("products")
    if not isinstance(products, list):
        return []
    rows: list[dict[str, Any]] = []
    for p in products:
        if not isinstance(p, dict):
            continue
        pid = str(p.get("id", "")).strip()
        name = str(p.get("name", "")).strip()
        if not pid or not name:
            continue
        rows.append({
            "id": pid,
            "name": name,
            "type": str(p.get("type", "")).strip(),
        })
    return rows


async def _run(args: argparse.Namespace) -> int:
    login = (args.login or settings.iiko_api_login).strip()
    org_id = (args.org or settings.iiko_organization_id).strip()
    terminal_group_id = (args.terminal_group or settings.iiko_terminal_group_id).strip()
    order_type_id = (args.order_type_id or settings.iiko_order_type_id).strip()
    product_id = (args.product_id or "").strip()
    phone = (args.phone or "+77001234567").strip()
    customer_name = (args.customer_name or "Diagnostic Guest").strip()
    external_number = (args.external_number or str(uuid.uuid4())[:8]).strip()

    if not login:
        login = _prompt("IIKO apiLogin")
    if not login:
        print("apiLogin обязателен.", file=sys.stderr)
        return 2

    async with IikoClient(api_login=login) as client:
        print("✅ Авторизация iiko успешна.")

        orgs = await client.get_organizations()
        if orgs:
            print(f"\nНайдено организаций: {len(orgs)}")
        if not org_id:
            picked_org = _pick_from_list(orgs, "id", "Организации")
            org_id = picked_org or _prompt("organizationId")
        if not org_id:
            print("organizationId обязателен.", file=sys.stderr)
            return 2

        terminals_payload = await client.get_terminal_groups([org_id], include_disabled=True)
        terminals = _extract_terminal_items(terminals_payload)
        if terminals:
            print(f"\nНайдено terminal groups: {len(terminals)}")
        if not terminal_group_id:
            terminal_group_id = _pick_from_list(terminals, "id", "Terminal groups (первые 20)")

        order_types_payload = await client.get_delivery_order_types([org_id])
        order_types = _extract_order_type_items(order_types_payload)
        if order_types:
            print(f"\nНайдено order types: {len(order_types)}")
            for row in order_types[:20]:
                rid = str(row.get("id", "")).strip()
                rname = str(row.get("name", "")).strip()
                rsvc = str(row.get("orderServiceType", row.get("serviceType", ""))).strip()
                print(f"  - {rname or '<без имени>'} | id={rid} | service={rsvc or '-'}")
        if not order_type_id:
            picked_order_type = _pick_from_list(order_types, "id", "Order types (первые 20)")
            order_type_id = picked_order_type or _prompt("orderTypeId")
        if not order_type_id and not args.payload_file:
            print("orderTypeId обязателен для тестового create.", file=sys.stderr)
            return 2

        nomenclature = await client.get_nomenclature(org_id)
        products = _extract_products(nomenclature)
        print(f"\nНайдено продуктов в номенклатуре: {len(products)}")
        for row in products[:15]:
            print(f"  - {row['name']} | id={row['id']} | type={row['type'] or '-'}")
        if not product_id and not args.payload_file:
            picked_product = _pick_from_list(products, "id", "Продукты (первые 20)")
            product_id = picked_product or _prompt("productId")
        if not product_id and not args.payload_file:
            print("productId обязателен для тестового create.", file=sys.stderr)
            return 2

        if args.payload_file:
            payload_path = Path(args.payload_file).resolve()
            if not payload_path.exists():
                print(f"Файл не найден: {payload_path}", file=sys.stderr)
                return 2
            payload = json.loads(payload_path.read_text(encoding="utf-8"))
            print(f"\n➡️ Отправляем custom payload из файла: {payload_path}")
        else:
            order_payload: dict[str, Any] = {
                "customer": {
                    "phone": phone,
                    "name": customer_name,
                },
                "phone": phone,
                "externalNumber": external_number,
                "items": [
                    {
                        "productId": product_id,
                        "type": "Product",
                        "amount": 1,
                    },
                ],
                "orderTypeId": order_type_id,
                "comment": "RestoMind diagnostic create",
            }
            payload = {
                "organizationId": org_id,
                "order": order_payload,
                "createOrderSettings": {
                    "servicePrint": False,
                    "transportToFrontTimeout": 0,
                    "checkStopList": False,
                },
            }
            if terminal_group_id:
                payload["terminalGroupId"] = terminal_group_id

            print("\n➡️ Отправляем минимальный тестовый payload:")
            print(json.dumps(payload, ensure_ascii=False, indent=2))

        try:
            result = await client._request("POST", "/api/1/deliveries/create", json=payload, retry_on_4xx=False)
        except httpx.HTTPStatusError as exc:
            _print_http_error(exc)
            return 1

        print("\n✅ deliveries/create принят iiko.")
        print(json.dumps(result, ensure_ascii=False, indent=2))

        correlation_id = str(result.get("correlationId", "")).strip()
        if correlation_id:
            print("\nДальше проверьте статус команды:")
            print(f"  python scripts/check_iiko_command_status.py {correlation_id}")
        return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Диагностика iiko deliveries/create для конкретного аккаунта.")
    parser.add_argument("--login", default="", help="IIKO apiLogin")
    parser.add_argument("--org", default="", help="organizationId")
    parser.add_argument("--terminal-group", default="", help="terminalGroupId")
    parser.add_argument("--order-type-id", default="", help="orderTypeId")
    parser.add_argument("--product-id", default="", help="productId (любой валидный товар из номенклатуры)")
    parser.add_argument("--phone", default="+77001234567", help="Телефон клиента в формате E.164")
    parser.add_argument("--customer-name", default="Diagnostic Guest", help="Имя гостя (customer.name)")
    parser.add_argument("--external-number", default="", help="Внешний номер заказа (для поиска)")
    parser.add_argument(
        "--payload-file",
        default="",
        help="Путь к JSON-файлу с полностью вашим payload для /api/1/deliveries/create",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
