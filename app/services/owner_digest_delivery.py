"""Owner Intelligence weekly digest — preview, send pipeline, audit log."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import Organization, RecommendationOutcome, SystemEvent
from app.db.session import redis_client
from app.integrations.telegram import send_ops_notification_html
from app.services.digest_agent_actions import (
    append_actions_to_digest_html,
    append_actions_to_digest_text,
    build_digest_agent_actions,
    digest_actions_reply_markup,
)
from app.services.owner_intelligence_digest import build_owner_intelligence_weekly_digest
from app.services.owner_roi import _bounds_to_utc_naive_pair, _previous_week_bounds_local, build_week_digest_payload
from app.services.system_events import BusinessEvent, emit_event

logger = logging.getLogger(__name__)

MANUAL_COOLDOWN_TTL_SEC = 30 * 60
CRON_DEDUPE_TTL_SEC = 8 * 24 * 3600


def _zoneinfo_for_org(org: Organization) -> ZoneInfo:
    tz_name = (org.timezone or "UTC").strip() or "UTC"
    try:
        return ZoneInfo(tz_name)
    except Exception:
        logger.warning("owner_digest: неверная TZ организации %s id=%s", tz_name, org.id)
        return ZoneInfo("UTC")


def org_local_now(org: Organization) -> datetime:
    """Текущее локальное время организации (для cron-окна понедельника)."""
    return datetime.now(timezone.utc).astimezone(_zoneinfo_for_org(org))


def is_cron_send_window(local: datetime) -> bool:
    """Понедельник 10:00–10:44 в TZ организации."""
    return local.weekday() == 0 and local.hour == 10 and local.minute < 45


def cron_dedupe_key(org_id: int, local: datetime) -> str:
    iso_year, iso_week, _ = local.isocalendar()
    return f"owner_weekly_digest:{org_id}:{iso_year}:W{iso_week:02d}"


def manual_cooldown_key(org_id: int) -> str:
    return f"owner_digest:manual:{org_id}"


def text_to_html(text_plain: str) -> str:
    safe = (
        text_plain.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    return "<b>RestoMind — неделя</b><br/><br/>" + safe.replace("\n", "<br/>")


async def _redis_set_once(key: str, ttl_sec: int) -> bool:
    """True если ключ установлен впервые."""
    try:
        ok = await redis_client.set(key, "1", nx=True, ex=ttl_sec)  # type: ignore[call-arg]
        return bool(ok)
    except TypeError:
        prev = await redis_client.get(key)
        if prev:
            return False
        await redis_client.set(key, "1")
        return True


async def _build_digest_payload(
    db: AsyncSession,
    org: Organization,
    *,
    period: str = "prev_week",
) -> dict[str, Any] | None:
    if period != "prev_week":
        logger.debug("owner_digest: period=%s не поддержан, используем prev_week", period)
    payload = await build_owner_intelligence_weekly_digest(db, org)
    if not payload:
        payload = await build_week_digest_payload(db, org)
    if payload:
        since = datetime.now(timezone.utc) - timedelta(days=14)
        roi_rows = (
            await db.execute(
                select(RecommendationOutcome)
                .where(
                    RecommendationOutcome.organization_id == int(org.id),
                    RecommendationOutcome.status == "measured",
                    RecommendationOutcome.measured_at >= since,
                )
                .order_by(RecommendationOutcome.measured_at.desc(), RecommendationOutcome.id.desc())
                .limit(3),
            )
        ).scalars().all()
        if roi_rows:
            text = str(payload.get("text") or "").strip()
            lines = ["ROI по рекомендациям:"]
            total_money = 0.0
            for row in roi_rows:
                money = float(row.realized_money or 0)
                total_money += money
                confidence = float(row.data_quality_confidence or 0)
                suffix = "низкая уверенность данных" if confidence < 0.7 else f"уверенность {confidence:.0%}"
                lines.append(f"- {row.recommendation_type or row.metric}: {money:+.0f} ({suffix})")
            payload["text"] = (text + "\n\n" if text else "") + "\n".join(lines)
            metrics = dict(payload.get("metrics") or {})
            metrics["recommendation_roi_realized_money"] = round(total_money, 2)
            metrics["recommendation_roi_items"] = len(roi_rows)
            payload["metrics"] = metrics
    return payload


async def preview_weekly_digest(
    db: AsyncSession,
    org_id: int,
    *,
    period: str = "prev_week",
) -> dict[str, Any]:
    org = await db.get(Organization, int(org_id))
    if org is None:
        return {"ok": False, "error": "organization_not_found", "text": "", "metrics": {}, "html": ""}

    payload = await _build_digest_payload(db, org, period=period)
    if not payload:
        return {
            "ok": True,
            "organization_id": int(org_id),
            "period": period,
            "text": "",
            "metrics": {},
            "html": "",
            "empty": True,
        }

    text = str(payload.get("text") or "").strip()
    metrics = dict(payload.get("metrics") or {})
    return {
        "ok": True,
        "organization_id": int(org_id),
        "period": period,
        "text": text,
        "metrics": metrics,
        "html": text_to_html(text) if text else "",
        "empty": not bool(text),
    }


async def list_digest_history(
    db: AsyncSession,
    org_id: int,
    *,
    limit: int = 10,
) -> list[dict[str, Any]]:
    cap = max(1, min(int(limit), 50))
    rows = (
        await db.execute(
            select(SystemEvent)
            .where(
                SystemEvent.organization_id == int(org_id),
                SystemEvent.event_type.in_(("owner_digest.sent", "owner_digest.failed")),
            )
            .order_by(SystemEvent.created_at.desc())
            .limit(cap),
        )
    ).scalars().all()

    items: list[dict[str, Any]] = []
    for ev in rows:
        payload = dict(ev.payload_json or {})
        items.append(
            {
                "id": int(ev.id),
                "event_type": ev.event_type,
                "created_at": ev.created_at.isoformat() if ev.created_at else None,
                "channel": payload.get("channel"),
                "triggered_by": payload.get("triggered_by"),
                "period": payload.get("period"),
                "success": ev.event_type == "owner_digest.sent",
                "skip_reason": payload.get("skip_reason"),
                "error": payload.get("error"),
            },
        )
    return items


async def send_weekly_digest(
    db: AsyncSession,
    org_id: int,
    *,
    force: bool = False,
    channel: str = "telegram",
    triggered_by: Literal["cron", "admin"] = "cron",
) -> dict[str, Any]:
    org = await db.get(Organization, int(org_id))
    if org is None:
        return {"ok": False, "sent": False, "error": "organization_not_found"}

    local = org_local_now(org)
    period = "prev_week"

    if triggered_by == "cron":
        if not is_cron_send_window(local):
            return {
                "ok": True,
                "sent": False,
                "skipped": True,
                "skip_reason": "outside_window",
                "triggered_by": triggered_by,
            }
        dedupe_key = cron_dedupe_key(int(org_id), local)
        if not await _redis_set_once(dedupe_key, CRON_DEDUPE_TTL_SEC):
            return {
                "ok": True,
                "sent": False,
                "skipped": True,
                "skip_reason": "already_sent",
                "triggered_by": triggered_by,
            }
    elif triggered_by == "admin" and not force:
        cooldown_key = manual_cooldown_key(int(org_id))
        if not await _redis_set_once(cooldown_key, MANUAL_COOLDOWN_TTL_SEC):
            return {
                "ok": True,
                "sent": False,
                "skipped": True,
                "skip_reason": "manual_cooldown",
                "triggered_by": triggered_by,
            }

    payload = await _build_digest_payload(db, org, period=period)
    if not payload:
        return {
            "ok": False,
            "sent": False,
            "skipped": True,
            "skip_reason": "empty_payload",
            "triggered_by": triggered_by,
        }

    base_text = str(payload.get("text") or "").strip()
    if not base_text:
        return {
            "ok": False,
            "sent": False,
            "skipped": True,
            "skip_reason": "empty_text",
            "triggered_by": triggered_by,
        }

    lo_l, hi_l = _previous_week_bounds_local(org.timezone or "UTC")
    start_utc, end_utc = _bounds_to_utc_naive_pair(lo_l, hi_l)
    iso_year, iso_week, _ = local.isocalendar()
    agent_actions = await build_digest_agent_actions(
        db,
        int(org_id),
        start=start_utc,
        end=end_utc,
        source="owner_weekly_digest",
        idempotency_prefix=f"digest:weekly:{iso_year}:W{iso_week:02d}:{org_id}",
    )
    text_plain = append_actions_to_digest_text(base_text, agent_actions) if agent_actions else base_text
    if agent_actions:
        payload["agent_actions"] = agent_actions

    if channel != "telegram":
        return {
            "ok": False,
            "sent": False,
            "error": "unsupported_channel",
            "triggered_by": triggered_by,
        }

    if not settings.telegram_bot_token.strip():
        await _emit_digest_event(
            db,
            org_id=int(org_id),
            success=False,
            triggered_by=triggered_by,
            channel=channel,
            period=period,
            metrics=payload.get("metrics") or {},
            error="telegram_not_configured",
        )
        return {
            "ok": False,
            "sent": False,
            "error": "telegram_not_configured",
            "triggered_by": triggered_by,
        }

    html = text_to_html(base_text)
    html = append_actions_to_digest_html(html, agent_actions)
    reply_markup = digest_actions_reply_markup(agent_actions)
    actor = "system" if triggered_by == "cron" else "operator"

    try:
        await send_ops_notification_html(
            html,
            organization_id=int(org_id),
            reply_markup=reply_markup,
        )
    except Exception as exc:
        logger.exception("owner_digest: send failed org=%s: %s", org_id, exc)
        await _emit_digest_event(
            db,
            org_id=int(org_id),
            success=False,
            triggered_by=triggered_by,
            channel=channel,
            period=period,
            metrics=payload.get("metrics") or {},
            error=str(exc)[:500],
        )
        return {
            "ok": False,
            "sent": False,
            "error": "send_failed",
            "triggered_by": triggered_by,
        }

    email = (settings.owner_digest_email or "").strip()
    if email:
        logger.info(
            "owner_digest email stub: would send weekly digest to %s org=%s",
            email,
            org_id,
        )

    if triggered_by == "admin":
        await _redis_set_once(manual_cooldown_key(int(org_id)), MANUAL_COOLDOWN_TTL_SEC)

    await _emit_digest_event(
        db,
        org_id=int(org_id),
        success=True,
        triggered_by=triggered_by,
        channel=channel,
        period=period,
        metrics={
            **(payload.get("metrics") or {}),
            "agent_actions_count": len(agent_actions),
        },
        agent_actions=agent_actions,
    )

    return {
        "ok": True,
        "sent": True,
        "channel": channel,
        "triggered_by": triggered_by,
        "period": period,
        "text_preview": text_plain[:280],
        "metrics": payload.get("metrics") or {},
        "agent_actions": agent_actions,
        "actor": actor,
    }


async def _emit_digest_event(
    db: AsyncSession,
    *,
    org_id: int,
    success: bool,
    triggered_by: str,
    channel: str,
    period: str,
    metrics: dict[str, Any],
    error: str | None = None,
    skip_reason: str | None = None,
    agent_actions: list[dict[str, Any]] | None = None,
) -> None:
    event_type = "owner_digest.sent" if success else "owner_digest.failed"
    payload: dict[str, Any] = {
        "channel": channel,
        "triggered_by": triggered_by,
        "period": period,
        "metrics": metrics,
    }
    if agent_actions:
        payload["agent_actions"] = [
            {
                "proposal_id": row.get("proposal_id"),
                "action_type": row.get("action_type"),
                "confirm_url": row.get("confirm_url"),
            }
            for row in agent_actions
        ]
    if error:
        payload["error"] = error
    if skip_reason:
        payload["skip_reason"] = skip_reason

    await emit_event(
        db,
        BusinessEvent(
            org_id=int(org_id),
            type=event_type,
            actor="system" if triggered_by == "cron" else "operator",
            payload=payload,
        ),
    )
