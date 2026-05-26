#!/usr/bin/env python3
"""
Verify Owner Intelligence OS schema after `alembic upgrade head`.

Usage:
  python scripts/verify_owner_intel_schema.py
  DATABASE_URL=postgresql://... python scripts/verify_owner_intel_schema.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import settings
from app.db.pool_settings import resolve_postgres_pool_settings
from app.db.ssl_context import postgres_connect_args


REQUIRED_TABLES = (
    "ai_order_audits",
    "upsell_offer_events",
    "operational_mode_states",
    "upsell_phrase_variants",
)

REQUIRED_COLUMNS: dict[str, tuple[str, ...]] = {
    "users": ("telegram_user_id",),
    "chat_logs": ("channel",),
    "organizations": ("pos_provider", "telegram_bot_username", "telegram_webhook_secret"),
    "menu_items": ("cost_price",),
    "ai_order_audits": ("review_reason",),
}


async def _run() -> int:
    if settings.db_mode != "postgres":
        print("SKIP: db_mode is not postgres (local sqlite — schema checks limited)")
        return 0

    pool_cfg = resolve_postgres_pool_settings(settings.database_url)
    engine = create_async_engine(
        settings.database_url,
        pool_size=1,
        max_overflow=0,
        connect_args=postgres_connect_args(settings.database_url),
    )
    errors: list[str] = []

    async with engine.connect() as conn:
        def _inspect(sync_conn):
            return inspect(sync_conn)

        inspector = await conn.run_sync(_inspect)
        tables = set(inspector.get_table_names())

        for table in REQUIRED_TABLES:
            if table not in tables:
                errors.append(f"missing table: {table}")

        for table, cols in REQUIRED_COLUMNS.items():
            if table not in tables:
                errors.append(f"missing table for columns check: {table}")
                continue
            existing = {c["name"] for c in inspector.get_columns(table)}
            for col in cols:
                if col not in existing:
                    errors.append(f"missing column: {table}.{col}")

        rev = await conn.execute(text("SELECT version_num FROM alembic_version"))
        head = rev.scalar_one_or_none()
        print(f"alembic_version: {head}")

    await engine.dispose()

    if errors:
        print("FAIL — schema verification errors:")
        for err in errors:
            print(f"  - {err}")
        return 1

    print("OK — Owner Intelligence OS schema verified")
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
