"""
Учёт сообщений WhatsApp по организации/дню — upsert в message_accounting_logs.
Вызывается fire-and-forget: не блокирует pipeline бота.

direction:    "inbound"  | "outbound"
source:       "user"     | "ai" | "operator" | "system"
message_type: "text"     | "voice" | "interactive" | "template"
"""

from __future__ import annotations

import logging
from datetime import date

from sqlalchemy import text

from app.db.session import async_session_factory
from app.services.async_tasks import spawn_tracked

logger = logging.getLogger(__name__)


async def log_message(
    organization_id: int,
    direction: str,
    source: str,
    message_type: str,
) -> None:
    """Инкрементировать счётчик для (org, day, direction, source, type). Upsert идемпотентен."""
    try:
        today = date.today()
        async with async_session_factory() as db:
            await db.execute(
                text("""
                    INSERT INTO message_accounting_logs
                        (organization_id, day, direction, source, message_type, count)
                    VALUES (:org_id, :day, :direction, :source, :msg_type, 1)
                    ON CONFLICT (organization_id, day, direction, source, message_type)
                    DO UPDATE SET
                        count      = message_accounting_logs.count + 1,
                        updated_at = now()
                """),
                {
                    "org_id": int(organization_id),
                    "day": today,
                    "direction": direction,
                    "source": source,
                    "msg_type": message_type,
                },
            )
            await db.commit()
    except Exception:
        logger.exception(
            "message_accounting log failed org=%s dir=%s src=%s type=%s",
            organization_id, direction, source, message_type,
        )


def schedule_log_message(
    organization_id: int,
    direction: str,
    source: str,
    message_type: str,
) -> None:
    """Fire-and-forget: создаёт asyncio.Task, не блокирует вызывающий код."""
    spawn_tracked(
        log_message(
            organization_id=organization_id,
            direction=direction,
            source=source,
            message_type=message_type,
        ),
        name=f"msg_acct_{organization_id}_{direction}_{source}",
        log=logger,
    )
