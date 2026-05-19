"""Диалоговые события на OS Event Layer."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import redis_client
from app.services.system_events import BusinessEvent, emit_event

logger = logging.getLogger(__name__)


async def emit_dialog_started_once(
    db: AsyncSession,
    *,
    organization_id: int,
    phone: str,
    location_id: int | None = None,
) -> None:
    """Один ai.dialog.started на org+phone+день (UTC) для DailyOrgStats.dialogs_count."""
    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    key = f"evt:dialog_started:{organization_id}:{phone}:{today}"
    try:
        was_set = await redis_client.set(key, "1", nx=True, ex=86400)
    except Exception:
        was_set = True
    if not was_set:
        return
    try:
        await emit_event(
            db,
            BusinessEvent(
                id=f"ai.dialog.started:{organization_id}:{phone}:{today}",
                org_id=int(organization_id),
                type="ai.dialog.started",
                actor="customer",
                location_id=location_id,
                entity_type="user",
                entity_id=phone,
                payload={"phone": phone},
            ),
        )
    except Exception:
        logger.exception("emit_dialog_started_once failed org=%s phone=%s", organization_id, phone)
