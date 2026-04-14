#!/usr/bin/env python3
"""
Проверка OPENAI_API_KEY: запрос списка моделей (v1/models).

Загрузка переменных:
  1) .openai_check.env (в корне репозитория, в .gitignore)
  2) .env

Пример:
  python scripts/check_openai_key.py
  python scripts/check_openai_key.py --limit 5
"""

from __future__ import annotations

import argparse
import os
import sys


def _load_dotenv_files() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    load_dotenv(os.path.join(root, ".openai_check.env"))
    load_dotenv(os.path.join(root, ".env"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Проверка ключа OpenAI через список моделей.")
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Сколько id моделей вывести (0 = только счётчик)",
    )
    args = parser.parse_args()

    _load_dotenv_files()

    key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if not key:
        print("Задайте OPENAI_API_KEY в окружении, .env или .openai_check.env", file=sys.stderr)
        return 1

    base = (os.environ.get("OPENAI_BASE_URL") or "").strip()

    try:
        from openai import APIError, OpenAI
    except ImportError:
        print("Нужен пакет openai: pip install openai", file=sys.stderr)
        return 1

    kw: dict[str, str] = {"api_key": key}
    if base:
        kw["base_url"] = base

    client = OpenAI(**kw)
    try:
        page = client.models.list()
    except APIError as exc:
        print(f"Ошибка OpenAI API: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1

    ids = sorted(m.id for m in page.data)
    print(f"OK: моделей в ответе: {len(ids)}")
    if args.limit > 0:
        for mid in ids[: args.limit]:
            print(f"  {mid}")
        if len(ids) > args.limit:
            print(f"  ... ещё {len(ids) - args.limit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
