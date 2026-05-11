"""
ARQ worker entrypoint.

Запуск локально:
  ARQ_ENABLED=true REDIS_ENABLED=true REDIS_URL=redis://... python -m arq app.worker.WorkerSettings
"""

from __future__ import annotations

from typing import Any

from app.services.owner_weekly_digest import owner_digest_scheduled_tick
from app.services.payment_notify import run_payment_received_customer_notify
from app.services.payment_expiry import expire_stale_payment_transactions
from app.services.payment_reminder import send_payment_reminders_for_expired

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


async def payment_expire_cron(ctx: dict[str, Any]) -> None:
    """Expire stale payment transactions and send re-initiation reminders."""
    from app.db.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        expired = await expire_stale_payment_transactions(db)
        await db.commit()

    if expired:
        await send_payment_reminders_for_expired(expired, AsyncSessionLocal)


class WorkerSettings:
    # Это имена задач, которые мы enqueue_job("name", **kwargs) будем вызывать.
    functions = [
        whatsapp_process_text,
        whatsapp_process_voice,
        whatsapp_process_statuses,
        payment_notify_customer,
        payment_expire_cron,
    ]
    # Понедельник ~10:00 в TZ каждой организации: проверка внутри тика (4× в час).
    # payment_expire_cron: каждые 10 минут.
    cron_jobs = (
        [
            cron(owner_digest_scheduled_tick, minute={0, 15, 30, 45}),
            cron(payment_expire_cron, minute={0, 10, 20, 30, 40, 50}),
        ]
        if cron is not None
        else []
    )

