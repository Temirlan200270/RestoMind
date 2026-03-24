"""
Удаление устаревших записей chat_logs по политике ретеншна (сутки в UTC).
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete as sql_delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import ChatLog

logger = logging.getLogger(__name__)


def _cutoff_for_sql() -> datetime:
    """Граница «старше чем» в виде datetime для сравнения с колонкой БД."""
    cutoff_utc = datetime.now(timezone.utc) - timedelta(days=settings.chat_log_retention_days)
    if settings.db_mode == "sqlite":
        return cutoff_utc.replace(tzinfo=None)
    return cutoff_utc


async def purge_old_chat_logs(session: AsyncSession) -> int:
    """
    Удалить сообщения chat_logs с created_at старше chat_log_retention_days.

    Returns:
        Число удалённых строк (оценка по rowcount драйвера).
    """
    if settings.chat_log_retention_days <= 0:
        return 0
    cutoff = _cutoff_for_sql()
    r = await session.execute(sql_delete(ChatLog).where(ChatLog.created_at < cutoff))
    n = r.rowcount
    out = int(n) if n is not None and n >= 0 else 0
    if out:
        logger.info(
            "Ретеншн chat_logs: удалено %d записей (старше %s суток, cutoff=%s)",
            out,
            settings.chat_log_retention_days,
            cutoff,
        )
    return out


async def count_chat_logs_eligible_for_purge(session: AsyncSession) -> int:
    """Сколько строк попадёт под текущую политику (без удаления)."""
    if settings.chat_log_retention_days <= 0:
        return 0
    cutoff = _cutoff_for_sql()
    c = await session.scalar(
        select(func.count()).select_from(ChatLog).where(ChatLog.created_at < cutoff),
    )
    return int(c or 0)
