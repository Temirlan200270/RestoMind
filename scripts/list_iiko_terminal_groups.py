#!/usr/bin/env python3
"""
Список терминальных групп iiko Cloud для организации (POST /api/1/terminal_groups).

Нужен UUID группы для IIKO_TERMINAL_GROUP_ID — им фильтруется стоп-лист (только эта точка),
если в сети несколько заведений под одной организацией.

Запуск из корня проекта:
    python scripts/list_iiko_terminal_groups.py
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


async def _run(org_id: str, api_login: str, include_disabled: bool) -> None:
    async with IikoClient(api_login=api_login) as client:
        data = await client.get_terminal_groups([org_id], include_disabled=include_disabled)

    wrappers = data.get("terminalGroups") or []
    sleep_wrappers = data.get("terminalGroupsInSleep") or []

    print("Терминальные группы (id — подставьте в IIKO_TERMINAL_GROUP_ID):\n")

    def _print_block(title: str, blocks: list) -> None:
        if not blocks:
            return
        print(f"--- {title} ---")
        for w in blocks:
            oid = w.get("organizationId", "")
            for tg in w.get("items") or []:
                if not isinstance(tg, dict):
                    continue
                tid = tg.get("id", "")
                name = tg.get("name", "")
                print(f"  id: {tid}")
                print(f"  name: {name}")
                if oid:
                    print(f"  organizationId: {oid}")
                print()

    _print_block("Активные", wrappers)
    _print_block("В режиме сна (sleep)", sleep_wrappers)

    if not wrappers and not sleep_wrappers:
        print("Список пуст. Проверьте IIKO_ORGANIZATION_ID и доступ API.", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--login", default="", help="IIKO_API_LOGIN")
    parser.add_argument("--org", default="", help="IIKO_ORGANIZATION_ID")
    parser.add_argument(
        "--include-disabled",
        action="store_true",
        help="Передать includeDisabled=true в API",
    )
    args = parser.parse_args()

    from app.core.config import settings  # noqa: E402

    api_login = (args.login or settings.iiko_api_login).strip()
    org_id = (args.org or settings.iiko_organization_id).strip()
    if not api_login or not org_id:
        print("Нужны IIKO_API_LOGIN и IIKO_ORGANIZATION_ID в .env или --login / --org.", file=sys.stderr)
        sys.exit(1)

    asyncio.run(_run(org_id, api_login, args.include_disabled))


if __name__ == "__main__":
    main()
