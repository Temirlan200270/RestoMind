"""Human-in-the-loop agent actions — propose, confirm, apply."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AgentActionProposal, Organization, UpsellRule


_ACTION_STATUSES = frozenset({"proposed", "confirmed", "applied", "rejected", "expired"})


def proposal_public(row: AgentActionProposal) -> dict[str, Any]:
    return {
        "id": row.id,
        "organization_id": row.organization_id,
        "action_type": row.action_type,
        "title": row.title,
        "summary": row.summary,
        "payload": row.payload_json or {},
        "status": row.status,
        "source": row.source,
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
) -> AgentActionProposal:
    row = AgentActionProposal(
        id=str(uuid.uuid4()),
        organization_id=int(organization_id),
        staff_user_id=staff_user_id,
        action_type=(action_type or "").strip(),
        title=(title or "").strip() or action_type,
        summary=(summary or "").strip(),
        payload_json=dict(payload or {}),
        status="proposed",
        source=(source or "hub").strip(),
    )
    db.add(row)
    await db.flush()
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


async def reject_agent_action(
    db: AsyncSession,
    *,
    proposal_id: str,
    organization_id: int,
) -> AgentActionProposal | None:
    row = await get_agent_action_proposal(db, proposal_id=proposal_id, organization_id=organization_id)
    if row is None or row.status not in {"proposed", "confirmed"}:
        return row
    row.status = "rejected"
    await db.flush()
    return row


async def _apply_force_close(db: AsyncSession, organization_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    org = await db.get(Organization, organization_id)
    if org is None:
        raise ValueError("organization_not_found")
    minutes = int(payload.get("minutes") or 0)
    reason = str(payload.get("reason") or "").strip()
    if minutes <= 0:
        org.force_closed_until = None
        org.force_closed_reason = ""
    else:
        org.force_closed_until = datetime.now(timezone.utc) + timedelta(minutes=minutes)
        org.force_closed_reason = reason
    await db.flush()
    fc_until = org.force_closed_until
    return {
        "force_closed": fc_until is not None,
        "force_closed_until": fc_until.isoformat() if fc_until else None,
        "force_closed_reason": org.force_closed_reason or "",
    }


async def _apply_upsell_rule_create(db: AsyncSession, organization_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    trigger_category = str(payload.get("trigger_category") or "").strip()
    suggest_category = str(payload.get("suggest_category") or "").strip()
    if not trigger_category or not suggest_category:
        raise ValueError("upsell_categories_required")
    phrase = str(payload.get("phrase_template") or "").strip() or (
        "К заказу отлично подойдёт {item_name} ({price} ₸). Добавить?"
    )
    row = UpsellRule(
        organization_id=organization_id,
        trigger_mode=str(payload.get("trigger_mode") or "missing_category").strip(),
        trigger_category=trigger_category,
        suggest_category=suggest_category,
        min_order_sum=float(payload.get("min_order_sum") or 0),
        max_order_sum=payload.get("max_order_sum"),
        phrase_template=phrase,
        sort_order=int(payload.get("sort_order") or 0),
        is_active=bool(payload.get("is_active", True)),
    )
    db.add(row)
    await db.flush()
    return {"upsell_rule_id": row.id, "trigger_category": trigger_category, "suggest_category": suggest_category}


async def _apply_iiko_write_staged(db: AsyncSession, organization_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    """Stage iiko write for manual review — no autonomous iiko API call yet."""
    return {
        "staged": True,
        "organization_id": organization_id,
        "operation": str(payload.get("operation") or "menu_price_update"),
        "items": payload.get("items") or [],
        "note": "Запрос сохранён. Автономная запись в iiko включится после guardrails (X1 freeze).",
    }


async def apply_agent_action(
    db: AsyncSession,
    row: AgentActionProposal,
) -> dict[str, Any]:
    payload = row.payload_json or {}
    action_type = row.action_type
    if action_type == "force_close":
        result = await _apply_force_close(db, row.organization_id, payload)
    elif action_type == "upsell_rule_create":
        result = await _apply_upsell_rule_create(db, row.organization_id, payload)
    elif action_type == "iiko_write_staged":
        result = await _apply_iiko_write_staged(db, row.organization_id, payload)
    else:
        raise ValueError(f"unsupported_action_type:{action_type}")
    now = datetime.now(timezone.utc)
    row.status = "applied"
    row.confirmed_at = row.confirmed_at or now
    row.applied_at = now
    await db.flush()
    return result


async def confirm_agent_action(
    db: AsyncSession,
    *,
    proposal_id: str,
    organization_id: int,
    staff_user_id: int | None = None,
) -> dict[str, Any]:
    row = await get_agent_action_proposal(db, proposal_id=proposal_id, organization_id=organization_id)
    if row is None:
        raise LookupError("proposal_not_found")
    if row.status == "applied":
        return {"ok": True, "proposal": proposal_public(row), "result": row.payload_json.get("_apply_result") or {}}
    if row.status != "proposed":
        raise ValueError(f"proposal_not_confirmable:{row.status}")
    if staff_user_id is not None and row.staff_user_id is None:
        row.staff_user_id = staff_user_id
    row.confirmed_at = datetime.now(timezone.utc)
    row.status = "confirmed"
    result = await apply_agent_action(db, row)
    payload = dict(row.payload_json or {})
    payload["_apply_result"] = result
    row.payload_json = payload
    await db.flush()
    return {"ok": True, "proposal": proposal_public(row), "result": result}


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
