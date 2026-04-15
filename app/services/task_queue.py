"""
ARQ (asyncio + Redis) очередь задач.

Цель: убрать тяжёлую обработку из FastAPI BackgroundTasks.
Если Redis/ARQ выключены — функции возвращают False, и вызывающий код может
безопасно откатиться на BackgroundTasks.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)

_pool_lock = asyncio.Lock()
_pool: Any | None = None


def arq_can_run() -> bool:
    """True если можно enqueue в Redis (нужен реальный Redis)."""
    if not settings.arq_enabled:
        return False
    if settings.redis_memory_only:
        return False
    if not settings.redis_enabled:
        return False
    if not (settings.redis_url or "").strip():
        return False
    return True


async def _get_pool() -> Any | None:
    global _pool
    if _pool is not None:
        return _pool
    if not arq_can_run():
        return None
    async with _pool_lock:
        if _pool is not None:
            return _pool
        try:
            from arq import create_pool
            from arq.connections import RedisSettings
        except Exception as exc:  # pragma: no cover
            logger.error("ARQ import error: %s", exc)
            return None
        try:
            _pool = await create_pool(
                RedisSettings.from_dsn(settings.redis_url),
                default_queue_name=(settings.arq_queue_name or "restomind").strip(),
            )
            return _pool
        except Exception as exc:
            logger.error("ARQ create_pool failed: %s", exc)
            _pool = None
            return None


async def enqueue_job(name: str, **kwargs: Any) -> bool:
    """
    Enqueue задачу в Redis.
    Возвращает False если очередь недоступна (тогда можно fallback на BackgroundTasks).
    """
    pool = await _get_pool()
    if pool is None:
        return False
    try:
        await pool.enqueue_job(name, **kwargs)
        return True
    except Exception as exc:
        logger.error("ARQ enqueue_job(%s) failed: %s", name, exc)
        return False

