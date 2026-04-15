"""
Проверка схемы Render Postgres (диагностика миграций) без ручного копирования DATABASE_URL.

Читает Bearer из ~/.cursor/mcp.json (Render MCP) и тянет DATABASE_URL из env-vars сервиса,
затем проверяет:
  - есть ли organizations.slug
  - есть ли knowledge_items.knowledge_kind
  - текущую alembic_version
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import asyncpg
import httpx


SERVICE_ID = "srv-d6uu88uuk2gs738etq7g"
MCP_JSON = Path.home() / ".cursor" / "mcp.json"


def _load_render_auth_header() -> str:
    d = json.loads(MCP_JSON.read_text(encoding="utf-8"))
    raw = (
        d.get("mcpServers", {})
        .get("render", {})
        .get("headers", {})
        .get("Authorization", "")
    )
    return str(raw or "").strip()


async def main() -> None:
    auth = _load_render_auth_header()
    if not auth.lower().startswith("bearer "):
        raise SystemExit("mcp.json: render Authorization must be Bearer ...")

    async with httpx.AsyncClient(timeout=20.0) as client:
        env = (await client.get(
            f"https://api.render.com/v1/services/{SERVICE_ID}/env-vars",
            headers={"Authorization": auth},
        )).json()
    db_url = ""
    for x in env:
        if isinstance(x, dict) and x.get("envVar", {}).get("key") == "DATABASE_URL":
            db_url = str(x.get("envVar", {}).get("value") or "").strip()
            break
    if not db_url:
        raise SystemExit("DATABASE_URL not found in service env-vars")

    conn = await asyncpg.connect(db_url)
    try:
        cols = await conn.fetch(
            """
            select column_name
            from information_schema.columns
            where table_name = 'organizations'
              and column_name in ('slug', 'tenant_id', 'iiko_api_login_enc', 'prepayment_enforced')
            order by 1
            """,
        )
        print("organizations:", [r["column_name"] for r in cols])

        kk = await conn.fetchval(
            """
            select count(*)::int
            from information_schema.columns
            where table_name = 'knowledge_items' and column_name = 'knowledge_kind'
            """,
        )
        print("knowledge_items.knowledge_kind:", bool(kk))

        av = await conn.fetchval("select version_num from alembic_version limit 1")
        print("alembic_version:", av)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())

