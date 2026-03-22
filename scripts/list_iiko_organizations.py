#!/usr/bin/env python3
"""
Список организаций iiko Cloud для текущего IIKO_API_LOGIN из .env.

Запуск из корня проекта:
    python scripts/list_iiko_organizations.py

Поле id — это и есть значение для IIKO_ORGANIZATION_ID (UUID в ответе POST /api/1/organizations).

Опционально без правки .env:
    python scripts/list_iiko_organizations.py --login "ваш_api_login"
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


async def _run(api_login: str) -> None:
    async with IikoClient(api_login=api_login) as client:
        orgs = await client.get_organizations()

    if not orgs:
        print("Список организаций пуст. Проверьте API-логин и доступ в iiko Cloud.", file=sys.stderr)
        sys.exit(1)

    print("Организации (скопируйте id нужной в IIKO_ORGANIZATION_ID):\n")
    for o in orgs:
        oid = o.get("id", "")
        name = o.get("name", "")
        code = o.get("code", "")
        line = f"  id: {oid}\n  name: {name}"
        if code:
            line += f"\n  code: {code}"
        print(line + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Список организаций iiko по API-логину.")
    parser.add_argument(
        "--login",
        default="",
        help="Переопределить IIKO_API_LOGIN (иначе из .env через Settings)",
    )
    args = parser.parse_args()

    if args.login:
        api_login = args.login.strip()
    else:
        from app.core.config import settings  # noqa: E402

        api_login = settings.iiko_api_login.strip()

    if not api_login:
        print("Задайте IIKO_API_LOGIN в .env или передайте --login.", file=sys.stderr)
        sys.exit(1)

    asyncio.run(_run(api_login))


if __name__ == "__main__":
    main()
