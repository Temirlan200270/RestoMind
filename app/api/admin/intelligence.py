"""Restaurant Intelligence and Digital Twin admin API."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin.deps import (
    _session_is_superadmin,
    _session_staff_user,
    admin_org_from_session,
    require_admin_session_active,
    require_staff_admin,
    require_staff_manager_or_admin,
)
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified
from app.db.models import (
    AIContextSnapshot,
    BusinessRecommendation,
    ExternalReview,
    InventoryStockSnapshot,
    MenuItem,
    OperationalInsight,
    Organization,
    StaffOnboardingSession,
    SupplyPurchaseDraft,
)
from app.db.session import get_db
from app.services.analytics_consumer import get_event_stats, get_today_event_summary
from app.services.owner_dashboard import (
    build_autopilot_pricing,
    build_cancellation_forecast,
    build_demand_forecast,
    build_overload_risk,
    build_stock_alerts_from_inventory,
    build_stock_alerts_stub,
    build_week_forecast,
    fetch_daily_revenue_history,
    fetch_daily_revenue_history_from_events,
)
from app.services.organization_memory import (
    list_memory_events,
    memory_event_public,
    record_memory_event,
)
from app.services.insight_delivery import (
    delivery_public,
    list_insight_deliveries,
    mark_insight_delivery,
)
from app.services.copilot.business_questions import questions_for_role
from app.services.executive_hub import build_executive_hub_payload
from app.services.intelligence import (
    SimulationInput,
    answer_intelligence_query,
    build_state_snapshot,
    list_insights,
    revenue_orders_summary,
    simulate_operator_capacity,
)
from app.services.inventory_snapshots import (
    InventorySnapshotUpsertItem,
    upsert_inventory_snapshots as upsert_inventory_snapshot_rows,
)
from app.services.recommendation_outcomes import (
    list_recommendation_outcomes,
    recommendation_outcome_public,
)
from app.services.tenant_scope import allowed_location_ids_for_staff

router = APIRouter(
    prefix="/admin/intelligence",
    tags=["Restaurant Intelligence"],
    dependencies=[Depends(require_admin_session_active)],
)


async def _location_scope_for_request(
    request: Request,
    db: AsyncSession,
    org_id: int,
    location_id: int | None,
) -> tuple[set[int] | None, bool]:
    staff = await _session_staff_user(request, db)
    is_super = await _session_is_superadmin(request, db)
    allowed = await allowed_location_ids_for_staff(
        db,
        staff=staff,
        org_id=org_id,
        is_superadmin=is_super,
    )
    if location_id is not None and allowed is not None and int(location_id) not in allowed:
        raise HTTPException(status_code=403, detail="Location is not allowed")
    return allowed, bool(location_id is not None or allowed is not None)


class IntelligenceQueryBody(BaseModel):
    question: str = Field(..., min_length=2, max_length=1000)
    conversation_id: int | None = None


class AgentActionProposeBody(BaseModel):
    action_type: str = Field(..., min_length=2, max_length=64)
    title: str = Field(default="", max_length=255)
    summary: str = Field(default="", max_length=2000)
    payload: dict[str, Any] = Field(default_factory=dict)
    source: str = Field(default="hub", max_length=32)
    source_insight_id: int | None = None
    source_snapshot_id: int | None = None
    source_conversation_id: int | None = None
    trace_id: str | None = Field(default=None, max_length=64)


class InsightPatchBody(BaseModel):
    status: str = Field(..., pattern="^(new|seen|resolved|dismissed)$")
    was_useful: bool | None = Field(default=None, description="Оператор отметил инсайт полезным (true) или нет (false)")
    notes: str | None = Field(default=None, max_length=500, description="Заметка оператора при закрытии")


class SimulationBody(BaseModel):
    orders_per_hour: float = Field(..., ge=0, le=500)
    operators: int = Field(..., ge=1, le=100)
    avg_check: float = Field(..., ge=0, le=10_000_000)
    base_cancel_rate_pct: float = Field(default=5.0, ge=0, le=100)


class MemoryNoteBody(BaseModel):
    summary: str = Field(..., min_length=1, max_length=4000)
    event_type: str = Field(default="manager_note", max_length=80)
    entity_type: str | None = Field(default=None, max_length=80)
    entity_id: str | None = Field(default=None, max_length=120)
    event_date: date | None = None
    payload: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_business_memory_type(self) -> "MemoryNoteBody":
        allowed = {
            "manager_note",
            "manual_note",
            "campaign",
            "supplier_change",
            "price_change",
            "menu_change",
            "cost_change",
            "staff_change",
        }
        if self.event_type not in allowed:
            raise ValueError("Unsupported memory event type")
        return self


class SnapshotFeedbackBody(BaseModel):
    reason: str = Field(..., min_length=2, max_length=1000)
    correction: str | None = Field(default=None, max_length=2000)
    question: str | None = Field(default=None, max_length=1000)
    expected_behavior: str | None = Field(default=None, max_length=2000)


class InsightDeliveryActionBody(BaseModel):
    action: str = Field(..., pattern="^(read|dismiss|action_taken)$")


class InsightDeliverySettingsBody(BaseModel):
    telegram_owner_severities: list[str] = Field(default_factory=lambda: ["critical", "warning"])
    daily_digest_enabled: bool = True
    weekly_digest_enabled: bool = True
    inbox_enabled: bool = True


def _default_delivery_settings() -> dict[str, Any]:
    return {
        "telegram_owner": {"severities": ["critical", "warning"], "enabled": True},
        "daily_digest": {"enabled": True},
        "weekly_digest": {"enabled": True},
        "inbox": {"enabled": True},
    }


def _delivery_settings_public(org: Organization) -> dict[str, Any]:
    configured = ((org.meta_json or {}).get("insight_delivery") or {})
    settings = _default_delivery_settings()
    for key, value in configured.items():
        if isinstance(value, dict):
            settings.setdefault(key, {}).update(value)
    return settings


async def _copilot_role_from_request(request: Request, db: AsyncSession, org_id: int) -> str:
    staff = await _session_staff_user(request, db)
    raw = str(getattr(staff, "role", "") or "").strip().lower()
    if raw == "manager":
        return "manager"
    if raw == "operator":
        return "manager"
    if getattr(request, "session", {}).get("is_network"):
        return "network"
    org = await db.get(Organization, int(org_id))
    if org is not None and getattr(org, "tenant_id", None):
        return "network"
    return "owner"


def _insight_public(row: OperationalInsight) -> dict:
    payload = row.payload_json or {}
    return {
        "id": row.id,
        "insight_type": row.insight_type,
        "severity": row.severity,
        "title": row.title,
        "summary": row.summary,
        "payload": payload,
        "confidence_score": round(float(row.confidence_score or 0), 4) if row.confidence_score is not None else payload.get("confidence_score"),
        "evidence": row.evidence_json or payload.get("evidence") or {},
        "drilldown": row.drilldown_json or payload.get("drilldown") or {},
        "cause_hypotheses": payload.get("cause_hypotheses") or [],
        "recommended_actions": payload.get("recommended_actions") or [],
        "weekday_baseline": payload.get("weekday_baseline"),
        "status": row.status,
        "was_useful": getattr(row, "was_useful", None),
        "notes": getattr(row, "notes", None),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "resolved_at": row.resolved_at.isoformat() if row.resolved_at else None,
    }


@router.get("/overview")
async def intelligence_overview(
    request: Request,
    db: AsyncSession = Depends(get_db),
    location_id: Annotated[int | None, Query(ge=1)] = None,
) -> dict:
    org_id = admin_org_from_session(request)
    allowed_location_ids, location_scoped = await _location_scope_for_request(
        request,
        db,
        org_id,
        location_id,
    )
    summary = await revenue_orders_summary(
        db,
        org_id,
        "today",
        location_id=location_id,
        allowed_location_ids=allowed_location_ids,
    )
    insights = await list_insights(db, org_id, limit=10)
    snapshot = await build_state_snapshot(
        db,
        org_id,
        persist=not location_scoped,
        location_id=location_id,
        allowed_location_ids=allowed_location_ids,
    )
    await db.commit()
    return {
        "summary": summary,
        "insights": [_insight_public(x) for x in insights],
        "snapshot": {
            "id": snapshot.id,
            "active_orders": snapshot.active_orders,
            "draft_orders": snapshot.draft_orders,
            "confirmed_orders": snapshot.confirmed_orders,
            "cancelled_today": snapshot.cancelled_today,
            "revenue_today": float(snapshot.revenue_today or 0),
            "avg_check_today": float(snapshot.avg_check_today or 0),
            "queue_size": snapshot.queue_size,
            "operator_load": float(snapshot.operator_load or 0),
            "kitchen_load": float(snapshot.kitchen_load or 0),
            "stoplist_count": snapshot.stoplist_count,
            "created_at": snapshot.created_at.isoformat() if snapshot.created_at else None,
        },
        "location_scope": {
            "location_id": int(location_id) if location_id is not None else None,
            "source": "sql_location" if location_scoped else "org",
        },
    }


@router.get("/executive-hub")
async def intelligence_executive_hub(
    request: Request,
    db: AsyncSession = Depends(get_db),
    period: Annotated[str, Query(pattern="^(today|7d|30d)$")] = "today",
    location_id: Annotated[int | None, Query(ge=1)] = None,
) -> dict:
    org_id = admin_org_from_session(request)
    allowed_location_ids, location_scoped = await _location_scope_for_request(
        request,
        db,
        org_id,
        location_id,
    )
    role = await _copilot_role_from_request(request, db, org_id)
    payload = await build_executive_hub_payload(
        db,
        org_id,
        period=period,
        location_id=location_id,
        allowed_location_ids=allowed_location_ids,
        role=role,
    )
    await db.commit()
    return {
        "ok": True,
        "organization_id": org_id,
        "role": role,
        "version": payload.get("version", 1),
        "period": payload["period"],
        "dimensions": payload.get("dimensions") or {},
        "cards": payload["cards"],
        "chat": payload["chat"],
        "location_scope": {
            "location_id": int(location_id) if location_id is not None else None,
            "source": "sql_location" if location_scoped else "org",
        },
    }


@router.post("/agent-actions/propose")
async def intelligence_agent_action_propose(
    body: AgentActionProposeBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    from app.services.agent_actions import proposal_public, propose_agent_action

    org_id = admin_org_from_session(request)
    staff = await _session_staff_user(request, db)
    try:
        row = await propose_agent_action(
            db,
            organization_id=org_id,
            staff_user_id=int(staff.id) if staff is not None else None,
            action_type=body.action_type,
            title=body.title,
            summary=body.summary,
            payload=body.payload,
            source=body.source,
            source_insight_id=body.source_insight_id,
            source_snapshot_id=body.source_snapshot_id,
            source_conversation_id=body.source_conversation_id,
            trace_id=body.trace_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await db.commit()
    return {"ok": True, "proposal": proposal_public(row)}


@router.get("/agent-actions/commands")
async def intelligence_agent_action_commands() -> dict:
    from app.services.agent_actions import supported_agent_commands

    return {"ok": True, "items": supported_agent_commands()}


@router.post("/agent-actions/{proposal_id}/preview")
async def intelligence_agent_action_preview(
    proposal_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    from app.services.agent_actions import preview_agent_action

    org_id = admin_org_from_session(request)
    staff = await _session_staff_user(request, db)
    role = str(getattr(staff, "role", "") or "admin").strip().lower()
    try:
        result = await preview_agent_action(
            db,
            proposal_id=proposal_id,
            organization_id=org_id,
            staff_role=role,
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="Proposal not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PermissionError:
        raise HTTPException(status_code=403, detail="Role not allowed") from None
    await db.commit()
    return result


@router.get("/agent-actions/{proposal_id}/chain")
async def intelligence_agent_action_chain(
    proposal_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    from app.services.agent_actions import build_action_chain

    org_id = admin_org_from_session(request)
    try:
        return await build_action_chain(db, proposal_id=proposal_id, organization_id=org_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Proposal not found") from None


@router.post("/agent-actions/{proposal_id}/confirm")
async def intelligence_agent_action_confirm(
    proposal_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    from app.services.agent_actions import confirm_agent_action

    org_id = admin_org_from_session(request)
    staff = await _session_staff_user(request, db)
    role = str(getattr(staff, "role", "") or "admin").strip().lower()
    try:
        result = await confirm_agent_action(
            db,
            proposal_id=proposal_id,
            organization_id=org_id,
            staff_user_id=int(staff.id) if staff is not None else None,
            staff_role=role,
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="Proposal not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PermissionError:
        raise HTTPException(status_code=403, detail="Role not allowed") from None
    await db.commit()
    return result


@router.post("/agent-actions/{proposal_id}/reject")
async def intelligence_agent_action_reject(
    proposal_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    from app.services.agent_actions import proposal_public, reject_agent_action

    org_id = admin_org_from_session(request)
    row = await reject_agent_action(db, proposal_id=proposal_id, organization_id=org_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Proposal not found")
    await db.commit()
    return {"ok": True, "proposal": proposal_public(row)}


@router.post("/query")
async def intelligence_query(
    body: IntelligenceQueryBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    org_id = admin_org_from_session(request)
    role = await _copilot_role_from_request(request, db, org_id)
    result = await answer_intelligence_query(
        db,
        org_id=org_id,
        question=body.question,
        conversation_id=body.conversation_id,
        role=role,
    )
    await db.commit()
    return result


@router.get("/business-questions")
async def intelligence_business_questions(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    org_id = admin_org_from_session(request)
    role = await _copilot_role_from_request(request, db, org_id)
    return {"ok": True, "role": role, "items": questions_for_role(role)}


@router.get("/insights")
async def intelligence_insights(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    org_id = admin_org_from_session(request)
    insights = await list_insights(db, org_id, limit=30)
    await db.commit()
    return {"items": [_insight_public(x) for x in insights]}


@router.patch("/insights/{insight_id}")
async def patch_intelligence_insight(
    insight_id: int,
    body: InsightPatchBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    org_id = admin_org_from_session(request)
    row = await db.get(OperationalInsight, int(insight_id))
    if row is None or int(row.organization_id) != int(org_id):
        raise HTTPException(status_code=404, detail="Insight not found")
    row.status = body.status
    if body.status == "resolved":
        row.resolved_at = datetime.now(timezone.utc)
        await record_memory_event(
            db,
            org_id,
            event_type="major_anomaly_resolved",
            event_date=datetime.now(timezone.utc).date(),
            entity_type="operational_insight",
            entity_id=str(row.id),
            summary=f"Resolved insight: {row.title}",
            payload={"insight_type": row.insight_type, "severity": row.severity},
            source="system",
            confidence_score=float(row.confidence_score or 0.8),
        )
    if body.was_useful is not None:
        row.was_useful = body.was_useful
    if body.notes is not None:
        row.notes = (body.notes or "").strip() or None
    await db.commit()
    return {"ok": True, "item": _insight_public(row)}


@router.get("/insight-deliveries")
async def intelligence_insight_deliveries(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    db: AsyncSession = Depends(get_db),
) -> dict:
    org_id = admin_org_from_session(request)
    rows = await list_insight_deliveries(db, org_id, limit=limit)
    await db.commit()
    return {"ok": True, "items": [delivery_public(row) for row in rows]}


@router.patch("/insight-deliveries/{delivery_id}")
async def patch_intelligence_insight_delivery(
    delivery_id: int,
    body: InsightDeliveryActionBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    org_id = admin_org_from_session(request)
    row = await mark_insight_delivery(db, org_id, delivery_id, action=body.action)
    if row is None:
        raise HTTPException(status_code=404, detail="Insight delivery not found")
    await db.commit()
    return {"ok": True, "item": delivery_public(row)}


@router.get("/insight-delivery-settings")
async def get_intelligence_insight_delivery_settings(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    org_id = admin_org_from_session(request)
    org = await db.get(Organization, int(org_id))
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    return {"ok": True, "settings": _delivery_settings_public(org)}


@router.patch("/insight-delivery-settings")
async def patch_intelligence_insight_delivery_settings(
    body: InsightDeliverySettingsBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    org_id = admin_org_from_session(request)
    org = await db.get(Organization, int(org_id))
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    allowed = {"critical", "warning", "info"}
    severities = [s for s in body.telegram_owner_severities if s in allowed]
    if not severities:
        severities = ["critical"]
    meta = dict(org.meta_json or {})
    meta["insight_delivery"] = {
        "telegram_owner": {"enabled": True, "severities": severities},
        "daily_digest": {"enabled": bool(body.daily_digest_enabled)},
        "weekly_digest": {"enabled": bool(body.weekly_digest_enabled)},
        "inbox": {"enabled": bool(body.inbox_enabled)},
    }
    org.meta_json = meta
    flag_modified(org, "meta_json")
    await db.commit()
    return {"ok": True, "settings": _delivery_settings_public(org)}


@router.get("/roi-outcomes")
async def intelligence_roi_outcomes(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    status: Annotated[str | None, Query(pattern="^(proposed|applied|measured)$")] = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
    org_id = admin_org_from_session(request)
    rows = await list_recommendation_outcomes(db, org_id, limit=limit, status=status)
    await db.commit()
    return {"ok": True, "items": [recommendation_outcome_public(row) for row in rows]}


@router.get("/memory")
async def intelligence_memory(
    request: Request,
    days: Annotated[int, Query(ge=1, le=365)] = 90,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    db: AsyncSession = Depends(get_db),
) -> dict:
    org_id = admin_org_from_session(request)
    rows = await list_memory_events(db, org_id, days=days, limit=limit)
    await db.commit()
    return {"ok": True, "items": [memory_event_public(row) for row in rows]}


@router.post("/memory")
async def create_intelligence_memory(
    body: MemoryNoteBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    org_id = admin_org_from_session(request)
    row = await record_memory_event(
        db,
        org_id,
        event_type=body.event_type or "manager_note",
        event_date=body.event_date,
        entity_type=body.entity_type,
        entity_id=body.entity_id,
        summary=body.summary,
        payload=body.payload or {},
        source="manual",
        confidence_score=1.0,
    )
    await db.commit()
    return {"ok": True, "item": memory_event_public(row)}


@router.get("/digital-twin")
async def digital_twin_state(
    request: Request,
    db: AsyncSession = Depends(get_db),
    location_id: Annotated[int | None, Query(ge=1)] = None,
) -> dict:
    org_id = admin_org_from_session(request)
    allowed_location_ids, location_scoped = await _location_scope_for_request(
        request,
        db,
        org_id,
        location_id,
    )
    snapshot = await build_state_snapshot(
        db,
        org_id,
        persist=not location_scoped,
        location_id=location_id,
        allowed_location_ids=allowed_location_ids,
    )
    await db.commit()
    return {
        "snapshot": {
            "id": snapshot.id,
            "active_orders": snapshot.active_orders,
            "draft_orders": snapshot.draft_orders,
            "confirmed_orders": snapshot.confirmed_orders,
            "cancelled_today": snapshot.cancelled_today,
            "revenue_today": float(snapshot.revenue_today or 0),
            "avg_check_today": float(snapshot.avg_check_today or 0),
            "queue_size": snapshot.queue_size,
            "operator_load": float(snapshot.operator_load or 0),
            "kitchen_load": float(snapshot.kitchen_load or 0),
            "stoplist_count": snapshot.stoplist_count,
            "payload": snapshot.payload_json or {},
            "created_at": snapshot.created_at.isoformat() if snapshot.created_at else None,
        },
        "location_scope": {
            "location_id": int(location_id) if location_id is not None else None,
            "source": "sql_location" if location_scoped else "org",
        },
    }


@router.post("/simulate")
async def digital_twin_simulate(body: SimulationBody) -> dict:
    result = simulate_operator_capacity(
        SimulationInput(
            orders_per_hour=float(body.orders_per_hour),
            operators=int(body.operators),
            avg_check=float(body.avg_check),
            base_cancel_rate_pct=float(body.base_cancel_rate_pct),
        )
    )
    return {"result": result}


# ─── P4: Latency baselines ────────────────────────────────────────────────────

@router.get("/latency")
async def intelligence_latency(
    request: Request,
    hours: int = Query(default=24, ge=1, le=168),
    location_id: Annotated[int | None, Query(ge=1)] = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Агрегированные метрики задержки пайплайна по стадиям (p50/p95/max)."""
    from app.services.pipeline_latency import get_latency_summary, get_sla_violations_count
    org_id = admin_org_from_session(request)
    _, location_scoped = await _location_scope_for_request(request, db, org_id, location_id)
    stages = await get_latency_summary(db, org_id, hours=hours)
    violations = await get_sla_violations_count(db, org_id, hours=hours)
    return {
        "period_hours": hours,
        "stages": stages,
        "sla_violations": violations,
        "location_scope": {
            "location_id": int(location_id) if location_id is not None else None,
            "source": "org_level_latency_logs" if location_scoped else "org",
        },
        "sla_thresholds": {
            "llm_p95_ms": 4000,
            "total_p95_ms": 8000,
        },
    }


