#!/usr/bin/env python3
"""
Однократная или cron-синхронизация меню из iiko Cloud в локальную БД (SQLite/PostgreSQL).

Запуск из корня проекта:
    python scripts/sync_menu_from_iiko.py

Нужны в .env: IIKO_API_LOGIN, IIKO_ORGANIZATION_ID (и настроенный DATABASE_URL / DB_MODE).
"""

from __future__ import annotations

import asyncio
import os
import sys

# Корень репозитория в PYTHONPATH
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from app.db.session import async_session_factory  # noqa: E402
from app.services.menu_sync import sync_iiko_menu_to_db  # noqa: E402


async def main() -> None:
    async with async_session_factory() as db:
        try:
            stats = await sync_iiko_menu_to_db(db)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            sys.exit(1)
        await db.commit()
    print("Синхронизация меню:", stats)


if __name__ == "__main__":
    asyncio.run(main())
