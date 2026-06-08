"""Proactive delivery for OperationalInsight with dedupe/history."""

from __future__ import annotations

import html
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import InsightDelivery, OperationalInsight, Organization
from app.integrations.telegram import send_ops_notification_html
from app.services.agent_actions import propose_agent_action, proposal_confirm_links
from app.services.insight_proactive_actions import build_proactive_action_from_insight
from app.services.system_events import BusinessEvent, emit_event

logger = logging.getLogger(__name__)


def _threshold_for_channel(org: Organization, channel: str) -> set[str]:
    meta = org.meta_json if isinstance(org.meta_json, dict) else {}
    channel_config = ((meta.get("insight_delivery") or {}).get(channel) or {})
    if channel_config.get("enabled") is False:
        return set()
    configured = channel_config.get("severities")
    if isinstance(configured, list) and configured:
        return {str(x) for x in configured}
    return {"critical", "warning"} if channel == "telegram_owner" else {"critical"}


def _format_insight_html(row: OperationalInsight, *, confirm_url: str | None = None) -> str:
    confidence = ""
    if row.confidence_score is not None:
        confidence = f"\n<b>Уверенность:</b> <code>{round(float(row.confidence_score or 0) * 100)}%</code>"
    action_line = ""
    if confirm_url:
        action_line = f'\n\n<a href="{html.escape(confirm_url, quote=True)}">✅ Подтвердить действие</a>'
    return (
        f"<b>{html.escape(row.title)}</b>\n"
        f"{html.escape(row.summary)}"
        f"{confidence}\n"
        f"<i>Источник: Intelligence OS</i>"
        f"{action_line}"
    )[:3900]


async def deliver_due_insights(
    db: AsyncSession,
    org_id: int,
    *,
    channel: str = "telegram_owner",
    limit: int = 20,
) -> int:
    org = await db.get(Organization, int(org_id))
    if org is None:
        return 0
    severities = _threshold_for_channel(org, channel)
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=7)
    rows = (
        await db.execute(
            select(OperationalInsight)
            .where(
                OperationalInsight.organization_id == int(org_id),
                OperationalInsight.status.in_(["new", "seen"]),
                OperationalInsight.severity.in_(list(severities)),
                OperationalInsight.created_at >= cutoff,
            )
            .order_by(OperationalInsight.created_at.desc())
            .limit(max(1, int(limit))),
        )
    ).scalars().all()

    sent_or_recorded = 0
    for insight in rows:
        existing = await db.scalar(
            select(InsightDelivery).where(
                InsightDelivery.organization_id == int(org_id),
                InsightDelivery.insight_id == int(insight.id),
                InsightDelivery.channel == channel,
            ),
        )
        if existing is not None:
            continue
        action_spec = build_proactive_action_from_insight(insight)
        proposal_id: str | None = None
        confirm_url: str | None = None
        if action_spec is not None:
            try:
                proposal = await propose_agent_action(
                    db,
                    organization_id=int(org_id),
                    action_type=str(action_spec["action_type"]),
                    title=str(action_spec.get("title") or insight.title),
                    summary=str(action_spec.get("summary") or insight.summary),
                    payload=action_spec.get("payload") if isinstance(action_spec.get("payload"), dict) else {},
                    source="insight_delivery",
                    source_insight_id=int(insight.id),
                    idempotency_key=f"insight:{int(insight.id)}:{action_spec['action_type']}",
                )
                proposal_id = proposal.id
                links = proposal_confirm_links(proposal)
                confirm_url = links.get("confirm_url")
            except Exception:
                logger.exception("proactive action proposal failed org=%s insight=%s", org_id, insight.id)

        delivery = InsightDelivery(
            organization_id=int(org_id),
            insight_id=int(insight.id),
            channel=channel,
            severity=insight.severity,
            status="pending",
            payload_json={
                "title": insight.title,
                "insight_type": insight.insight_type,
                "agent_action_proposal_id": proposal_id,
                "confirm_url": confirm_url,
            },
        )
        db.add(delivery)
        await db.flush()

        if channel != "telegram_owner":
            delivery.status = "skipped"
            delivery.error_text = "unsupported_channel"
        elif not (settings.telegram_bot_token or "").strip():
            delivery.status = "skipped"
            delivery.error_text = "telegram_not_configured"
        else:
            try:
                reply_markup = None
                if confirm_url:
                    reply_markup = {
                        "inline_keyboard": [[{"text": "✅ Подтвердить действие", "url": confirm_url}]],
                    }
                await send_ops_notification_html(
                    _format_insight_html(insight, confirm_url=confirm_url),
                    organization_id=int(org_id),
                    reply_markup=reply_markup,
                )
                delivery.status = "sent"
                delivery.sent_at = datetime.now(tz=timezone.utc)
            except Exception as exc:
                logger.exception("insight_delivery failed org=%s insight=%s", org_id, insight.id)
                delivery.status = "failed"
                delivery.error_text = str(exc)[:500]

        await emit_event(
            db,
            BusinessEvent(
                org_id=int(org_id),
                type="insight.delivered",
                actor="system",
                entity_type="operational_insight",
                entity_id=int(insight.id),
                payload={
                    "channel": channel,
                    "delivery_id": int(delivery.id),
                    "status": delivery.status,
                    "severity": insight.severity,
                    "agent_action_proposal_id": proposal_id,
                },
            ),
        )
        sent_or_recorded += 1
    if sent_or_recorded:
        await db.flush()
    return sent_or_recorded


