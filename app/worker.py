"""
ARQ worker entrypoint.

Запуск локально:
  ARQ_ENABLED=true REDIS_ENABLED=true REDIS_URL=redis://... python -m arq app.worker.WorkerSettings
"""

from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.services.billing_rollup import billing_usage_daily_scheduled_tick
from app.services.night_preorders import morning_preorders_tick
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
    organization_id: int | None = None,
) -> None:
    # Импорт внутри, чтобы worker не тащил FastAPI на импорт‑тайме
    from app.api.webhooks import process_with_retry

    await process_with_retry(
        phone,
        message_text,
        whatsapp_message_id=whatsapp_message_id,
        webhook_value=webhook_value,
        organization_id=organization_id,
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


async def send_review_request(
    ctx: dict[str, Any],
    *,
    org_id: int,
    order_id: int,
    phone: str,
) -> None:
    from app.services.review_requests import run_send_review_request
    await run_send_review_request(org_id=org_id, order_id=order_id, phone=phone)


async def send_blast_batch(
    ctx: dict[str, Any],
    *,
    blast_id: int,
) -> None:
    from app.services.marketing import run_send_blast_batch
    await run_send_blast_batch(blast_id=blast_id)


async def iiko_stoplist_sync(
    ctx: dict[str, Any],
    *,
    org_id: int,
) -> None:
    from app.services.iiko_sync_tasks import run_stoplist_sync
    await run_stoplist_sync(org_id)


async def iiko_menu_sync(
    ctx: dict[str, Any],
    *,
    org_id: int,
) -> None:
    from app.services.iiko_sync_tasks import run_menu_sync
    await run_menu_sync(org_id)


class WorkerSettings:
    # Это имена задач, которые мы enqueue_job("name", **kwargs) будем вызывать.
    # Важно: web-процесс ставит задачи в эту же очередь через task_queue._queue_name().
    queue_name = (settings.arq_queue_name or "restomind").strip() or "restomind"
    functions = [
        whatsapp_process_text,
        whatsapp_process_voice,
        whatsapp_process_statuses,
        payment_notify_customer,
        send_review_request,
        send_blast_batch,
        morning_preorders_tick,
        iiko_stoplist_sync,
        iiko_menu_sync,
    ]
    # Digest: 4× в час; биллинг: суточный rollup; ночные предзаказы: каждые 5 мин.
    cron_jobs = tuple(
        [
            cron(owner_digest_scheduled_tick, minute={0, 15, 30, 45}),
            cron(billing_usage_daily_scheduled_tick, hour=0, minute=12),
            cron(morning_preorders_tick, minute={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55}),
        ]
        if cron is not None
        else [],
    )

