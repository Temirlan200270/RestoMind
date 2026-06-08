"""Daily OS digest for owners."""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import AuditLog, BusinessRecommendation, OperationalInsight, Organization
from app.db.session import redis_client
from app.integrations.telegram import send_ops_notification_html
from app.services.digest_agent_actions import (
    append_actions_to_digest_html,
    append_actions_to_digest_text,
    build_digest_agent_actions,
    digest_actions_reply_markup,
)

logger = logging.getLogger(__name__)


async def build_daily_os_digest_payload(
    db: AsyncSession,
    org: Organization,
    *,
    target_day: date | None = None,
) -> dict[str, Any]:
    day = target_day or (datetime.now(timezone.utc).date() - timedelta(days=1))
    start = datetime.combine(day, time.min, tzinfo=timezone.utc)
    end = start + timedelta(days=1)

    audit_count = await db.scalar(
        select(func.count(AuditLog.id)).where(
            AuditLog.organization_id == org.id,
            AuditLog.created_at >= start,
            AuditLog.created_at < end,
        )
    ) or 0
    escalations = await db.scalar(
        select(func.count(AuditLog.id)).where(
            AuditLog.organization_id == org.id,
            AuditLog.action.in_(["ai.escalated", "operator.took_over"]),
            AuditLog.created_at >= start,
            AuditLog.created_at < end,
        )
    ) or 0
    integration_failures = await db.scalar(
        select(func.count(AuditLog.id)).where(
            AuditLog.organization_id == org.id,
            AuditLog.action.like("integration.%"),
            AuditLog.created_at >= start,
            AuditLog.created_at < end,
        )
    ) or 0
    recommendations = await db.scalar(
        select(func.count(BusinessRecommendation.id)).where(
            BusinessRecommendation.organization_id == org.id,
            BusinessRecommendation.created_at >= start,
            BusinessRecommendation.created_at < end,
        )
    ) or 0
    incidents = await db.scalar(
        select(func.count(OperationalInsight.id)).where(
            OperationalInsight.organization_id == org.id,
            OperationalInsight.created_at >= start,
            OperationalInsight.created_at < end,
        )
    ) or 0
    recovered_kzt = 0.0
    try:
        from sqlalchemy import text as sql_text

        row = (
            await db.execute(
                sql_text(
                    "SELECT recovered_kzt FROM daily_org_stats WHERE organization_id = :org_id AND day = :day"
                ),
                {"org_id": int(org.id), "day": day},
            )
        ).mappings().first()
        if row:
            recovered_kzt = round(float(row["recovered_kzt"] or 0), 2)
    except Exception:
        recovered_kzt = 0.0
    hours_saved = round(float(audit_count) * 0.08 + float(escalations) * 0.25, 1)
    recovered_str = f"{recovered_kzt:,.0f} ₸".replace(",", " ")
    text = (
        f"Daily OS Digest за {day.isoformat()}\n"
        f"OS-действий: {int(audit_count)}\n"
        f"Спасено действиями: {recovered_str}\n"
        f"Эскалаций/перехватов: {int(escalations)}\n"
        f"Сбоев интеграций: {int(integration_failures)}\n"
        f"Новых рекомендаций: {int(recommendations)}\n"
        f"Инсайтов/инцидентов: {int(incidents)}\n"
        f"Оценка экономии времени: {hours_saved} ч"
    )
    return {
        "day": day.isoformat(),
        "audit_count": int(audit_count),
        "escalations": int(escalations),
        "integration_failures": int(integration_failures),
        "recommendations": int(recommendations),
        "incidents": int(incidents),
        "recovered_kzt": recovered_kzt,
        "hours_saved": hours_saved,
        "text": text,
    }


async def _redis_set_once(key: str, ttl_sec: int = 3 * 24 * 3600) -> bool:
    try:
        return bool(await redis_client.set(key, "1", nx=True, ex=ttl_sec))  # type: ignore[call-arg]
    except TypeError:
        prev = await redis_client.get(key)
        if prev:
            return False
        await redis_client.set(key, "1")
        return True


async def maybe_send_daily_os_digest_for_org(db: AsyncSession, org: Organization) -> None:
    tz_name = (org.timezone or "UTC").strip() or "UTC"
    try:
        local = datetime.now(timezone.utc).astimezone(ZoneInfo(tz_name))
    except Exception:
        local = datetime.now(timezone.utc)
    if local.hour != 9 or local.minute >= 45:
        return
    target_day = local.date() - timedelta(days=1)
    key = f"daily_os_digest:{org.id}:{target_day.isoformat()}"
    if not await _redis_set_once(key):
        return
    payload = await build_daily_os_digest_payload(db, org, target_day=target_day)
    base_text = str(payload.get("text") or "")
    start = datetime.combine(target_day, time.min, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    agent_actions = await build_digest_agent_actions(
        db,
        int(org.id),
        start=start,
        end=end,
        source="daily_os_digest",
        idempotency_prefix=f"digest:daily:{target_day.isoformat()}:{org.id}",
    )
    if agent_actions:
        payload["agent_actions"] = agent_actions
        payload["text"] = append_actions_to_digest_text(base_text, agent_actions)
    safe = base_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    html = "<b>RestoMind OS — daily digest</b><br/><br/>" + safe.replace("\n", "<br/>")
    html = append_actions_to_digest_html(html, agent_actions)
    reply_markup = digest_actions_reply_markup(agent_actions)
    await send_ops_notification_html(
        html,
        organization_id=int(org.id),
        reply_markup=reply_markup,
    )


async def daily_os_digest_scheduled_tick(_ctx: dict[str, Any]) -> None:
    if not settings.telegram_bot_token.strip():
        return
    from app.db.session import async_session_factory

    async with async_session_factory() as db:
        rows = (await db.execute(select(Organization).where(Organization.is_active.is_(True)))).scalars().all()
        for org in rows:
            try:
                await maybe_send_daily_os_digest_for_org(db, org)
            except Exception:
                logger.exception("daily_os_digest failed org=%s", org.id)
