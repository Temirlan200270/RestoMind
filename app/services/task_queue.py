"""
ARQ (asyncio + Redis) очередь задач.

В продакшене worker обязателен: fallback на FastAPI BackgroundTasks отсутствует.
При недоступности Redis/ARQ постановка задачи бросает исключение (и на старте
в production/staging — проверка в app.main lifespan).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import BackgroundTasks

from app.core.config import settings

logger = logging.getLogger(__name__)

_pool_lock = asyncio.Lock()
_pool: Any | None = None


class TaskQueueEnqueueError(RuntimeError):
    """Не удалось поставить задачу в ARQ (нет Redis, ошибка соединения или enqueue)."""


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


def _queue_name() -> str:
    return (settings.arq_queue_name or "restomind").strip() or "restomind"


async def _get_pool() -> Any:
    global _pool
    if _pool is not None:
        return _pool
    if not arq_can_run():
        raise TaskQueueEnqueueError(
            "ARQ queue unavailable: set REDIS_ENABLED=true, ARQ_ENABLED=true, REDIS_URL, "
            "and do not use REDIS_MEMORY_ONLY.",
        )
    async with _pool_lock:
        if _pool is not None:
            return _pool
        try:
            from arq import create_pool
            from arq.connections import RedisSettings
        except Exception as exc:  # pragma: no cover
            raise TaskQueueEnqueueError(f"ARQ import error: {exc}") from exc
        try:
            _pool = await create_pool(
                RedisSettings.from_dsn(settings.redis_url),
                default_queue_name=_queue_name(),
            )
            return _pool
        except Exception as exc:
            raise TaskQueueEnqueueError(f"ARQ create_pool failed: {exc}") from exc


async def enqueue_job(name: str, **kwargs: Any) -> None:
    """
    Поставить задачу в Redis или бросить ``TaskQueueEnqueueError``.

    Лог-формат структурный (поля попадают в `extra`, а текст — короткий
    и стабильный), чтобы пайплайн логов мог выделить:
    ``event=task_queue_enqueue queue=<name> job=<name> [job_id=...] [error=...]``.
    """
    queue = _queue_name()
    job_id = kwargs.get("_job_id") or kwargs.get("job_id")
    common = {
        "event": "task_queue_enqueue",
        "queue": queue,
        "job": name,
    }
    if job_id is not None:
        common["job_id"] = str(job_id)
    trace_id = kwargs.get("trace_id")
    if trace_id:
        common["trace_id"] = str(trace_id)

    try:
        pool = await _get_pool()
    except TaskQueueEnqueueError as exc:
        logger.error(
            "task_queue_enqueue_failed queue=%s job=%s error=%s",
            queue,
            name,
            exc,
            extra={**common, "outcome": "pool_unavailable", "error": str(exc)[:300]},
        )
        raise

    try:
        result = await pool.enqueue_job(name, **kwargs)
    except Exception as exc:
        logger.error(
            "task_queue_enqueue_failed queue=%s job=%s error=%s",
            queue,
            name,
            exc,
            extra={**common, "outcome": "enqueue_failed", "error": str(exc)[:300]},
        )
        raise TaskQueueEnqueueError(f"ARQ enqueue_job({name}) failed: {exc}") from exc

    real_job_id = getattr(result, "job_id", None) if result is not None else None
    if real_job_id and "job_id" not in common:
        common["job_id"] = str(real_job_id)
    trace_prefix = f"[trace_id={trace_id}] " if trace_id else ""
    logger.info(
        "%stask_queue_enqueue_ok queue=%s job=%s job_id=%s",
        trace_prefix,
        queue,
        name,
        common.get("job_id") or "-",
        extra={**common, "outcome": "enqueued"},
    )


async def assert_arq_reachable_for_prod_startup() -> None:
    """Проверка при старте web-процесса в production/staging (одно краткое подключение)."""
    from arq import create_pool
    from arq.connections import RedisSettings

    if not arq_can_run():
        raise RuntimeError(
            "APP_ENV production/staging requires Redis and ARQ: "
            "REDIS_URL, REDIS_ENABLED=true, ARQ_ENABLED=true, REDIS_MEMORY_ONLY=false.",
        )
    pool = await create_pool(
        RedisSettings.from_dsn(settings.redis_url),
        default_queue_name=(settings.arq_queue_name or "restomind").strip(),
    )
    try:
        await pool.close()
    except Exception:
        logger.warning("ARQ probe: pool.close failed", exc_info=True)


def _get_background_fn(job_name: str) -> Any:
    """Возвращает callable для запуска задачи без ARQ (BackgroundTasks fallback)."""
    import importlib

    _map = {
        "whatsapp_process_text": ("app.api.webhooks", "process_with_retry"),
        "whatsapp_process_voice": ("app.api.webhooks", "process_voice_message"),
        "whatsapp_process_statuses": ("app.api.webhooks", "_process_whatsapp_status_batch"),
        "payment_notify_customer": ("app.services.payment_notify", "run_payment_received_customer_notify"),
        "send_review_request": ("app.services.review_requests", "run_send_review_request"),
        "send_blast_batch": ("app.services.marketing", "run_send_blast_batch"),
        "iiko_stoplist_sync": ("app.services.iiko_sync_tasks", "run_stoplist_sync"),
        "iiko_menu_sync": ("app.services.iiko_sync_tasks", "run_menu_sync"),
        "iiko_inventory_sync": ("app.services.iiko_sync_tasks", "run_inventory_sync"),
    }
    if job_name not in _map:
        return None
    module_path, fn_name = _map[job_name]
    module = importlib.import_module(module_path)
    return getattr(module, fn_name, None)


async def dispatch_arq_or_background(
    job_name: str,
    background_tasks: BackgroundTasks,
    **kwargs: Any,
) -> None:
    """Ставит задачу в ARQ если настроен, иначе — через BackgroundTasks.

    Если ARQ настроен (arq_can_run=True) но enqueue падает — ошибка пробрасывается
    наверх без fallback (Redis сконфигурирован, но недостижим — нужно знать об этом).
    Fallback на BackgroundTasks работает только когда Redis вообще не настроен.
    """
    from app.services.wa_queue_metrics import mark_whatsapp_enqueued

    if job_name.startswith("whatsapp_process"):
        await mark_whatsapp_enqueued(
            trace_id=kwargs.get("trace_id"),
            whatsapp_message_id=kwargs.get("whatsapp_message_id"),
        )
    if job_name == "whatsapp_process_text" and settings.whatsapp_text_background_enabled:
        fn = _get_background_fn(job_name)
        if fn is None:
            logger.error("dispatch_arq_or_background: no fallback for job=%s", job_name)
            return
        background_tasks.add_task(fn, **kwargs)
        logger.info(
            "BackgroundTasks realtime: task %s scheduled",
            job_name,
            extra={
                "event": "task_queue_realtime_background",
                "job": job_name,
                "trace_id": str(kwargs.get("trace_id") or ""),
            },
        )
        return

    if arq_can_run():
        await enqueue_job(job_name, **kwargs)
        logger.debug("ARQ: task %s enqueued", job_name)
        return

    fn = _get_background_fn(job_name)
    if fn is None:
        logger.error("dispatch_arq_or_background: no fallback for job=%s", job_name)
        return
    background_tasks.add_task(fn, **kwargs)
    logger.debug("BackgroundTasks fallback: task %s scheduled", job_name)
