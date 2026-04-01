"""
Идемпотентность входящих сообщений WhatsApp (БД + Redis в вебхуке).
"""

import logging

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import WhatsappInboundDedupe

logger = logging.getLogger(__name__)


async def try_claim_whatsapp_inbound_in_db(db: AsyncSession, *, message_id: str, phone: str) -> bool:
    """
    Атомарно резервирует message_id в БД.

    Returns:
        True — первое вхождение, можно обрабатывать.
        False — дубликат (уже обработано ранее).
    """
    mid = (message_id or "").strip()
    if not mid:
        return True

    if settings.db_mode == "sqlite":
        stmt = (
            sqlite_insert(WhatsappInboundDedupe)
            .values(message_id=mid, phone=phone)
            .on_conflict_do_nothing(index_elements=["message_id"])
        )
    else:
        stmt = (
            pg_insert(WhatsappInboundDedupe)
            .values(message_id=mid, phone=phone)
            .on_conflict_do_nothing(index_elements=["message_id"])
        )

    result = await db.execute(stmt)
    claimed = (result.rowcount or 0) > 0
    if not claimed:
        logger.info("Повтор message_id=%s (БД) от %s — пропуск обработки", mid, phone)
    return claimed


async def inbound_already_processed_in_db(db: AsyncSession, message_id: str) -> bool:
    """Проверка без записи (редко нужна)."""
    mid = (message_id or "").strip()
    if not mid:
        return False
    row = await db.scalar(
        select(WhatsappInboundDedupe.message_id).where(WhatsappInboundDedupe.message_id == mid),
    )
    return row is not None
