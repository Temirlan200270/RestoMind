"""
Одноразовый хелпер: чинит Docker Start Command на Render для сервиса RestoMind.

Нужны переменные окружения:
  RENDER_API_KEY   — Render API key

Запуск:
  set RENDER_API_KEY=rnd_...
  python scripts\\render_fix_start_command.py
"""

from __future__ import annotations

import os
import json
from pathlib import Path

import httpx


SERVICE_ID = "srv-d6uu88uuk2gs738etq7g"  # RestoMind
MCP_JSON = Path.home() / ".cursor" / "mcp.json"


def _load_api_key_from_mcp_json() -> str:
    if not MCP_JSON.exists():
        return ""
    try:
        d = json.loads(MCP_JSON.read_text(encoding="utf-8"))
        raw = (
            d.get("mcpServers", {})
            .get("render", {})
            .get("headers", {})
            .get("Authorization", "")
        )
        s = str(raw or "").strip()
        if s.lower().startswith("bearer "):
            s = s[7:].strip()
        return s
    except Exception:
        return ""


def main() -> None:
    api_key = (os.environ.get("RENDER_API_KEY") or "").strip()
    if not api_key:
        api_key = _load_api_key_from_mcp_json()
    if not api_key:
        raise SystemExit("RENDER_API_KEY is not set and ~/.cursor/mcp.json has no token")

    auth = f"Bearer {api_key}"
    # Важно: Render исполняет dockerCommand как entrypoint-подобную строку.
    # Используем `sh -lc` и `;` (не &&), чтобы избежать проблем с разбором аргументов.
    cmd = "sh -lc 'echo \"[boot] alembic upgrade\"; alembic upgrade head; echo \"[boot] uvicorn\"; exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}'"
    payload = {"serviceDetails": {"envSpecificDetails": {"dockerCommand": cmd}}}

    with httpx.Client(timeout=20.0) as c:
        r = c.patch(
            f"https://api.render.com/v1/services/{SERVICE_ID}",
            headers={"Authorization": auth, "Content-Type": "application/json"},
            json=payload,
        )
        r.raise_for_status()
        print("updated dockerCommand")

        d = c.post(
            f"https://api.render.com/v1/services/{SERVICE_ID}/deploys",
            headers={"Authorization": auth},
        )
        d.raise_for_status()
        j = d.json() if isinstance(d.json(), dict) else {}
        print("deploy started", j.get("id"))


if __name__ == "__main__":
    main()

