"""
Rate Limiter — защита от спама на уровне телефонного номера.
Использует Redis (или in-memory) для подсчёта запросов в скользящем окне.
"""

import asyncio
import logging
import time
from collections import defaultdict

from app.core.config import settings

logger = logging.getLogger(__name__)

WINDOW_SECONDS = 60

_memory_store: dict[str, list[float]] = defaultdict(list)
_lock = asyncio.Lock()


async def check_rate_limit(key: str) -> bool:
    """
    Проверяет, не превысил ли ключ (phone/IP) лимит запросов.

    Returns:
        True — запрос разрешён, False — заблокирован.
    """
    if settings.redis_enabled:
        return await _check_redis(key)
    return await _check_memory(key)


async def _check_memory(key: str) -> bool:
    """In-memory rate limiter (для разработки без Redis)."""
    now = time.monotonic()
    async with _lock:
        timestamps = _memory_store[key]
        cutoff = now - WINDOW_SECONDS
        _memory_store[key] = [t for t in timestamps if t > cutoff]

        if len(_memory_store[key]) >= settings.rate_limit_per_minute:
            logger.warning("Rate limit exceeded: %s (%d/%d)", key, len(_memory_store[key]), settings.rate_limit_per_minute)
            return False

        _memory_store[key].append(now)
        return True


async def _check_redis(key: str) -> bool:
    """Redis rate limiter через sorted set (sliding window)."""
    from app.db.session import redis_client

    redis_key = f"rate:{key}"
    now = time.time()
    cutoff = now - WINDOW_SECONDS

    pipe = redis_client.pipeline()
    pipe.zremrangebyscore(redis_key, 0, cutoff)
    pipe.zcard(redis_key)
    pipe.zadd(redis_key, {str(now): now})
    pipe.expire(redis_key, WINDOW_SECONDS + 5)

    try:
        results = await pipe.execute()
        current_count = results[1]

        if current_count >= settings.rate_limit_per_minute:
            logger.warning("Rate limit exceeded (Redis): %s (%d/%d)", key, current_count, settings.rate_limit_per_minute)
            return False
        return True
    except Exception as exc:
        logger.error("Rate limiter Redis error: %s", exc)
        return True
