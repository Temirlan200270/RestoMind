"""Human-in-the-loop agent actions — propose, preview, confirm, apply."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AgentActionProposal, OperationalInsight, RecommendationOutcome, SystemEvent
from app.services.agent_action_tokens import build_confirm_url, create_agent_action_confirm_token
from app.services.agent_commands import (
    command_requires_preview,
    get_agent_command,
    staff_role_allows,
    validate_agent_command,
)
from app.services.agent_commands.base import CommandContext
from app.services.system_events import BusinessEvent, emit_event


_ACTION_STATUSES = frozenset({"proposed", "previewed", "confirmed", "applied", "rejected", "expired"})


async def _emit_agent_action_event(
    db: AsyncSession,
    row: AgentActionProposal,
    event_type: str,
    *,
    actor: str = "ai",
    extra: dict[str, Any] | None = None,
) -> None:
    payload = row.payload_json or {}
    await emit_event(
        db,
        BusinessEvent(
            org_id=int(row.organization_id),
            type=event_type,
            actor=actor,
            entity_type="agent_action_proposal",
            entity_id=row.id,
            payload={
                "proposal_id": row.id,
                "action_type": row.action_type,
                "status": row.status,
                "source": row.source,
                "staff_user_id": row.staff_user_id,
                "trace_id": row.trace_id,
                "source_insight_id": row.source_insight_id,
                "command": payload.get("_command") or {},
                **(extra or {}),
            },
        ),
    )


def proposal_public(row: AgentActionProposal) -> dict[str, Any]:
    payload = row.payload_json or {}
    command = payload.get("_command") or {}
    if not command:
        try:
            from app.services.agent_commands.base import command_public

            command = command_public(get_agent_command(row.action_type).spec)
        except ValueError:
            command = {}
    return {
        "id": row.id,
        "organization_id": row.organization_id,
        "action_type": row.action_type,
        "title": row.title,
        "summary": row.summary,
        "payload": payload,
        "command": command,
        "status": row.status,
        "source": row.source,
        "preview": row.preview_json,
        "previewed_at": row.previewed_at.isoformat() if row.previewed_at else None,
        "source_insight_id": row.source_insight_id,
        "source_snapshot_id": row.source_snapshot_id,
        "source_conversation_id": row.source_conversation_id,
        "trace_id": row.trace_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "confirmed_at": row.confirmed_at.isoformat() if row.confirmed_at else None,
        "applied_at": row.applied_at.isoformat() if row.applied_at else None,
        "confirm_required": True,
    }


async def propose_agent_action(
    db: AsyncSession,
    *,
    organization_id: int,
    action_type: str,
    title: str,
    summary: str,
    payload: dict[str, Any] | None = None,
    staff_user_id: int | None = None,
    source: str = "hub",
    source_insight_id: int | None = None,
    source_snapshot_id: int | None = None,
    source_conversation_id: int | None = None,
    trace_id: str | None = None,
    idempotency_key: str | None = None,
) -> AgentActionProposal:
    action_type_norm = (action_type or "").strip()
    command_payload = validate_agent_command(action_type_norm, payload)
    idem = (idempotency_key or "").strip()[:120] or None
    if idem:
        existing = await db.scalar(
            select(AgentActionProposal)
            .where(
                AgentActionProposal.organization_id == int(organization_id),
                AgentActionProposal.idempotency_key == idem,
                AgentActionProposal.status.notin_(("rejected", "expired")),
            )
            .order_by(AgentActionProposal.created_at.desc())
            .limit(1),
        )
        if existing is not None:
            return existing
    row = AgentActionProposal(
        id=str(uuid.uuid4()),
        organization_id=int(organization_id),
        staff_user_id=staff_user_id,
        action_type=action_type_norm,
        title=(title or "").strip() or action_type_norm,
        summary=(summary or "").strip(),
        payload_json=command_payload,
        status="proposed",
        source=(source or "hub").strip(),
        source_insight_id=int(source_insight_id) if source_insight_id is not None else None,
        source_snapshot_id=int(source_snapshot_id) if source_snapshot_id is not None else None,
        source_conversation_id=int(source_conversation_id) if source_conversation_id is not None else None,
        trace_id=(trace_id or "").strip()[:64] or None,
        idempotency_key=idem,
    )
    db.add(row)
    await db.flush()
    await _emit_agent_action_event(db, row, "agent_action.proposed", actor="ai")
    return row


async def get_agent_action_proposal(
    db: AsyncSession,
    *,
    proposal_id: str,
    organization_id: int,
) -> AgentActionProposal | None:
    return await db.scalar(
        select(AgentActionProposal).where(
            AgentActionProposal.id == proposal_id,
            AgentActionProposal.organization_id == organization_id,
        ),
    )


async def preview_agent_action(
    db: AsyncSession,
    *,
    proposal_id: str,
    organization_id: int,
    staff_role: str = "admin",
) -> dict[str, Any]:
    row = await get_agent_action_proposal(db, proposal_id=proposal_id, organization_id=organization_id)
    if row is None:
        raise LookupError("proposal_not_found")
    if row.status not in {"proposed", "previewed"}:
        raise ValueError(f"proposal_not_previewable:{row.status}")

    cmd = get_agent_command(row.action_type)
    if not staff_role_allows(cmd.spec, staff_role):
        raise PermissionError("role_not_allowed")

    ctx = CommandContext(organization_id=int(row.organization_id), payload=row.payload_json or {}, staff_role=staff_role)
    preview = await cmd.preview(db, ctx)
    now = datetime.now(timezone.utc)
    row.preview_json = preview
    row.previewed_at = now
    row.status = "previewed"
    payload = dict(row.payload_json or {})
    payload["_preview"] = preview
    row.payload_json = payload
    await db.flush()
    await _emit_agent_action_event(db, row, "agent_action.previewed", actor="operator", extra={"preview": preview})
    return {"ok": True, "proposal": proposal_public(row), "preview": preview}


async def reject_agent_action(
    db: AsyncSession,
    *,
    proposal_id: str,
    organization_id: int,
) -> AgentActionProposal | None:
    row = await get_agent_action_proposal(db, proposal_id=proposal_id, organization_id=organization_id)
    if row is None or row.status not in {"proposed", "previewed", "confirmed"}:
        return row
    row.status = "rejected"
    await db.flush()
    await _emit_agent_action_event(db, row, "agent_action.rejected", actor="operator")
    return row


async def apply_agent_action(
    db: AsyncSession,
    row: AgentActionProposal,
    *,
    staff_role: str = "admin",
) -> dict[str, Any]:
    cmd = get_agent_command(row.action_type)
    if not staff_role_allows(cmd.spec, staff_role):
        raise PermissionError("role_not_allowed")

    payload = row.payload_json or {}
    row.payload_json = validate_agent_command(row.action_type, payload)
    payload = row.payload_json or {}
    if row.preview_json:
        payload["_preview"] = row.preview_json
    if row.idempotency_key:
        payload["_idempotency_key"] = row.idempotency_key

    ctx = CommandContext(organization_id=int(row.organization_id), payload=payload, staff_role=staff_role)
    result = await cmd.apply(db, ctx)
    now = datetime.now(timezone.utc)
    row.status = "applied"
    row.confirmed_at = row.confirmed_at or now
    row.applied_at = now
    stored = dict(row.payload_json or {})
    stored["_apply_result"] = result
    row.payload_json = stored
    await db.flush()
    await _emit_agent_action_event(
        db,
        row,
        "agent_action.applied",
        actor="system",
        extra={"result": result, "audit": cmd.audit_payload(payload)},
    )
    return result


def _ensure_preview_gate(row: AgentActionProposal) -> None:
    cmd = get_agent_command(row.action_type)
    if command_requires_preview(cmd.spec) and row.status != "previewed":
        raise ValueError("preview_required")


async def confirm_agent_action(
    db: AsyncSession,
    *,
    proposal_id: str,
    organization_id: int,
    staff_user_id: int | None = None,
    staff_role: str = "admin",
    source_channel: str = "admin",
) -> dict[str, Any]:
    row = await get_agent_action_proposal(db, proposal_id=proposal_id, organization_id=organization_id)
    if row is None:
        raise LookupError("proposal_not_found")
    if row.status == "applied":
        return {"ok": True, "proposal": proposal_public(row), "result": (row.payload_json or {}).get("_apply_result") or {}}
    if row.status not in {"proposed", "previewed"}:
        raise ValueError(f"proposal_not_confirmable:{row.status}")

    _ensure_preview_gate(row)

    if staff_user_id is not None and row.staff_user_id is None:
        row.staff_user_id = staff_user_id
    row.confirmed_at = datetime.now(timezone.utc)
    row.status = "confirmed"
    event_type = "agent_action.confirmed_from_telegram" if source_channel == "telegram" else "agent_action.confirmed"
    await _emit_agent_action_event(db, row, event_type, actor="operator", extra={"channel": source_channel})
    result = await apply_agent_action(db, row, staff_role=staff_role)
    return {"ok": True, "proposal": proposal_public(row), "result": result}


async def confirm_agent_action_by_token(
    db: AsyncSession,
    *,
    proposal_id: str,
    organization_id: int,
) -> dict[str, Any]:
    row = await get_agent_action_proposal(db, proposal_id=proposal_id, organization_id=organization_id)
    if row is None:
        raise LookupError("proposal_not_found")
    await _emit_agent_action_event(db, row, "agent_action.confirm_link_opened", actor="operator", extra={"channel": "telegram"})
    if row.status == "applied":
        return {"ok": True, "already_applied": True, "proposal": proposal_public(row)}
    if row.status == "proposed":
        await preview_agent_action(db, proposal_id=proposal_id, organization_id=organization_id, staff_role="admin")
        row = await get_agent_action_proposal(db, proposal_id=proposal_id, organization_id=organization_id)
        if row is None:
            raise LookupError("proposal_not_found")
    return await confirm_agent_action(
        db,
        proposal_id=proposal_id,
        organization_id=organization_id,
        staff_role="admin",
        source_channel="telegram",
    )


async def build_action_chain(
    db: AsyncSession,
    *,
    proposal_id: str,
    organization_id: int,
) -> dict[str, Any]:
    row = await get_agent_action_proposal(db, proposal_id=proposal_id, organization_id=organization_id)
    if row is None:
        raise LookupError("proposal_not_found")

    insight_block: dict[str, Any] | None = None
    if row.source_insight_id is not None:
        insight = await db.get(OperationalInsight, int(row.source_insight_id))
        if insight is not None and int(insight.organization_id) == int(organization_id):
            insight_block = {
                "id": insight.id,
                "insight_type": insight.insight_type,
                "severity": insight.severity,
                "title": insight.title,
                "summary": insight.summary,
                "evidence": insight.evidence_json or {},
                "confidence_score": float(insight.confidence_score or 0) if insight.confidence_score is not None else None,
            }

    events = (
        await db.execute(
            select(SystemEvent)
            .where(
                SystemEvent.organization_id == int(organization_id),
                SystemEvent.entity_type == "agent_action_proposal",
                SystemEvent.entity_id == row.id,
            )
            .order_by(SystemEvent.id.asc())
            .limit(50),
        )
    ).scalars().all()

    outcomes = (
        await db.execute(
            select(RecommendationOutcome)
            .where(
                RecommendationOutcome.organization_id == int(organization_id),
                RecommendationOutcome.action_id == row.id,
            )
            .limit(5),
        )
    ).scalars().all()

    lineage = {
        "metric": insight_block,
        "insight": insight_block,
        "evidence": (insight_block or {}).get("evidence") or row.preview_json or {},
        "command": proposal_public(row),
        "audit_events": [
            {
                "type": e.event_type,
                "source": e.source,
                "created_at": e.created_at.isoformat() if e.created_at else None,
                "payload": e.payload_json or {},
            }
            for e in events
        ],
        "outcomes": [
            {
                "id": o.id,
                "status": o.status,
                "metric": o.metric,
                "recommendation_type": o.recommendation_type,
            }
            for o in outcomes
        ],
    }
    return {"ok": True, "proposal_id": row.id, "lineage": lineage, "chain": lineage}


def proposal_confirm_links(row: AgentActionProposal) -> dict[str, str | None]:
    token = create_agent_action_confirm_token(proposal_id=row.id, organization_id=int(row.organization_id))
    url = build_confirm_url(proposal_id=row.id, organization_id=int(row.organization_id))
    return {"confirm_token": token, "confirm_url": url}


_FORCE_CLOSE_RE = re.compile(
    r"(?:закр(?:ой|ыть|ойте)|пауз[ау]|force[- ]?close).{0,40}?(\d{1,3})\s*(?:мин|minute|m\b)",
    re.IGNORECASE,
)
_UPSELL_RE = re.compile(
    r"(?:допродаж|upsell).{0,60}?(?:если|when).{0,20}?([^\n,]+?).{0,20}?(?:предлаг|suggest).{0,20}?([^\n,.]+)",
    re.IGNORECASE,
)
_IIKO_PRICE_RE = re.compile(
    r"(?:iiko|ико).{0,40}?(?:цен|price).{0,40}?([^\n.]+)",
    re.IGNORECASE,
)


def detect_conversational_action_proposals(question: str) -> list[dict[str, Any]]:
    """Lightweight intent detection for human-in-the-loop config actions."""
    q = (question or "").strip()
    if not q:
        return []
    proposals: list[dict[str, Any]] = []

    force_match = _FORCE_CLOSE_RE.search(q)
    if force_match:
        minutes = max(1, min(480, int(force_match.group(1))))
        proposals.append(
            {
                "action_type": "force_close",
                "title": f"Закрыть ресторан на {minutes} мин",
                "summary": "Экстренная пауза приёма заказов до подтверждения владельцем.",
                "payload": {"minutes": minutes, "reason": "Запрос из чата Executive Hub"},
            },
        )

    upsell_match = _UPSELL_RE.search(q)
    if upsell_match:
        trigger = upsell_match.group(1).strip()
        suggest = upsell_match.group(2).strip()
        if trigger and suggest:
            proposals.append(
                {
                    "action_type": "upsell_rule_create",
                    "title": f"Допродажа: {trigger} → {suggest}",
                    "summary": "Создать правило upsell после подтверждения.",
                    "payload": {
                        "trigger_category": trigger,
                        "suggest_category": suggest,
                        "trigger_mode": "missing_category",
                    },
                },
            )

    iiko_match = _IIKO_PRICE_RE.search(q)
    if iiko_match:
        target = iiko_match.group(1).strip()
        proposals.append(
            {
                "action_type": "iiko_write_staged",
                "title": "Изменение цен в iiko (staged)",
                "summary": f"Подготовить запрос на обновление цен: {target}",
                "payload": {"operation": "menu_price_update", "items": [{"label": target}]},
            },
        )

    return proposals
