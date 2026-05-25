"""Метрика queue wait: webhook enqueue → worker/BackgroundTasks process start."""

from __future__ import annotations

import logging
import time

from app.core.config import settings
from app.db.session import redis_client

logger = logging.getLogger(__name__)

_KEY_PREFIX = "rm:wa_enqueued:"
_TTL_SEC = 600


def _queue_key(identifier: str) -> str:
    return f"{_KEY_PREFIX}{identifier.strip()}"


async def mark_whatsapp_enqueued(
    *,
    trace_id: str | None,
    whatsapp_message_id: str | None,
) -> None:
    """Фиксирует monotonic wall time постановки в очередь (до process_with_retry)."""
    if not settings.redis_enabled:
        return
    key_id = (whatsapp_message_id or trace_id or "").strip()
    if not key_id:
        return
    try:
        await redis_client.setex(_queue_key(key_id), _TTL_SEC, str(time.time()))
    except Exception as exc:
        logger.debug("wa_queue_metrics mark failed id=%s: %s", key_id[:24], exc)


async def pop_queue_wait_ms(
    *,
    trace_id: str | None,
    whatsapp_message_id: str | None,
) -> float | None:
    """Снимает метку enqueue и возвращает ожидание в ms (или None)."""
    if not settings.redis_enabled:
        return None
    key_id = (whatsapp_message_id or trace_id or "").strip()
    if not key_id:
        return None
    key = _queue_key(key_id)
    try:
        raw = await redis_client.get(key)
        if raw is None:
            return None
        await redis_client.delete(key)
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8", errors="replace")
        enqueued_at = float(raw)
        return round((time.time() - enqueued_at) * 1000, 2)
    except Exception as exc:
        logger.debug("wa_queue_metrics pop failed id=%s: %s", key_id[:24], exc)
        return None
