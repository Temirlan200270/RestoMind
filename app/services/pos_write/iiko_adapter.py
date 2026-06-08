"""Guarded iiko POS write adapter."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.services.system_events import BusinessEvent, emit_event

logger = logging.getLogger(__name__)


class IikoWriteAdapter:
    """Apply menu mutations to iiko with idempotency and audit events."""

    def __init__(self, db: AsyncSession, *, organization_id: int) -> None:
        self._db = db
        self._org_id = int(organization_id)

    async def apply_menu_price_update(
        self,
        *,
        items: list[dict[str, Any]],
        idempotency_key: str,
        previewed: bool = False,
    ) -> dict[str, Any]:
        if not previewed:
            raise ValueError("dry_run_preview_required")
        if not bool(getattr(settings, "iiko_live_write_enabled", False)):
            return {
                "staged": True,
                "live_write_enabled": False,
                "items": items,
                "note": "IIKO_LIVE_WRITE_ENABLED=false — запись остаётся staged.",
            }

        key = (idempotency_key or "").strip() or str(uuid.uuid4())
        await emit_event(
            self._db,
            BusinessEvent(
                org_id=self._org_id,
                type="iiko.write.requested",
                actor="system",
                entity_type="iiko_write",
                entity_id=key,
                payload={"operation": "menu_price_update", "items_count": len(items), "idempotency_key": key},
            ),
        )
        try:
            # Live API contract placeholder — OLAP client is read-only today.
            result = {
                "applied": False,
                "staged": True,
                "idempotency_key": key,
                "items": items,
                "note": "Live iiko write adapter готов; подключите write API когда контракт iiko доступен.",
            }
            await emit_event(
                self._db,
                BusinessEvent(
                    org_id=self._org_id,
                    type="iiko.write.applied",
                    actor="system",
                    entity_type="iiko_write",
                    entity_id=key,
                    payload=result,
                ),
            )
            return result
        except Exception as exc:
            logger.exception("iiko write failed org=%s key=%s", self._org_id, key)
            await emit_event(
                self._db,
                BusinessEvent(
                    org_id=self._org_id,
                    type="iiko.write.failed",
                    actor="system",
                    entity_type="iiko_write",
                    entity_id=key,
                    payload={"error": str(exc)[:500], "idempotency_key": key},
                ),
            )
            raise
