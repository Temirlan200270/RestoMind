#!/usr/bin/env python3
"""
Одноразовая проверка статуса асинхронной команды iiko Cloud по correlationId.

Использование:
  set IIKO_API_LOGIN и IIKO_ORGANIZATION_ID в .env (как у приложения)
  python scripts/check_iiko_command_status.py <correlation_id>

Или:
  set CORRELATION_ID=... и запустить без аргументов.

Эндпоинт: POST /api/1/commands/status (см. документацию iiko API).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

import httpx

IIKO_BASE = "https://api-ru.iiko.services"


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env")


async def main() -> int:
    _load_env()
    parser = argparse.ArgumentParser(description="Статус команды iiko по correlationId")
    parser.add_argument(
        "correlation_id",
        nargs="?",
        default=(os.environ.get("CORRELATION_ID") or "").strip(),
        help="UUID из логов после deliveries/create",
    )
    args = parser.parse_args()
    cid = (args.correlation_id or "").strip()
    api_login = (os.environ.get("IIKO_API_LOGIN") or "").strip()
    org_id = (os.environ.get("IIKO_ORGANIZATION_ID") or "").strip()
    if not cid:
        print("Укажите correlation_id аргументом или переменную CORRELATION_ID.", file=sys.stderr)
        return 2
    if not api_login or not org_id:
        print("Задайте IIKO_API_LOGIN и IIKO_ORGANIZATION_ID в .env", file=sys.stderr)
        return 2

    async with httpx.AsyncClient(base_url=IIKO_BASE, timeout=30.0) as client:
        tok = await client.post("/api/1/access_token", json={"apiLogin": api_login})
        tok.raise_for_status()
        token = tok.json().get("token")
        if not token:
            print("iiko не вернул token", file=sys.stderr)
            return 3

        resp = await client.post(
            "/api/1/commands/status",
            headers={"Authorization": f"Bearer {token}"},
            json={"organizationId": org_id, "correlationId": cid},
        )
        print("HTTP", resp.status_code)
        try:
            data = resp.json()
        except Exception:
            print(resp.text[:2000])
            return 4
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0 if resp.is_success else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
