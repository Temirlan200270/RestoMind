"""
ARQ worker entrypoint.

Запуск локально:
  ARQ_ENABLED=true REDIS_ENABLED=true REDIS_URL=redis://... python -m arq app.worker.WorkerSettings
"""

from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.services.billing_rollup import billing_usage_daily_scheduled_tick
from app.services.daily_os_digest import daily_os_digest_scheduled_tick
from app.services.draft_recovery import draft_recovery_scheduled_tick
from app.services.iiko_sales_hourly_sync import sales_hourly_iiko_scheduled_tick
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
    trace_id: str | None = None,
) -> None:
    # Импорт внутри, чтобы worker не тащил FastAPI на импорт‑тайме
    from app.api.webhooks import process_with_retry

    await process_with_retry(
        phone,
        message_text,
        whatsapp_message_id=whatsapp_message_id,
        webhook_value=webhook_value,
        organization_id=organization_id,
        trace_id=trace_id,
    )


async def whatsapp_process_voice(
    ctx: dict[str, Any],
    *,
    phone: str,
    media_id: str,
    whatsapp_message_id: str = "",
    webhook_value: dict[str, Any] | None = None,
    trace_id: str | None = None,
) -> None:
    from app.api.webhooks import process_voice_message

    await process_voice_message(
        phone,
        media_id,
        whatsapp_message_id=whatsapp_message_id,
        webhook_value=webhook_value,
        trace_id=trace_id,
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


async def scheduled_blasts_tick(ctx: dict[str, Any]) -> None:
    from app.services.marketing import run_scheduled_blasts
    await run_scheduled_blasts()


async def ai_incidents_hourly_tick(ctx: dict[str, Any]) -> None:
    """Cron раз в час: AI-инциденты + SLA-check + авто-эскалация (Phase 5.3)."""
    import logging
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import select as _sel
    from app.db.models import OperationalInsight, Organization
    from app.db.session import async_session_factory
    from app.services.intelligence import detect_ai_incidents
    from app.services.pipeline_latency import check_sla_thresholds
    from app.services.healing_actions import run_healing_actions

    logger = logging.getLogger(__name__)

    async with async_session_factory() as db:
        org_ids = list((await db.execute(
            _sel(Organization.id).where(Organization.is_active.is_(True))
        )).scalars().all())

    for org_id in org_ids:
        try:
            async with async_session_factory() as db:
                await detect_ai_incidents(db, org_id)
                await check_sla_thresholds(db, org_id)

                # Phase 5 OS: self-healing actions — детект инцидентов + автодействия
                healing_done = await run_healing_actions(db, org_id)
                if healing_done:
                    logger.info("Phase5 healing actions org=%s: %s", org_id, healing_done)

                # Auto-escalation: инсайты "new" старше 2 часов → critical
                stale_cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=2)
                stale = (await db.execute(
                    _sel(OperationalInsight).where(
                        OperationalInsight.organization_id == org_id,
                        OperationalInsight.status == "new",
                        OperationalInsight.severity != "critical",
                        OperationalInsight.created_at < stale_cutoff,
                    ).limit(10)
                )).scalars().all()
                for insight in stale:
                    insight.severity = "critical"
                    logger.warning(
                        "Phase5.3 auto-escalate org=%s insight=%s: %s",
                        org_id, insight.id, insight.title,
                    )

                await db.commit()
        except Exception:
            logger.exception("ai_incidents_hourly_tick: ошибка для org=%s", org_id)

    logger.info("Phase5.3 hourly_tick: %d orgs processed", len(org_ids))

    # Phase 3a OS: snapshot retention — удаляем снимки старше 30 дней
    try:
        from sqlalchemy import delete as _del
        from app.db.models import AIContextSnapshot
        cutoff_snap = datetime.now(tz=timezone.utc) - timedelta(days=30)
        async with async_session_factory() as db:
            result = await db.execute(
                _del(AIContextSnapshot).where(AIContextSnapshot.created_at < cutoff_snap)
            )
            deleted = result.rowcount
            await db.commit()
        if deleted:
            logger.info("Phase3a snapshot_retention: deleted %d snapshots older than 30d", deleted)
    except Exception:
        logger.exception("snapshot_retention: ошибка")


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


async def iiko_inventory_sync(
    ctx: dict[str, Any],
    *,
    org_id: int,
) -> None:
    from app.services.iiko_sync_tasks import run_inventory_sync
    await run_inventory_sync(org_id)


