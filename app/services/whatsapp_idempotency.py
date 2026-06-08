"""
Идемпотентность входящих сообщений WhatsApp (БД + Redis в вебхуке).
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import WhatsappInboundDedupe

logger = logging.getLogger(__name__)

PROCESSING_STALE_AFTER = timedelta(minutes=15)

# Кэш «обработано» после commit done — только ускорение; источник истины — БД.
WHATSAPP_INBOUND_REDIS_CACHE_TTL = int(PROCESSING_STALE_AFTER.total_seconds())


async def redis_whatsapp_inbound_done_cache_hit(message_id: str) -> bool:
    """True если в Redis помечено как успешно обработанное (после mark done)."""
    mid = (message_id or "").strip()
    if not mid:
        return False
    try:
        from app.db.session import redis_client

        raw = await redis_client.get(f"wa:dedupe:{mid}")
        return raw is not None and str(raw).strip() != ""
    except Exception as exc:
        logger.debug("WhatsApp dedupe redis read skipped: %s", exc)
        return False


async def cache_whatsapp_inbound_done_redis(message_id: str) -> None:
    """Выставить кэш после успешного commit mark_whatsapp_inbound_done (best-effort)."""
    mid = (message_id or "").strip()
    if not mid:
        return
    try:
        from app.db.session import redis_client

        await redis_client.setex(f"wa:dedupe:{mid}", WHATSAPP_INBOUND_REDIS_CACHE_TTL, "1")
    except TypeError:
        try:
            from app.db.session import redis_client

            await redis_client.set(f"wa:dedupe:{mid}", "1", ex=WHATSAPP_INBOUND_REDIS_CACHE_TTL)
        except Exception as exc:
            logger.debug("WhatsApp dedupe redis set (fallback) skipped: %s", exc)
    except Exception as exc:
        logger.debug("WhatsApp dedupe redis set skipped: %s", exc)


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


async def try_start_whatsapp_inbound_in_db(db: AsyncSession, *, message_id: str, phone: str) -> bool:
    """
    Атомарно начинает обработку входящего сообщения.

    True — сообщение можно обрабатывать.
    False — дубль уже обработан или прямо сейчас обрабатывается другим воркером.
    """
    mid = (message_id or "").strip()
    if not mid:
        return True

    now = datetime.now(timezone.utc)
    stale_before = now - PROCESSING_STALE_AFTER

    values = {
        "message_id": mid,
        "phone": phone,
        "status": "processing",
        "attempts": 1,
        "error": "",
        "claimed_at": now,
    }
    insert_stmt = pg_insert(WhatsappInboundDedupe)
    inserted = await db.execute(insert_stmt.values(**values).on_conflict_do_nothing(index_elements=["message_id"]))
    if (inserted.rowcount or 0) > 0:
        return True

    reclaimed = await db.execute(
        update(WhatsappInboundDedupe)
        .where(
            WhatsappInboundDedupe.message_id == mid,
            or_(
                WhatsappInboundDedupe.status == "failed",
                WhatsappInboundDedupe.claimed_at < stale_before,
            ),
        )
        .values(
            phone=phone,
            status="processing",
            attempts=WhatsappInboundDedupe.attempts + 1,
            error="",
            claimed_at=now,
            processed_at=None,
        )
    )
    if (reclaimed.rowcount or 0) > 0:
        logger.info("Повторная обработка message_id=%s после failed/stale состояния", mid)
        return True

    logger.info("Повтор message_id=%s уже обработан или выполняется — пропуск", mid)
    return False


async def mark_whatsapp_inbound_done(db: AsyncSession, message_id: str) -> None:
    mid = (message_id or "").strip()
    if not mid:
        return
    await db.execute(
        update(WhatsappInboundDedupe)
        .where(WhatsappInboundDedupe.message_id == mid)
        .values(status="done", error="", processed_at=datetime.now(timezone.utc))
    )


async def mark_whatsapp_inbound_failed(db: AsyncSession, message_id: str, error: str) -> None:
    mid = (message_id or "").strip()
    if not mid:
        return
    await db.execute(
        update(WhatsappInboundDedupe)
        .where(WhatsappInboundDedupe.message_id == mid)
        .values(status="failed", error=(error or "")[:2000])
    )


async def inbound_already_processed_in_db(db: AsyncSession, message_id: str) -> bool:
    """Проверка без записи (редко нужна)."""
    mid = (message_id or "").strip()
    if not mid:
        return False
    row = await db.scalar(
        select(WhatsappInboundDedupe.message_id).where(WhatsappInboundDedupe.message_id == mid),
    )
    return row is not None