# ─── P4: Operator efficiency ──────────────────────────────────────────────────

@router.get("/operator-efficiency")
async def intelligence_operator_efficiency(
    request: Request,
    hours: int = Query(default=24, ge=1, le=168),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Метрики эффективности операторов: эскалации, время отклика, recovery rate."""
    from app.services.operator_efficiency import get_operator_efficiency
    org_id = admin_org_from_session(request)
    return await get_operator_efficiency(db, org_id, hours=hours)


# ─── P4: Business recommendations ────────────────────────────────────────────

def _rec_public(r: BusinessRecommendation) -> dict:
    return {
        "id": r.id,
        "recommendation_type": r.recommendation_type,
        "title": r.title,
        "body": r.body,
        "confidence_pct": r.confidence_pct,
        "expected_impact_kzt": r.expected_impact_kzt,
        "status": r.status,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


class RecommendationPatchBody(BaseModel):
    status: str = Field(..., pattern="^(viewed|acted_on|dismissed)$")


@router.get("/recommendations")
async def intelligence_recommendations(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Последние бизнес-рекомендации для ресторана."""
    from app.services.recommendations import list_recommendations
    org_id = admin_org_from_session(request)
    recs = await list_recommendations(db, org_id, limit=10)
    return {"items": [_rec_public(r) for r in recs]}


@router.post("/recommendations/refresh")
async def intelligence_recommendations_refresh(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Принудительно перегенерировать рекомендации прямо сейчас."""
    from app.services.recommendations import generate_recommendations
    org_id = admin_org_from_session(request)
    recs = await generate_recommendations(db, org_id)
    await db.commit()
    return {"ok": True, "generated": len(recs), "items": [_rec_public(r) for r in recs]}


@router.patch("/recommendations/{rec_id}")
async def patch_recommendation(
    rec_id: int,
    body: RecommendationPatchBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Обновить статус рекомендации (viewed / acted_on / dismissed)."""
    org_id = admin_org_from_session(request)
    row = await db.get(BusinessRecommendation, int(rec_id))
    if row is None or int(row.organization_id) != int(org_id):
        raise HTTPException(status_code=404, detail="Recommendation not found")
    row.status = body.status
    await db.commit()
    return {"ok": True, "item": _rec_public(row)}


# ─── Phase 5 OS: Apply Autopilot Pricing ────────────────────────────────────


class ApplyPricingSignalBody(BaseModel):
    price_adj_pct: int = Field(..., ge=-50, le=50, description="Изменение цены в % (отрицательное = снижение)")


@router.post("/apply-pricing-signal")
async def apply_pricing_signal(
    body: ApplyPricingSignalBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Применяет ценовой сигнал напрямую: изменяет цены активных позиций меню.

    Используется когда рекомендация ещё не создана или владелец применяет сигнал вручную.
    Автоматически создаёт запись BusinessRecommendation с типом autopilot_pricing и acted_on.
    """
    from app.db.models import BusinessRecommendation as BRec
    from app.services.system_events import BusinessEvent, emit_event

    org_id = admin_org_from_session(request)
    adj_pct = body.price_adj_pct
    if adj_pct == 0:
        raise HTTPException(status_code=400, detail="price_adj_pct не может быть 0")

    multiplier = 1.0 + adj_pct / 100.0
    items = (await db.execute(
        select(MenuItem).where(
            MenuItem.organization_id == org_id,
            MenuItem.is_available.is_(True),
            MenuItem.is_archived.is_(False),
            MenuItem.price > 0,
        )
    )).scalars().all()

    snapshot: list[dict] = []
    for item in items:
        old_price = float(item.price)
        new_price = max(1.0, round(old_price * multiplier))
        snapshot.append({"id": item.id, "name": item.name, "old": old_price, "new": new_price})
        item.price = new_price

    direction = "↑" if adj_pct > 0 else "↓"
    rec = BRec(
        organization_id=org_id,
        recommendation_type="autopilot_pricing",
        title=f"Ценовой сигнал применён: {direction}{abs(adj_pct)}%",
        body=f"Применено вручную к {len(items)} активным позициям меню.",
        confidence_pct=70,
        status="acted_on",
        data_json={"price_adj_pct": adj_pct, "items_updated": len(items), "auto_generated": False},
    )
    db.add(rec)

    await emit_event(db, BusinessEvent(
        org_id=org_id,
        type="system.pricing_adjusted",
        actor="system",
        payload={"price_adj_pct": adj_pct, "items_updated": len(items), "snapshot_sample": snapshot[:5]},
    ))

    await db.commit()
    return {"ok": True, "price_adj_pct": adj_pct, "items_updated": len(items), "preview": snapshot[:10]}


@router.post("/apply-pricing/{rec_id}")
async def apply_autopilot_pricing(
    rec_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Применяет ценовую рекомендацию autopilot_pricing: изменяет цены активных позиций меню.

    Безопасно: только для рекомендаций с recommendation_type='autopilot_pricing'.
    После применения помечает рекомендацию как acted_on и эмитирует событие.
    Ограничение: изменяет только активные позиции (is_available=True, price>0) текущего org.
    """
    from sqlalchemy import update as _upd
    from app.db.models import BusinessRecommendation, MenuItem
    from app.services.system_events import BusinessEvent, emit_event

    org_id = admin_org_from_session(request)
    rec = await db.get(BusinessRecommendation, int(rec_id))
    if rec is None or int(rec.organization_id) != int(org_id):
        raise HTTPException(status_code=404, detail="Рекомендация не найдена")
    if rec.recommendation_type != "autopilot_pricing":
        raise HTTPException(status_code=400, detail="Только autopilot_pricing рекомендации поддерживают apply")
    if rec.status == "acted_on":
        raise HTTPException(status_code=409, detail="Рекомендация уже применена")

    data = rec.data_json or {}
    price_adj_pct = int(data.get("price_adj_pct") or 0)
    if price_adj_pct == 0:
        raise HTTPException(status_code=400, detail="price_adj_pct = 0, нечего применять")

    multiplier = 1.0 + price_adj_pct / 100.0

    # Загружаем все активные позиции с ненулевой ценой
    items = (await db.execute(
        select(MenuItem).where(
            MenuItem.organization_id == org_id,
            MenuItem.is_available.is_(True),
            MenuItem.is_archived.is_(False),
            MenuItem.price > 0,
        )
    )).scalars().all()

    updated_count = 0
    snapshot: list[dict] = []
    for item in items:
        old_price = float(item.price)
        new_price = max(1.0, round(old_price * multiplier))
        snapshot.append({"id": item.id, "name": item.name, "old": old_price, "new": new_price})
        item.price = new_price
        updated_count += 1

    rec.status = "acted_on"

    # Эмитируем событие для audit trail и websocket
    await emit_event(
        db,
        BusinessEvent(
            org_id=org_id,
            type="system.pricing_adjusted",
            actor="system",
            payload={
                "price_adj_pct": price_adj_pct,
                "tactic": data.get("tactic"),
                "items_updated": updated_count,
                "recommendation_id": int(rec_id),
                "snapshot_sample": snapshot[:5],  # первые 5 для аудита
            },
        ),
    )

    await db.commit()
    return {
        "ok": True,
        "price_adj_pct": price_adj_pct,
        "items_updated": updated_count,
        "tactic": data.get("tactic"),
        "preview": snapshot[:10],
    }


class BulkApplyPricingBody(BaseModel):
    rec_ids: list[int] | None = Field(
        default=None,
        description="Явный список recommendation id; иначе все autopilot_pricing со status=new",
    )


@router.post("/apply-pricing/bulk")
async def apply_autopilot_pricing_bulk(
    request: Request,
    body: BulkApplyPricingBody | None = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Применить все (или выбранные) autopilot_pricing рекомендации со status=new."""
    from app.services.system_events import BusinessEvent, emit_event

    org_id = admin_org_from_session(request)
    stmt = select(BusinessRecommendation).where(
        BusinessRecommendation.organization_id == org_id,
        BusinessRecommendation.recommendation_type == "autopilot_pricing",
        BusinessRecommendation.status == "new",
    )
    if body and body.rec_ids:
        stmt = stmt.where(BusinessRecommendation.id.in_(body.rec_ids))
    recs = (await db.execute(stmt)).scalars().all()
    if not recs:
        return {"ok": True, "applied": 0, "items_updated": 0, "message": "Нет новых ценовых рекомендаций"}

    items = (await db.execute(
        select(MenuItem).where(
            MenuItem.organization_id == org_id,
            MenuItem.is_available.is_(True),
            MenuItem.is_archived.is_(False),
            MenuItem.price > 0,
        )
    )).scalars().all()

    lead = max(
        recs,
        key=lambda r: abs(int((r.data_json or {}).get("price_adj_pct") or 0)),
    )
    lead_data = lead.data_json or {}
    price_adj_pct = int(lead_data.get("price_adj_pct") or 0)
    if price_adj_pct == 0:
        raise HTTPException(status_code=400, detail="Нет рекомендаций с ненулевым price_adj_pct")
    multiplier = 1.0 + price_adj_pct / 100.0
    total_updated = 0
    aggregate_snapshot: list[dict] = []
    for item in items:
        old_price = float(item.price)
        item.price = max(1.0, round(old_price * multiplier))
        total_updated += 1
        if len(aggregate_snapshot) < 5:
            aggregate_snapshot.append({
                "id": item.id,
                "name": item.name,
                "old": old_price,
                "new": float(item.price),
            })
    applied_ids: list[int] = []
    for rec in recs:
        rec.status = "acted_on"
        applied_ids.append(int(rec.id))
    last_adj_pct = price_adj_pct
    last_tactic = lead_data.get("tactic")

    if not applied_ids:
        raise HTTPException(status_code=400, detail="Нет рекомендаций с ненулевым price_adj_pct")

    await emit_event(
        db,
        BusinessEvent(
            org_id=org_id,
            type="system.pricing_adjusted",
            actor="system",
            payload={
                "bulk": True,
                "recommendation_ids": applied_ids,
                "price_adj_pct": last_adj_pct,
                "tactic": last_tactic,
                "items_updated": total_updated,
                "snapshot_sample": aggregate_snapshot,
            },
        ),
    )
    await db.commit()
    return {
        "ok": True,
        "applied": len(applied_ids),
        "recommendation_ids": applied_ids,
        "items_updated": total_updated,
        "price_adj_pct": last_adj_pct,
        "preview": aggregate_snapshot,
    }


# ─── Phase 5 OS: OS Autopilot Dashboard ─────────────────────────────────────


@router.get("/os-dashboard")
async def os_dashboard(
    request: Request,
    location_id: Annotated[int | None, Query(ge=1)] = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """OS Autopilot view — ТОЛЬКО event-driven данные, нет SQL к Order/ChatLog.

    Источники: DailyOrgStats + BusinessRecommendation + OperationalInsight + AiUsageLog.
    Идеально для Phase 5 pilot: один endpoint = всё что нужно OS-операторам.
    """
    from datetime import datetime, timezone
    org_id = admin_org_from_session(request)
    allowed_location_ids, location_scoped = await _location_scope_for_request(
        request,
        db,
        org_id,
        location_id,
    )
    now_utc = datetime.now(tz=timezone.utc)

    # Today summary (event-driven)
    today_summary = (
        {
            "orders_created": 0,
            "orders_confirmed": 0,
            "orders_cancelled": 0,
            "revenue_kzt": 0.0,
            "payments_completed": 0,
            "payments_failed": 0,
            "escalations": 0,
            "operator_takeovers": 0,
            "dialogs_count": 0,
        }
        if location_scoped
        else await get_today_event_summary(db, org_id)
    )

    # Revenue forecast (event-driven, 28 days)
    if location_scoped:
        revenue_history = await fetch_daily_revenue_history(
            db,
            org_id,
            days=28,
            now_utc=now_utc,
            location_id=location_id,
            allowed_location_ids=allowed_location_ids,
        )
        forecast_source = "sql_location"
        today_summary["revenue_kzt"] = float(revenue_history.get(now_utc.date().isoformat(), 0.0) or 0.0)
    else:
        revenue_history = await fetch_daily_revenue_history_from_events(db, org_id, days=28, now_utc=now_utc)
        forecast_source = "event_driven"
    week_forecast = build_week_forecast(revenue_history, today=now_utc.date())
    if week_forecast:
        week_forecast = {**week_forecast, "source": forecast_source}

    # Demand forecast (orders, event-driven) + predictive analytics
    event_rows = [] if location_scoped else await get_event_stats(db, org_id, days=28)
    orders_by_date = {r["date"]: int(r["orders_confirmed"] or 0) for r in event_rows}
    demand_forecast = build_demand_forecast(orders_by_date, today=now_utc.date())
    cancellation_risk = build_cancellation_forecast(event_rows, today=now_utc.date())
    overload_risk = build_overload_risk(event_rows, today=now_utc.date())
    autopilot_pricing = build_autopilot_pricing(event_rows, today=now_utc.date())
    inventory_stmt = (
        select(InventoryStockSnapshot)
        .where(InventoryStockSnapshot.organization_id == org_id)
        .order_by(InventoryStockSnapshot.updated_at.desc())
        .limit(200)
    )
    if location_id is not None:
        inventory_stmt = inventory_stmt.where(InventoryStockSnapshot.location_id == int(location_id))
    elif allowed_location_ids is not None:
        inventory_stmt = inventory_stmt.where(InventoryStockSnapshot.location_id.in_(list(allowed_location_ids)))
    inventory_rows = (await db.execute(inventory_stmt)).scalars().all()
    stock_alerts = build_stock_alerts_from_inventory(inventory_rows)
    if not stock_alerts:
        stock_alerts = build_stock_alerts_stub(event_rows, today=now_utc.date())

    # Active incidents (lazy: only new insights from last 24h)
    from datetime import timedelta
    from sqlalchemy import select as _sel
    cutoff = now_utc - timedelta(hours=24)
    incident_rows = (await db.execute(
        _sel(OperationalInsight)
        .where(
            OperationalInsight.organization_id == org_id,
            OperationalInsight.status == "new",
            OperationalInsight.created_at >= cutoff,
        )
        .order_by(OperationalInsight.created_at.desc())
        .limit(5)
    )).scalars().all()
    incidents = [
        {
            "id": int(i.id),
            "category": i.insight_type,
            "insight_type": i.insight_type,
            "title": i.title,
            "summary": i.summary,
            "severity": i.severity,
            "created_at": i.created_at.isoformat() if i.created_at else None,
        }
        for i in incident_rows
    ]

    # Top-3 recommendations (по expected_impact_kzt)
    rec_rows = (await db.execute(
        _sel(BusinessRecommendation)
        .where(
            BusinessRecommendation.organization_id == org_id,
            BusinessRecommendation.status.in_(["new", "viewed"]),
        )
        .order_by(
            BusinessRecommendation.expected_impact_kzt.desc().nulls_last(),
            BusinessRecommendation.created_at.desc(),
        )
        .limit(3)
    )).scalars().all()
    recommendations = [
        {
            "id": int(r.id),
            "type": r.recommendation_type,
            "title": r.title,
            "impact_kzt": r.expected_impact_kzt,
            "confidence_pct": r.confidence_pct,
        }
        for r in rec_rows
    ]

    return {
        "ok": True,
        "source": "sql_location" if location_scoped else "event_driven",
        "generated_at": now_utc.isoformat(),
        "organization_id": org_id,
        "location_scope": {
            "location_id": int(location_id) if location_id is not None else None,
            "source": "sql_location" if location_scoped else "event_driven",
        },
        "today": today_summary,
        "week_forecast": week_forecast,
        "demand_forecast": demand_forecast,
        "cancellation_risk": cancellation_risk,
        "overload_risk": overload_risk,
        "autopilot_pricing": autopilot_pricing,
        "stock_alerts": stock_alerts,
        "incidents": incidents,
        "top_recommendations": recommendations,
        "note": (
            "Данные по точке считаются из SQL, потому что DailyOrgStats пока хранит агрегат по организации."
            if location_scoped
            else "Данные из дневной статистики ОС (DailyOrgStats), без прямых запросов к заказам и чатам."
        ),
    }


# ─── Phase 3b OS: AI Context Snapshot List ───────────────────────────────────


@router.get("/snapshots")
async def list_ai_snapshots(
    request: Request,
    db: AsyncSession = Depends(get_db),
    limit: int = Query(20, ge=1, le=100),
    phone: str | None = Query(None, description="Фильтр по номеру телефона"),
) -> dict:
    """Список последних AI-снимков контекста для просмотра и replay."""
    from sqlalchemy import desc
    org_id = admin_org_from_session(request)
    stmt = (
        select(AIContextSnapshot)
        .where(AIContextSnapshot.organization_id == org_id)
        .order_by(desc(AIContextSnapshot.created_at))
        .limit(limit)
    )
    if phone:
        stmt = stmt.where(AIContextSnapshot.phone == phone.strip())
    rows = (await db.execute(stmt)).scalars().all()

    def _snap_brief(s: AIContextSnapshot) -> dict:
        bs = s.business_state or {}
        return {
            "id": s.id,
            "phone": s.phone,
            "created_at": s.created_at.isoformat() if s.created_at else None,
            "intent": bs.get("last_intent"),
            "has_menu": bool(bs.get("menu_context_text")),
            "has_event_slice": bool(s.event_slice),
            "has_prices": bool(bs.get("menu_prices_snapshot")),
            "has_business_state": s.business_state is not None,
            "has_customer_state": s.customer_state is not None,
            "menu_items_count": bs.get("menu_items_count"),
        }

    return {
        "ok": True,
        "organization_id": org_id,
        "items": [_snap_brief(r) for r in rows],
        "count": len(rows),
        "retention_days": 30,
    }


# ─── Money MVP: Revenue Leak Detector ────────────────────────────────────────


@router.get("/revenue-leak")
async def revenue_leak(
    request: Request,
    location_id: Annotated[int | None, Query(ge=1)] = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Детектор утечек выручки: брошенные корзины + медленные ответы + отмены.

    Обновляется при каждом запросе (no cache). Использовать для Hero Block дашборда.
    """
    from app.services.revenue_leak import build_revenue_leak
    from app.api.admin.deps import _session_is_superadmin, _session_staff_user
    from app.services.tenant_scope import allowed_location_ids_for_staff
    org_id = admin_org_from_session(request)
    staff = await _session_staff_user(request, db)
    is_super = await _session_is_superadmin(request, db)
    allowed_location_ids = await allowed_location_ids_for_staff(
        db,
        staff=staff,
        org_id=org_id,
        is_superadmin=is_super,
    )
    if location_id is not None and allowed_location_ids is not None and int(location_id) not in allowed_location_ids:
        raise HTTPException(status_code=403, detail="Location is not allowed")
    result = await build_revenue_leak(
        db,
        org_id,
        location_id=location_id,
        allowed_location_ids=allowed_location_ids,
    )
    return {"ok": True, "organization_id": org_id, **result}


@router.post("/revenue-leak/recover-drafts")
async def revenue_leak_recover_drafts(
    request: Request,
    location_id: Annotated[int | None, Query(ge=1)] = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """G8: вручную запустить G6 draft recovery для текущей org (reuse cron-логики)."""
    from app.services.draft_recovery import run_draft_recovery_for_org

    org_id = admin_org_from_session(request)
    staff = await _session_staff_user(request, db)
    is_super = await _session_is_superadmin(request, db)
    allowed_location_ids = await allowed_location_ids_for_staff(
        db,
        staff=staff,
        org_id=org_id,
        is_superadmin=is_super,
    )
    if location_id is not None and allowed_location_ids is not None and int(location_id) not in allowed_location_ids:
        raise HTTPException(status_code=403, detail="Location is not allowed")
    sent = await run_draft_recovery_for_org(db, org_id)
    await db.commit()
    return {
        "ok": True,
        "organization_id": org_id,
        "sent": int(sent),
        "location_id": location_id,
    }


# ─── Phase 5 OS: Backfill Historical Stats ───────────────────────────────────


@router.post("/backfill-stats")
async def backfill_stats(
    request: Request,
    db: AsyncSession = Depends(get_db),
    days: int = Query(90, ge=7, le=365, description="Глубина backfill в днях"),
) -> dict:
    """Заполняет daily_org_stats историческими данными из Order/EscalationEvent/ChatLog.

    Безопасно: использует GREATEST(existing, backfill) — живые event-driven данные не перетираются.
    Запускать однократно при первом развёртывании Phase 5 или после длительного простоя.
    """
    from app.services.analytics_backfill import backfill_daily_org_stats
    org_id = admin_org_from_session(request)
    result = await backfill_daily_org_stats(db, org_id, days=days)
    await db.commit()
    return result


@router.post("/tenant-scope-backfill")
async def tenant_scope_backfill(
    request: Request,
    db: AsyncSession = Depends(get_db),
    fill_org: bool = Query(True, description="Backfill NULL organization_id from users"),
    fill_location: bool = Query(True, description="Backfill NULL location_id to default location"),
) -> dict:
    """Диагностика и backfill NULL organization_id / location_id для текущей org."""
    from app.services.tenant_backfill import run_tenant_scope_backfill

    org_id = admin_org_from_session(request)
    return await run_tenant_scope_backfill(
        db,
        org_id,
        fill_org=fill_org,
        fill_location=fill_location,
    )


@router.get("/tenant-scope-gaps")
async def tenant_scope_gaps(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Счётчики строк с NULL organization_id / location_id."""
    from app.services.tenant_backfill import diagnose_tenant_scope_gaps

    org_id = admin_org_from_session(request)
    gaps = await diagnose_tenant_scope_gaps(db)
    return {"org_id": org_id, **gaps}


# ─── Phase 5 OS: Audit Log ───────────────────────────────────────────────────


@router.get("/audit-log")
async def intelligence_audit_log(
    request: Request,
    db: AsyncSession = Depends(get_db),
    limit: int = Query(50, ge=1, le=200, description="Максимум записей"),
    action: str | None = Query(None, description="Фильтр по типу события"),
    actor: str | None = Query(None, description="Фильтр по актору (ai/operator/customer/system)"),
) -> dict:
    """Иммутабельный аудит-лог бизнес-событий для org.

    Записи создаются автоматически при каждом emit_event() через audit_consumer.
    Только чтение — записи не редактируются и не удаляются.
    """
    from app.services.audit_consumer import get_audit_log
    org_id = admin_org_from_session(request)
    entries = await get_audit_log(db, org_id, limit=limit, action_filter=action, actor_filter=actor)
    return {
        "ok": True,
        "organization_id": org_id,
        "count": len(entries),
        "entries": entries,
    }


# ─── GuestCare External (MVP) ─────────────────────────────────────────────────


class ExternalReviewImportBody(BaseModel):
    url: str = Field(..., min_length=8, max_length=500)
    note: str | None = Field(default=None, max_length=500)
    author: str | None = Field(default=None, max_length=160)
    rating: int | None = Field(default=None, ge=1, le=5)
    text: str | None = Field(default=None, max_length=4000)


class InventoryStockItemBody(BaseModel):
    sku: str = Field(..., min_length=1, max_length=120)
    ingredient: str = Field(..., min_length=1, max_length=240)
    quantity: float = Field(..., ge=0)
    unit: str | None = Field(default=None, max_length=32)
    min_quantity: float | None = Field(default=None, ge=0)
    reorder_quantity: float | None = Field(default=None, ge=0)
    daily_usage_estimate: float | None = Field(default=None, ge=0)
    location_id: int | None = None
    source: str = Field(default="manual", min_length=1, max_length=40)
    external_id: str | None = Field(default=None, max_length=160)
    payload: dict | None = None


class InventoryStockBulkBody(BaseModel):
    items: list[InventoryStockItemBody] = Field(default_factory=list, max_length=500)


class SupplyDraftBody(BaseModel):
    location_id: int | None = None
    cover_days: int = Field(default=7, ge=1, le=30)


class SupplyDraftItemCheckBody(BaseModel):
    idx: int = Field(..., ge=0)
    checked: bool = True


class SupplyDraftPatchBody(BaseModel):
    status: str | None = Field(default=None, pattern="^(draft|approved|completed|cancelled)$")
    items: list[SupplyDraftItemCheckBody] | None = None

    @model_validator(mode="after")
    def require_status_or_items(self) -> SupplyDraftPatchBody:
        if self.status is None and not self.items:
            raise ValueError("status_or_items_required")
        return self


class StaffOnboardingStartBody(BaseModel):
    phone: str = Field(..., min_length=5, max_length=32)
    role: str = Field(default="staff", max_length=80)
    staff_user_id: int | None = None


class StaffOnboardingMessageBody(BaseModel):
    question: str = Field(..., min_length=1, max_length=1000)


class VoiceConfigBody(BaseModel):
    enabled: bool
    mode: str = Field(default="stt_fallback", pattern="^(stt_fallback|realtime)$")


def _external_review_public(row: ExternalReview) -> dict:
    return {
        "id": row.external_id,
        "db_id": int(row.id),
        "source": row.source,
        "url": row.url,
        "author": row.author,
        "rating": row.rating,
        "text": row.text,
        "status": row.status,
        "reply_draft": row.reply_draft,
        "imported_at": row.imported_at.isoformat() if row.imported_at else None,
    }


@router.get("/reviews/external")
async def list_external_reviews(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    org_id = admin_org_from_session(request)
    rows = (await db.execute(
        select(ExternalReview)
        .where(ExternalReview.organization_id == org_id)
        .order_by(ExternalReview.imported_at.desc(), ExternalReview.id.desc())
        .limit(50)
    )).scalars().all()
    items = [_external_review_public(r) for r in rows]
    org = await db.get(Organization, org_id)
    sync_meta = {}
    if org is not None and isinstance(org.meta_json, dict):
        sync_meta = dict(org.meta_json.get("guestcare_sync") or {})
    return {
        "ok": True,
        "items": items,
        "count": len(items),
        "sync_meta": sync_meta,
    }


@router.post("/reviews/external/sync")
async def sync_external_reviews(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Fetch 2GIS (and optional Google) review pages and upsert into external_reviews."""
    from app.services.external_reviews_sync import sync_external_reviews_for_org

    org_id = admin_org_from_session(request)
    try:
        stats = await sync_external_reviews_for_org(db, org_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await db.commit()
    org = await db.get(Organization, org_id)
    sync_meta = {}
    if org is not None and isinstance(org.meta_json, dict):
        sync_meta = dict(org.meta_json.get("guestcare_sync") or {})
    rows = (await db.execute(
        select(ExternalReview)
        .where(ExternalReview.organization_id == org_id)
        .order_by(ExternalReview.imported_at.desc(), ExternalReview.id.desc())
        .limit(50)
    )).scalars().all()
    return {
        "ok": stats.get("ok", True),
        "stats": stats,
        "sync_meta": sync_meta,
        "items": [_external_review_public(r) for r in rows],
        "count": len(rows),
    }


@router.post("/reviews/external/import")
async def import_external_review(
    body: ExternalReviewImportBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    from app.integrations.reviews_external import import_review_from_url

    org_id = admin_org_from_session(request)
    try:
        item = import_review_from_url(
            body.url,
            note=body.note,
            author=body.author,
            rating=body.rating,
            text=body.text,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    row = await db.scalar(
        select(ExternalReview).where(
            ExternalReview.organization_id == org_id,
            ExternalReview.source == item["source"],
            ExternalReview.external_id == item["id"],
        )
    )
    if row is None:
        row = ExternalReview(
            organization_id=org_id,
            source=item["source"],
            external_id=item["id"],
            url=item["url"],
        )
        db.add(row)
    row.author = str(item.get("author") or "")
    row.rating = item.get("rating")
    row.text = str(item.get("text") or "")
    row.reply_draft = item.get("reply_draft")
    row.payload_json = item
    await db.commit()
    await db.refresh(row)
    return {"ok": True, "item": _external_review_public(row)}


@router.post("/reviews/external/{review_id}/reply-draft")
async def external_review_reply_draft(
    review_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    from app.integrations.reviews_external import draft_reply_for_review

    org_id = admin_org_from_session(request)
    row = await db.scalar(
        select(ExternalReview).where(
            ExternalReview.organization_id == org_id,
            ExternalReview.external_id == review_id,
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Review not found")
    draft = draft_reply_for_review(_external_review_public(row))
    row.reply_draft = draft
    row.status = "drafted"
    await db.commit()
    return {"ok": True, "review_id": review_id, "reply_draft": draft}


@router.post("/inventory/snapshots/bulk")
async def upsert_inventory_snapshots(
    body: InventoryStockBulkBody,
    request: Request,
    _perm: None = Depends(require_staff_manager_or_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Upsert latest inventory read model used by OS stock alerts."""
    org_id = admin_org_from_session(request)
    updated = await upsert_inventory_snapshot_rows(
        db,
        org_id,
        [
            InventorySnapshotUpsertItem(
                sku=item.sku,
                ingredient=item.ingredient,
                quantity=item.quantity,
                unit=item.unit or "",
                min_quantity=item.min_quantity,
                reorder_quantity=item.reorder_quantity,
                daily_usage_estimate=item.daily_usage_estimate,
                location_id=item.location_id,
                source=item.source,
                external_id=item.external_id,
                payload=item.payload,
            )
            for item in body.items
        ],
    )
    await db.commit()
    return {"ok": True, "updated": updated}


@router.get("/inventory/stock-alerts")
async def inventory_stock_alerts(
    request: Request,
    location_id: Annotated[int | None, Query(ge=1)] = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
    org_id = admin_org_from_session(request)
    allowed_location_ids, location_scoped = await _location_scope_for_request(
        request,
        db,
        org_id,
        location_id,
    )
    stmt = (
        select(InventoryStockSnapshot)
        .where(InventoryStockSnapshot.organization_id == org_id)
        .order_by(InventoryStockSnapshot.updated_at.desc())
        .limit(200)
    )
    if location_id is not None:
        stmt = stmt.where(InventoryStockSnapshot.location_id == int(location_id))
    elif allowed_location_ids is not None:
        stmt = stmt.where(InventoryStockSnapshot.location_id.in_(list(allowed_location_ids)))
    rows = (await db.execute(stmt)).scalars().all()
    alerts = build_stock_alerts_from_inventory(rows)
    return {
        "ok": True,
        "source": "inventory_stock_snapshots",
        "items": alerts,
        "count": len(alerts),
        "location_scope": {
            "location_id": int(location_id) if location_id is not None else None,
            "source": "sql_location" if location_scoped else "org",
        },
    }


@router.post("/supplymind/drafts")
async def create_supplymind_draft(
    body: SupplyDraftBody,
    request: Request,
    _perm: None = Depends(require_staff_manager_or_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    from app.services.supplymind import build_supplymind_draft, supply_draft_public

    org_id = admin_org_from_session(request)
    draft = await build_supplymind_draft(
        db,
        org_id,
        location_id=body.location_id,
        cover_days=body.cover_days,
    )
    await db.commit()
    await db.refresh(draft)
    return {"ok": True, "item": supply_draft_public(draft)}


@router.get("/supplymind/drafts")
async def list_supplymind_drafts(
    request: Request,
    db: AsyncSession = Depends(get_db),
    limit: int = Query(20, ge=1, le=100),
) -> dict:
    from app.services.supplymind import list_supply_drafts, supply_draft_public

    org_id = admin_org_from_session(request)
    rows = await list_supply_drafts(db, org_id, limit=limit)
    return {"ok": True, "items": [supply_draft_public(r) for r in rows], "count": len(rows)}


@router.get("/supplymind/drafts/{draft_id}")
async def get_supplymind_draft(
    draft_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    from app.services.supplymind import get_supply_draft, supply_draft_public

    org_id = admin_org_from_session(request)
    draft = await get_supply_draft(db, org_id, draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="Чеклист закупки не найден")
    return {"ok": True, "item": supply_draft_public(draft)}


@router.patch("/supplymind/drafts/{draft_id}")
async def patch_supplymind_draft(
    draft_id: int,
    body: SupplyDraftPatchBody,
    request: Request,
    _perm: None = Depends(require_staff_manager_or_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    from app.services.supplymind import supply_draft_public, update_draft_items, update_draft_status

    org_id = admin_org_from_session(request)
    draft = None
    try:
        if body.status is not None:
            draft = await update_draft_status(db, org_id, draft_id, body.status)
        if body.items:
            draft = await update_draft_items(
                db,
                org_id,
                draft_id,
                [{"idx": it.idx, "checked": it.checked} for it in body.items],
            )
    except LookupError:
        raise HTTPException(status_code=404, detail="Чеклист закупки не найден") from None
    except ValueError as exc:
        code = str(exc)
        if code == "status_or_items_required":
            raise HTTPException(status_code=400, detail="Укажите status или items") from None
        if code.startswith("invalid_status:"):
            raise HTTPException(status_code=400, detail="Недопустимый статус чеклиста") from None
        if code.startswith("invalid_transition:"):
            raise HTTPException(status_code=409, detail="Переход статуса недопустим") from None
        raise HTTPException(status_code=400, detail="Не удалось обновить чеклист") from None
    if draft is None:
        from app.services.supplymind import get_supply_draft

        draft = await get_supply_draft(db, org_id, draft_id)
        if draft is None:
            raise HTTPException(status_code=404, detail="Чеклист закупки не найден")
    await db.commit()
    await db.refresh(draft)
    return {"ok": True, "item": supply_draft_public(draft)}


@router.get("/supplymind/drafts/{draft_id}/export")
async def export_supplymind_draft(
    draft_id: int,
    request: Request,
    _perm: None = Depends(require_staff_manager_or_admin),
    db: AsyncSession = Depends(get_db),
    format: str = Query("csv", pattern="^csv$"),
) -> Response:
    from app.services.supplymind import export_draft_csv, get_supply_draft

    org_id = admin_org_from_session(request)
    draft = await get_supply_draft(db, org_id, draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="Чеклист закупки не найден")
    csv_text = export_draft_csv(draft)
    filename = f"supplymind_checklist_{draft_id}.csv"
    return Response(
        content=csv_text.encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/staffmind/onboarding")
async def start_staffmind_onboarding(
    body: StaffOnboardingStartBody,
    request: Request,
    _perm: None = Depends(require_staff_manager_or_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    from app.services.staffmind import onboarding_public, start_onboarding_session

    org_id = admin_org_from_session(request)
    session = await start_onboarding_session(
        db,
        org_id,
        phone=body.phone,
        role=body.role,
        staff_user_id=body.staff_user_id,
    )
    await db.commit()
    await db.refresh(session)
    return {"ok": True, "item": onboarding_public(session)}


@router.post("/staffmind/onboarding/{session_id}/message")
async def staffmind_onboarding_message(
    session_id: int,
    body: StaffOnboardingMessageBody,
    request: Request,
    _perm: None = Depends(require_staff_manager_or_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    from app.services.staffmind import answer_staff_question, onboarding_public

    org_id = admin_org_from_session(request)
    session = await db.get(StaffOnboardingSession, int(session_id))
    if session is None or int(session.organization_id) != int(org_id):
        raise HTTPException(status_code=404, detail="Onboarding session not found")
    answer = await answer_staff_question(db, session, body.question)
    await db.commit()
    await db.refresh(session)
    return {"ok": True, "answer": answer, "item": onboarding_public(session)}


@router.get("/staffmind/onboarding")
async def list_staffmind_onboarding(
    request: Request,
    db: AsyncSession = Depends(get_db),
    limit: int = Query(20, ge=1, le=100),
) -> dict:
    from app.services.staffmind import onboarding_public

    org_id = admin_org_from_session(request)
    rows = (await db.execute(
        select(StaffOnboardingSession)
        .where(StaffOnboardingSession.organization_id == org_id)
        .order_by(StaffOnboardingSession.created_at.desc())
        .limit(limit)
    )).scalars().all()
    return {"ok": True, "items": [onboarding_public(r) for r in rows], "count": len(rows)}


@router.get("/daily-os-digest/preview")
async def daily_os_digest_preview(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    from app.services.daily_os_digest import build_daily_os_digest_payload

    org_id = admin_org_from_session(request)
    org = await db.get(Organization, org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    payload = await build_daily_os_digest_payload(db, org)
    return {"ok": True, "item": payload}


@router.get("/voice/status")
async def voice_ai_status(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    from app.services.voice_ai import voice_status_for_org

    org_id = admin_org_from_session(request)
    org = await db.get(Organization, org_id)
    return {"ok": True, "item": voice_status_for_org(org)}


@router.post("/voice/config")
async def voice_ai_config(
    body: VoiceConfigBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _perm: None = Depends(require_staff_admin),
) -> dict:
    from app.services.voice_ai import set_voice_enabled

    org_id = admin_org_from_session(request)
    org = await db.get(Organization, org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    status = await set_voice_enabled(db, org, enabled=body.enabled, mode=body.mode)
    await db.commit()
    return {"ok": True, "item": status}


@router.get("/voice/calls")
async def voice_ai_call_logs(
    request: Request,
    db: AsyncSession = Depends(get_db),
    limit: int = Query(15, ge=1, le=100, description="Макс. записей на странице"),
    offset: int = Query(0, ge=0, description="Смещение для пагинации"),
    location_id: Annotated[int | None, Query(ge=1)] = None,
) -> dict:
    """Журнал звонков Voice AI для Final Mile UI (voice_call_logs)."""
    from app.services.voice_ai import list_voice_call_logs

    org_id = admin_org_from_session(request)
    staff = await _session_staff_user(request, db)
    is_super = await _session_is_superadmin(request, db)
    allowed_location_ids = await allowed_location_ids_for_staff(
        db,
        staff=staff,
        org_id=org_id,
        is_superadmin=is_super,
    )
    if location_id is not None and allowed_location_ids is not None and int(location_id) not in allowed_location_ids:
        raise HTTPException(status_code=403, detail="Location is not allowed")
    result = await list_voice_call_logs(
        db,
        org_id=org_id,
        limit=limit,
        offset=offset,
        location_id=location_id,
    )
    return {"ok": True, "organization_id": org_id, **result}


@router.get("/trace-timeline")
async def intelligence_trace_timeline(
    request: Request,
    trace_id: Annotated[str, Query(min_length=8, max_length=120)],
    db: AsyncSession = Depends(get_db),
    limit: int = Query(100, ge=1, le=200),
) -> dict:
    """Control Plane: хронология SystemEvent + ChatLog по одному trace_id."""
    from app.services.trace_timeline import build_trace_timeline

    org_id = admin_org_from_session(request)
    timeline = await build_trace_timeline(db, org_id=org_id, trace_id=trace_id, limit=limit)
    return {"ok": True, **timeline}


# ─── Phase 2.3 OS: Event-Driven Aggregates ───────────────────────────────────


@router.get("/event-stats")
async def intelligence_event_stats(
    request: Request,
    db: AsyncSession = Depends(get_db),
    days: int = Query(7, ge=1, le=90, description="Количество дней истории"),
) -> dict:
    """Агрегаты бизнес-событий из DailyOrgStats за последние N дней.

    Данные накапливаются analytics_consumer в реальном времени при каждом emit_event().
    Не содержит прямых запросов к Order/ChatLog — чистый event-driven источник.
    """
    org_id = admin_org_from_session(request)
    rows = await get_event_stats(db, org_id, days=days)

    # Сводка за весь запрошенный период
    int_keys = (
        "orders_created", "orders_confirmed", "orders_cancelled",
        "bookings_created", "bookings_confirmed", "bookings_cancelled",
        "payments_completed", "payments_failed",
        "escalations", "operator_takeovers",
    )
    totals: dict[str, int | float] = {k: 0 for k in int_keys}
    totals["revenue_kzt"] = 0.0
    for r in rows:
        for k in int_keys:
            totals[k] = int(totals[k]) + int(r.get(k, 0) or 0)
        totals["revenue_kzt"] = float(totals["revenue_kzt"]) + float(r.get("revenue_kzt", 0) or 0)

    conversion_pct: float | None = None
    if int(totals["orders_created"]) > 0:
        conversion_pct = round(
            100 * int(totals["orders_confirmed"]) / int(totals["orders_created"]),
            1,
        )

    return {
        "ok": True,
        "period_days": days,
        "source": "event_driven",
        "totals": {**totals, "conversion_pct": conversion_pct},
        "daily": rows,
        "note": "Агрегаты накапливаются с момента включения emit_event (2026-05-18). Исторические данные до этой даты не включены.",
    }


# ─── Phase 3 OS: AI Context Snapshot (Replay) ────────────────────────────────


@router.get("/snapshots/{snapshot_id}")
async def get_ai_snapshot(
    snapshot_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Полный снимок AI-контекста по ID — для аудита и отладки решений бота."""
    org_id = admin_org_from_session(request)
    row = await db.get(AIContextSnapshot, snapshot_id)
    if row is None or int(row.organization_id) != int(org_id):
        raise HTTPException(status_code=404, detail="Snapshot not found")
    bs = row.business_state or {}
    return {
        "ok": True,
        "id": row.id,
        "phone": row.phone,
        "organization_id": row.organization_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "business_state": {
            **{k: v for k, v in bs.items() if k != "menu_context_text"},
            "has_menu_context_text": bool(bs.get("menu_context_text")),
            "has_menu_prices_snapshot": bool(bs.get("menu_prices_snapshot")),
            "menu_prices_count": len(bs.get("menu_prices_snapshot") or []),
        },
        "customer_state": row.customer_state,
        "event_slice": row.event_slice,
    }


@router.post("/snapshots/{snapshot_id}/replay")
async def replay_ai_decision(
    snapshot_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user_text: str = Query(..., description="Сообщение гостя для воспроизведения"),
) -> dict:
    """Воспроизвести решение AI с тем же контекстом что был в момент snapshot.

    Не отправляет ответ клиенту — только возвращает AIBrainResponse для отладки.
    """
    org_id = admin_org_from_session(request)
    row = await db.get(AIContextSnapshot, snapshot_id)
    if row is None or int(row.organization_id) != int(org_id):
        raise HTTPException(status_code=404, detail="Snapshot not found")

    from app.services.ai_brain import call_openai
    from app.services.order_logic import build_menu_context_for_ai, load_available_menu

    business_state = row.business_state or {}

    # Phase G3: frozen menu_context_text → точный replay; иначе snapshot цен → synthetic context;
    # иначе текущее меню из БД (legacy снапшоты).
    from app.services.context_engine import build_menu_context_from_prices_snapshot

    frozen_menu_ctx = business_state.get("menu_context_text")
    if isinstance(frozen_menu_ctx, str) and frozen_menu_ctx.strip():
        menu_context = frozen_menu_ctx
    else:
        prices_ctx = build_menu_context_from_prices_snapshot(
            business_state.get("menu_prices_snapshot"),
        )
        if prices_ctx:
            menu_context = prices_ctx
        else:
            menu_items = await load_available_menu(db, organization_id=org_id, include_unavailable=True)
            menu_context = await build_menu_context_for_ai(menu_items, user_text)

    customer_ctx = (row.customer_state or {}).get("customer_ctx_snippet", "")
    history = (row.customer_state or {}).get("chat_history_slice") or []
    if not isinstance(history, list):
        history = []

    try:
        ai_response = await call_openai(
            history=history,
            user_text=user_text,
            menu_context=menu_context,
            customer_context=customer_ctx,
            raise_on_transient=False,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AI replay failed: {exc}") from exc

    return {
        "ok": True,
        "snapshot_id": snapshot_id,
        "snapshot_created_at": row.created_at.isoformat() if row.created_at else None,
        "replay_user_text": user_text,
        "ai_response": {
            "intent": ai_response.intent,
            "reply_text": ai_response.reply_text,
            "items": [item.model_dump() for item in getattr(ai_response, "items", [])],
            "order_type": getattr(ai_response, "order_type", None),
            "payment_method": getattr(ai_response, "payment_method", None),
            "raw": ai_response.model_dump(),
        },
    }


@router.post("/snapshots/{snapshot_id}/feedback")
async def create_ai_snapshot_feedback(
    snapshot_id: str,
    body: SnapshotFeedbackBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Record manager feedback for a replayable AI context snapshot.

    MVP learning loop: no fine-tuning here; the correction becomes organization
    memory so Copilot can use it in future explanations and calibration.
    """
    org_id = admin_org_from_session(request)
    row = await db.get(AIContextSnapshot, snapshot_id)
    if row is None or int(row.organization_id) != int(org_id):
        raise HTTPException(status_code=404, detail="Snapshot not found")

    reason = body.reason.strip()
    correction = (body.correction or "").strip()
    question = (body.question or "").strip()
    expected_behavior = (body.expected_behavior or "").strip()
    parts = [f"AI snapshot feedback: {reason}"]
    if correction:
        parts.append(f"Correction: {correction}")
    if expected_behavior:
        parts.append(f"Expected behavior: {expected_behavior}")
    summary = " | ".join(parts)

    memory = await record_memory_event(
        db,
        org_id,
        event_type="ai_snapshot_feedback",
        event_date=datetime.now(timezone.utc).date(),
        entity_type="ai_context_snapshot",
        entity_id=row.id,
        summary=summary,
        payload={
            "snapshot_id": row.id,
            "phone": row.phone,
            "question": question,
            "reason": reason,
            "correction": correction,
            "expected_behavior": expected_behavior,
            "snapshot_created_at": row.created_at.isoformat() if row.created_at else None,
            "intent": (row.business_state or {}).get("last_intent"),
        },
        source="manual",
        confidence_score=1.0,
    )
    await db.commit()
    return {"ok": True, "snapshot_id": row.id, "memory": memory_event_public(memory)}


@router.get("/replay/scenarios")
async def list_replay_scenarios(request: Request) -> dict:
    """Control Plane Phase 3 — golden conversation catalog for replay harness."""
    _ = request
    from app.services.replay_harness import list_golden_scenarios

    return {"ok": True, "scenarios": list_golden_scenarios()}