async def external_reviews_sync(ctx: dict[str, Any], *, organization_id: int) -> None:
    """ARQ: GuestCare external reviews sync for one organization."""
    from app.db.session import async_session_factory
    from app.services.external_reviews_sync import sync_external_reviews_for_org

    async with async_session_factory() as db:
        await sync_external_reviews_for_org(db, organization_id)
        await db.commit()


async def external_reviews_sync_scheduled_tick(ctx: dict[str, Any]) -> None:
    """Cron: sync 2GIS/Google reviews for orgs with review URLs configured."""
    from app.services.external_reviews_sync import run_external_reviews_scheduled_sync

    await run_external_reviews_scheduled_sync()


async def iiko_inventory_sync_scheduled_tick(ctx: dict[str, Any]) -> None:
    """Cron каждые 6 часов: остатки iiko Office для всех филиалов с конфигом."""
    import logging
    from app.db.session import async_session_factory
    from app.services.iiko_sync_tasks import run_inventory_sync
    from app.services.org_iiko_office import list_organizations_with_iiko_office_db

    logger = logging.getLogger(__name__)
    async with async_session_factory() as db:
        orgs = await list_organizations_with_iiko_office_db(db)
    for org in orgs:
        try:
            await run_inventory_sync(int(org.id))
        except Exception:
            logger.exception("iiko_inventory_sync_scheduled_tick: org_id=%s", org.id)
    logger.info("iiko_inventory_sync_scheduled_tick: %d orgs processed", len(orgs))


async def waiter_kpi_sync_scheduled_tick(ctx: dict[str, Any]) -> None:
    """Cron: KPI офiciантов из iiko для всех филиалов с Cloud и/или Office."""
    import logging
    from app.db.session import async_session_factory
    from app.services.iiko_waiter_kpi_sync import (
        list_organizations_for_waiter_kpi_sync,
        record_waiter_kpi_sync_run,
        sync_waiter_kpi_for_org,
    )

    logger = logging.getLogger(__name__)
    async with async_session_factory() as db:
        orgs = await list_organizations_for_waiter_kpi_sync(db)
    for org in orgs:
        try:
            async with async_session_factory() as db:
                await sync_waiter_kpi_for_org(db, int(org.id), days=1)
        except Exception as exc:
            logger.exception("waiter_kpi_sync_scheduled_tick: org_id=%s", org.id)
            try:
                async with async_session_factory() as db:
                    await record_waiter_kpi_sync_run(
                        db,
                        int(org.id),
                        ok=False,
                        error_text=str(exc),
                    )
                    await db.commit()
            except Exception:
                logger.exception("waiter_kpi_sync_scheduled_tick: audit failed org_id=%s", org.id)
    logger.info("waiter_kpi_sync_scheduled_tick: %d orgs processed", len(orgs))


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
        scheduled_blasts_tick,
        morning_preorders_tick,
        iiko_stoplist_sync,
        iiko_menu_sync,
        iiko_inventory_sync,
        iiko_inventory_sync_scheduled_tick,
        waiter_kpi_sync_scheduled_tick,
        external_reviews_sync,
        external_reviews_sync_scheduled_tick,
        ai_incidents_hourly_tick,
        daily_os_digest_scheduled_tick,
        draft_recovery_scheduled_tick,
        sales_hourly_iiko_scheduled_tick,
    ]
    # Digest: 4× в час; биллинг: суточный rollup; ночные предзаказы: каждые 5 мин.
    # Запланированные рассылки: каждые 5 минут. AI-инциденты: каждый час в :05.
    cron_jobs = tuple(
        [
            cron(owner_digest_scheduled_tick, minute={0, 15, 30, 45}),
            cron(daily_os_digest_scheduled_tick, minute={0, 15, 30, 45}),
            cron(billing_usage_daily_scheduled_tick, hour=0, minute=12),
            cron(morning_preorders_tick, minute={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55}),
            cron(draft_recovery_scheduled_tick, minute={2, 12, 22, 32, 42, 52}),
            cron(scheduled_blasts_tick, minute={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55}),
            cron(ai_incidents_hourly_tick, minute=5),
            cron(iiko_inventory_sync_scheduled_tick, hour={0, 6, 12, 18}, minute=20),
            cron(waiter_kpi_sync_scheduled_tick, hour=22, minute=30),
            cron(sales_hourly_iiko_scheduled_tick, hour=23, minute=15),
            cron(external_reviews_sync_scheduled_tick, hour={2, 14}, minute=10),
        ]
        if cron is not None
        else [],
    )

