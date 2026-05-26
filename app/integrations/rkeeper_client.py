"""
Stub-клиент r_keeper (Wave 4 POS Phase 2).

Реальный HTTP/XML API будет подключён в следующей фазе; сейчас — предсказуемые
ответы для адаптера и тестов.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class RKeeperClient:
    """Минимальный async-клиент r_keeper (stub)."""

    def __init__(self, *, server_url: str, object_id: str) -> None:
        self.server_url = (server_url or "").strip().rstrip("/")
        self.object_id = (object_id or "").strip()

    async def health(self) -> dict[str, Any]:
        """Проверка доступности POS (stub — всегда ok при заполненных creds)."""
        ok = bool(self.server_url and self.object_id)
        return {
            "ok": ok,
            "provider": "rkeeper",
            "server_url": self.server_url,
            "object_id": self.object_id,
        }

    async def fetch_menu(self) -> dict[str, Any]:
        """Номенклатура r_keeper (stub)."""
        logger.info("rkeeper stub fetch_menu object_id=%s", self.object_id)
        return {
            "provider": "rkeeper",
            "object_id": self.object_id,
            "items": [
                {
                    "id": f"rk-{self.object_id}-demo-1",
                    "name": "Demo r_keeper dish",
                    "category": "Main",
                    "price": 1500.0,
                    "description": "Stub menu item from r_keeper client",
                },
            ],
        }

    async def fetch_stoplist(self) -> dict[str, Any]:
        """Стоп-лист r_keeper (stub — пустой)."""
        logger.info("rkeeper stub fetch_stoplist object_id=%s", self.object_id)
        return {
            "provider": "rkeeper",
            "object_id": self.object_id,
            "stopped_ids": [],
        }
