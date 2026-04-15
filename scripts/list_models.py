"""
Вывести список доступных моделей по API-ключу OpenAI.

Запуск (Windows):
  set OPENAI_API_KEY=ваш_ключ
  python scripts\\list_models.py
"""

from __future__ import annotations

import os

import httpx


def main() -> None:
    api_key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if not api_key:
        raise SystemExit("OPENAI_API_KEY is not set")

    r = httpx.get(
        "https://api.openai.com/v1/models",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=20.0,
    )
    r.raise_for_status()
    payload = r.json() if isinstance(r.json(), dict) else {}
    data = payload.get("data", [])
    ids: list[str] = []
    if isinstance(data, list):
        for x in data:
            if isinstance(x, dict) and x.get("id"):
                ids.append(str(x["id"]))
    for mid in sorted(set(ids), key=str.lower):
        print(mid)


if __name__ == "__main__":
    main()

