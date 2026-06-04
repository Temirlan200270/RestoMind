"""Post-commit event consumers — вынесены из hot-path WhatsApp."""

from __future__ import annotations

import logging

from sqlalchemy import event as sa_event
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.async_tasks import spawn_tracked
from app.services.system_events import BusinessEvent

logger = logging.getLogger(__name__)


async def run_event_consumers(event: BusinessEvent, db: AsyncSession) -> None:
    """Analytics / audit / healing в переданной сессии (вызывающий коммитит)."""
    try:
        from app.services.analytics_consumer import on_business_event

        await on_business_event(event, db)
    except Exception:
        logger.exception(
            "analytics_consumer failed for event type=%s org=%d",
            event.type,
            event.org_id,
        )

    try:
        from app.services.audit_consumer import on_business_event as _audit_on_event

        await _audit_on_event(event, db)
    except Exception:
        logger.exception(
            "audit_consumer failed for event type=%s org=%d",
            event.type,
            event.org_id,
        )

    try:
        from app.services.healing_realtime import maybe_trigger_realtime_healing

        await maybe_trigger_realtime_healing(db, event)
    except Exception:
        logger.exception(
            "healing_realtime failed for event type=%s org=%d",
            event.type,
            event.org_id,
        )


async def run_event_consumers_isolated(event: BusinessEvent) -> None:
    """Отдельная сессия после commit родительской транзакции."""
    from app.db.session import async_session_factory

    async with async_session_factory() as db:
        try:
            await run_event_consumers(event, db)
            await db.commit()
        except Exception:
            await db.rollback()
            logger.exception(
                "isolated event consumers failed type=%s org=%d",
                event.type,
                event.org_id,
            )


def schedule_event_consumers_after_commit(db: AsyncSession, event: BusinessEvent) -> None:
    """Fire-and-forget consumers после успешного commit (rollback → не вызываются)."""
    sync_session = db.sync_session

    @sa_event.listens_for(sync_session, "after_commit", once=True)
    def _on_commit(_session) -> None:
        spawn_tracked(
            run_event_consumers_isolated(event),
            name=f"event_consumers_{event.org_id}_{event.type}",
            log=logger,
        )
