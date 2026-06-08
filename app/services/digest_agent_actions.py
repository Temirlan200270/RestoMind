"""Attach agent action proposals with confirm URLs to owner/daily digests."""

from __future__ import annotations

import html
import logging
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import BusinessRecommendation, OperationalInsight
from app.services.agent_actions import propose_agent_action, proposal_confirm_links
from app.services.insight_proactive_actions import (
    build_proactive_action_from_insight,
    build_proactive_action_from_recommendation,
)

logger = logging.getLogger(__name__)

_ACTIONABLE_SEVERITIES = ("critical", "warning")
_ACTIONABLE_REC_TYPES = ("upsell_pair",)


async def _fetch_actionable_insights(
    db: AsyncSession,
    org_id: int,
    *,
    start: datetime,
    end: datetime,
    limit: int = 3,
) -> list[OperationalInsight]:
    rows = (
        await db.execute(
            select(OperationalInsight)
            .where(
                OperationalInsight.organization_id == int(org_id),
                OperationalInsight.status.in_(["new", "seen"]),
                OperationalInsight.severity.in_(_ACTIONABLE_SEVERITIES),
                OperationalInsight.created_at >= start,
                OperationalInsight.created_at < end,
            )
            .order_by(OperationalInsight.created_at.desc())
            .limit(max(1, int(limit))),
        )
    ).scalars().all()
    return list(rows)


async def _fetch_actionable_recommendations(
    db: AsyncSession,
    org_id: int,
    *,
    start: datetime,
    end: datetime,
    limit: int = 2,
) -> list[BusinessRecommendation]:
    rows = (
        await db.execute(
            select(BusinessRecommendation)
            .where(
                BusinessRecommendation.organization_id == int(org_id),
                BusinessRecommendation.status.in_(["new", "viewed"]),
                BusinessRecommendation.recommendation_type.in_(_ACTIONABLE_REC_TYPES),
                BusinessRecommendation.created_at >= start,
                BusinessRecommendation.created_at < end,
            )
            .order_by(
                BusinessRecommendation.expected_impact_kzt.desc().nulls_last(),
                BusinessRecommendation.created_at.desc(),
            )
            .limit(max(1, int(limit))),
        )
    ).scalars().all()
    return list(rows)


async def _propose_for_spec(
    db: AsyncSession,
    org_id: int,
    *,
    action_spec: dict[str, Any],
    title: str,
    summary: str,
    source: str,
    source_insight_id: int | None,
    idempotency_key: str,
) -> dict[str, Any] | None:
    try:
        proposal = await propose_agent_action(
            db,
            organization_id=int(org_id),
            action_type=str(action_spec["action_type"]),
            title=str(action_spec.get("title") or title),
            summary=str(action_spec.get("summary") or summary),
            payload=action_spec.get("payload") if isinstance(action_spec.get("payload"), dict) else {},
            source=source,
            source_insight_id=source_insight_id,
            idempotency_key=idempotency_key,
        )
        links = proposal_confirm_links(proposal)
        return {
            "proposal_id": proposal.id,
            "action_type": proposal.action_type,
            "title": proposal.title,
            "confirm_url": links.get("confirm_url"),
            "source_insight_id": source_insight_id,
        }
    except Exception:
        logger.exception("digest agent action proposal failed org=%s key=%s", org_id, idempotency_key)
        return None


async def build_digest_agent_actions(
    db: AsyncSession,
    org_id: int,
    *,
    start: datetime,
    end: datetime,
    source: str,
    idempotency_prefix: str,
    insight_limit: int = 3,
    recommendation_limit: int = 2,
) -> list[dict[str, Any]]:
    """Propose agent actions for critical insights / actionable recommendations in a window."""
    items: list[dict[str, Any]] = []
    for insight in await _fetch_actionable_insights(
        db, org_id, start=start, end=end, limit=insight_limit,
    ):
        spec = build_proactive_action_from_insight(insight)
        if spec is None:
            continue
        row = await _propose_for_spec(
            db,
            org_id,
            action_spec=spec,
            title=insight.title,
            summary=insight.summary,
            source=source,
            source_insight_id=int(insight.id),
            idempotency_key=f"{idempotency_prefix}:insight:{insight.id}:{spec['action_type']}",
        )
        if row:
            row["insight_id"] = int(insight.id)
            items.append(row)

    for rec in await _fetch_actionable_recommendations(
        db, org_id, start=start, end=end, limit=recommendation_limit,
    ):
        spec = build_proactive_action_from_recommendation(rec)
        if spec is None:
            continue
        row = await _propose_for_spec(
            db,
            org_id,
            action_spec=spec,
            title=rec.title,
            summary=(rec.body or rec.title or "").strip(),
            source=source,
            source_insight_id=None,
            idempotency_key=f"{idempotency_prefix}:rec:{rec.id}:{spec['action_type']}",
        )
        if row:
            row["recommendation_id"] = int(rec.id)
            items.append(row)
    return items


def append_actions_to_digest_text(text: str, actions: list[dict[str, Any]]) -> str:
    if not actions:
        return text
    lines = ["", "Действия OS (подтвердите):"]
    for item in actions:
        line = f"• {item.get('title') or 'Действие'}"
        url = item.get("confirm_url")
        if url:
            line += f"\n  {url}"
        lines.append(line)
    base = text.rstrip()
    return (base + "\n" + "\n".join(lines)) if base else "\n".join(lines)


def append_actions_to_digest_html(html_body: str, actions: list[dict[str, Any]]) -> str:
    if not actions:
        return html_body
    parts = ["<br/><br/><b>Действия OS</b>"]
    for item in actions:
        title = html.escape(str(item.get("title") or "Действие"))
        url = item.get("confirm_url")
        if url:
            parts.append(
                f'<br/>• {title} — <a href="{html.escape(str(url), quote=True)}">✅ Подтвердить</a>',
            )
        else:
            parts.append(f"<br/>• {title}")
    return html_body + "".join(parts)


def digest_actions_reply_markup(actions: list[dict[str, Any]]) -> dict[str, Any] | None:
    keyboard: list[list[dict[str, str]]] = []
    for item in actions[:3]:
        url = item.get("confirm_url")
        if not url:
            continue
        label = str(item.get("title") or "Подтвердить")[:40]
        keyboard.append([{"text": f"✅ {label}", "url": str(url)}])
    if not keyboard:
        return None
    return {"inline_keyboard": keyboard}
