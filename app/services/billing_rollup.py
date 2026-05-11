"""Суточный rollup AI-usage по tenant для биллинга (E2.3)."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AiUsageLog, BillingUsageDaily, Organization
from app.db.session import async_session_factory

logger = logging.getLogger(__name__)


async def run_billing_usage_daily_rollup_for_day(db: AsyncSession, target_day: date) -> int:
    """
    Агрегирует ai_usage_logs за target_day в billing_usage_daily по tenant_id.
    День — UTC (как в AiUsageLog.day). Возвращает число затронутых tenant.
    """
    agg = (
        select(
            Organization.tenant_id.label("tenant_id"),
            func.coalesce(func.sum(AiUsageLog.total_tokens), 0).label("total_tokens"),
            func.coalesce(func.sum(AiUsageLog.call_count), 0).label("ai_calls"),
        )
        .select_from(AiUsageLog)
        .join(Organization, Organization.id == AiUsageLog.organization_id)
        .where(
            AiUsageLog.day == target_day,
            Organization.tenant_id.is_not(None),
        )
        .group_by(Organization.tenant_id)
    )
    rows = (await db.execute(agg)).all()
    touched = 0
    for row in rows:
        tid = int(row.tenant_id)
        existing = await db.scalar(
            select(BillingUsageDaily).where(
                BillingUsageDaily.tenant_id == tid,
                BillingUsageDaily.day == target_day,
            ),
        )
        tt = int(row.total_tokens or 0)
        ac = int(row.ai_calls or 0)
        if existing is not None:
            existing.total_tokens = tt
            existing.ai_calls = ac
        else:
            db.add(
                BillingUsageDaily(
                    tenant_id=tid,
                    day=target_day,
                    total_tokens=tt,
                    ai_calls=ac,
                ),
            )
        touched += 1
    await db.commit()
    logger.info("billing_rollup: day=%s tenants=%s", target_day.isoformat(), touched)
    return touched


async def billing_usage_daily_scheduled_tick(ctx: dict) -> None:
    """ARQ cron: вчера по UTC."""
    _ = ctx
    yesterday = datetime.now(timezone.utc).date() - timedelta(days=1)
    async with async_session_factory() as db:
        await run_billing_usage_daily_rollup_for_day(db, yesterday)
