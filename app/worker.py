"""
ARQ worker entrypoint.

Запуск локально:
  ARQ_ENABLED=true REDIS_ENABLED=true REDIS_URL=redis://... python -m arq app.worker.WorkerSettings
"""

from __future__ import annotations

from typing import Any

from app.services.owner_weekly_digest import owner_digest_scheduled_tick
from app.services.payment_notify import run_payment_received_customer_notify

try:
    from arq.cron import cron
except Exception:  # pragma: no cover
    cron = None  # type: ignore[misc, assignment]


async def whatsapp_process_text(
    ctx: dict[str, Any],
    *,
    phone: str,
    message_text: str,
    whatsapp_message_id: str = "",
    webhook_value: dict[str, Any] | None = None,
) -> None:
    # Импорт внутри, чтобы worker не тащил FastAPI на импорт‑тайме
    from app.api.webhooks import process_with_retry

    await process_with_retry(
        phone,
        message_text,
        whatsapp_message_id=whatsapp_message_id,
        webhook_value=webhook_value,
    )


async def whatsapp_process_voice(
    ctx: dict[str, Any],
    *,
    phone: str,
    media_id: str,
    whatsapp_message_id: str = "",
    webhook_value: dict[str, Any] | None = None,
) -> None:
    from app.api.webhooks import process_voice_message

    await process_voice_message(
        phone,
        media_id,
        whatsapp_message_id=whatsapp_message_id,
        webhook_value=webhook_value,
    )


async def whatsapp_process_statuses(
    ctx: dict[str, Any],
    *,
    statuses: list[dict[str, Any]],
) -> None:
    from app.api.webhooks import _process_whatsapp_status_batch

    await _process_whatsapp_status_batch(statuses)


async def payment_notify_customer(
    ctx: dict[str, Any],
    *,
    order_id: int,
) -> None:
    await run_payment_received_customer_notify(order_id)


class WorkerSettings:
    # Это имена задач, которые мы enqueue_job("name", **kwargs) будем вызывать.
    functions = [
        whatsapp_process_text,
        whatsapp_process_voice,
        whatsapp_process_statuses,
        payment_notify_customer,
    ]
    # Понедельник ~10:00 в TZ каждой организации: проверка внутри тика (4× в час).
    cron_jobs = (
        [cron(owner_digest_scheduled_tick, minute={0, 15, 30, 45})] if cron is not None else []
    )