async def list_insight_deliveries(db: AsyncSession, org_id: int, *, limit: int = 50) -> list[InsightDelivery]:
    return list(
        (
            await db.execute(
                select(InsightDelivery)
                .where(InsightDelivery.organization_id == int(org_id))
                .order_by(InsightDelivery.created_at.desc(), InsightDelivery.id.desc())
                .limit(max(1, int(limit))),
            )
        ).scalars().all(),
    )


async def mark_insight_delivery(
    db: AsyncSession,
    org_id: int,
    delivery_id: int,
    *,
    action: str,
) -> InsightDelivery | None:
    row = await db.get(InsightDelivery, int(delivery_id))
    if row is None or int(row.organization_id) != int(org_id):
        return None
    now = datetime.now(tz=timezone.utc)
    if action == "read":
        row.read_at = row.read_at or now
        row.status = "read"
    elif action == "dismiss":
        row.dismissed_at = row.dismissed_at or now
        row.status = "dismissed"
    elif action == "action_taken":
        row.action_taken_at = row.action_taken_at or now
        row.status = "action_taken"
    else:
        raise ValueError(f"Unsupported delivery action: {action}")
    await emit_event(
        db,
        BusinessEvent(
            org_id=int(org_id),
            type=f"insight.delivery.{action}",
            actor="operator",
            entity_type="insight_delivery",
            entity_id=int(row.id),
            payload={"insight_id": int(row.insight_id), "channel": row.channel, "status": row.status},
        ),
    )
    await db.flush()
    return row


def delivery_public(row: InsightDelivery) -> dict[str, Any]:
    return {
        "id": int(row.id),
        "insight_id": int(row.insight_id),
        "channel": row.channel,
        "status": row.status,
        "severity": row.severity,
        "error_text": row.error_text,
        "payload": row.payload_json or {},
        "sent_at": row.sent_at.isoformat() if row.sent_at else None,
        "read_at": row.read_at.isoformat() if row.read_at else None,
        "dismissed_at": row.dismissed_at.isoformat() if row.dismissed_at else None,
        "action_taken_at": row.action_taken_at.isoformat() if row.action_taken_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


async def insight_delivery_scheduled_tick(_ctx: dict[str, Any] | None = None) -> None:
    from sqlalchemy import select as _select

    from app.db.session import async_session_factory

    async with async_session_factory() as db:
        org_ids = list(
            (await db.execute(_select(Organization.id).where(Organization.is_active.is_(True)))).scalars().all(),
        )
    for org_id in org_ids:
        async with async_session_factory() as db:
            await deliver_due_insights(db, int(org_id))
            await db.commit()
