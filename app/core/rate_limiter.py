"""
Rate Limiter — защита от спама на уровне телефонного номера.
Использует Redis (или in-memory) для подсчёта запросов в скользящем окне.
"""

import asyncio
import logging
import time
from collections import defaultdict

from app.core.config import settings
from app.db.session import redis_pubsub_available

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
    return await check_rate_limit_window(
        key,
        limit=settings.rate_limit_per_minute,
        window_seconds=WINDOW_SECONDS,
    )


async def check_rate_limit_window(key: str, *, limit: int, window_seconds: int = WINDOW_SECONDS) -> bool:
    """Sliding-window rate limit with configurable window (e.g. hourly demo cap)."""
    if settings.redis_enabled and redis_pubsub_available():
        return await _check_redis_window(key, limit=limit, window_seconds=window_seconds)
    return await _check_memory_window(key, limit=limit, window_seconds=window_seconds)


async def _check_memory_window(key: str, *, limit: int, window_seconds: int) -> bool:
    """In-memory rate limiter (для разработки без Redis)."""
    now = time.monotonic()
    async with _lock:
        timestamps = _memory_store[key]
        cutoff = now - window_seconds
        _memory_store[key] = [t for t in timestamps if t > cutoff]

        if len(_memory_store[key]) >= limit:
            logger.warning(
                "Rate limit exceeded: %s (%d/%d, window=%ss)",
                key,
                len(_memory_store[key]),
                limit,
                window_seconds,
            )
            return False

        _memory_store[key].append(now)
        return True


async def _check_memory(key: str) -> bool:
    return await _check_memory_window(key, limit=settings.rate_limit_per_minute, window_seconds=WINDOW_SECONDS)


async def _check_redis_window(key: str, *, limit: int, window_seconds: int) -> bool:
    """Redis rate limiter через sorted set (sliding window)."""
    from app.db.session import redis_client

    redis_key = f"rate:{key}"
    now = time.time()
    cutoff = now - window_seconds

    pipe = redis_client.pipeline()
    pipe.zremrangebyscore(redis_key, 0, cutoff)
    pipe.zcard(redis_key)
    pipe.zadd(redis_key, {str(now): now})
    pipe.expire(redis_key, window_seconds + 5)

    try:
        results = await pipe.execute()
        current_count = results[1]

        if current_count >= limit:
            logger.warning(
                "Rate limit exceeded (Redis): %s (%d/%d, window=%ss)",
                key,
                current_count,
                limit,
                window_seconds,
            )
            return False
        return True
    except Exception as exc:
        logger.error("Rate limiter Redis error: %s", exc)
        return True


async def _check_redis(key: str) -> bool:
    return await _check_redis_window(
        key,
        limit=settings.rate_limit_per_minute,
        window_seconds=WINDOW_SECONDS,
    )
