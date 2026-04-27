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
from fastapi import BackgroundTasks

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


async def dispatch_arq_or_background(
    job_name: str,
    background_tasks: BackgroundTasks,
    **kwargs: Any,
) -> bool:
    """
    Enqueue в ARQ; при недоступности — тот же fallback, что раньше вручную через BackgroundTasks.
    Возвращает True, если задача ушла в ARQ, False — если поставлен fallback.
    """
    ok = await enqueue_job(job_name, **kwargs)
    if ok:
        logger.debug("ARQ: task %s enqueued", job_name)
        return True
    # Ленивые импорты, чтобы не зацикливать webhooks ↔ task_queue
    if job_name == "whatsapp_process_statuses":
        from app.api.webhooks import _process_whatsapp_status_batch

        statuses = kwargs.get("statuses")
        if statuses is not None:
            background_tasks.add_task(_process_whatsapp_status_batch, list(statuses))
        else:
            logger.error("ARQ fallback: whatsapp_process_statuses без statuses")
        logger.warning("ARQ недоступен: whatsapp_process_statuses → BackgroundTasks")
        return False
    if job_name == "whatsapp_process_text":
        from app.api.webhooks import process_with_retry

        background_tasks.add_task(
            process_with_retry,
            kwargs["phone"],
            kwargs.get("message_text", ""),
            whatsapp_message_id=kwargs.get("whatsapp_message_id", ""),
            voice_audio=kwargs.get("voice_audio"),
            webhook_value=kwargs.get("webhook_value"),
        )
        logger.warning("ARQ недоступен: whatsapp_process_text → BackgroundTasks")
        return False
    if job_name == "whatsapp_process_voice":
        from app.api.webhooks import process_voice_message

        background_tasks.add_task(
            process_voice_message,
            kwargs["phone"],
            kwargs["media_id"],
            whatsapp_message_id=kwargs.get("whatsapp_message_id", ""),
            webhook_value=kwargs.get("webhook_value"),
        )
        logger.warning("ARQ недоступен: whatsapp_process_voice → BackgroundTasks")
        return False
    if job_name == "payment_notify_customer":
        from app.services.payment_notify import run_payment_received_customer_notify

        background_tasks.add_task(
            run_payment_received_customer_notify,
            int(kwargs["order_id"]),
        )
        logger.warning("ARQ недоступен: payment_notify_customer → BackgroundTasks")
        return False
    logger.error("ARQ fallback: неизвестная задача %s (kwargs=%s)", job_name, list(kwargs))
    return False

