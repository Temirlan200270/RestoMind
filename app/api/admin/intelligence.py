"""Restaurant Intelligence and Digital Twin admin API."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin.deps import admin_org_from_session, require_admin_session_active
from app.db.models import BusinessRecommendation, OperationalInsight
from app.db.session import get_db
from app.services.intelligence import (
    SimulationInput,
    answer_intelligence_query,
    build_state_snapshot,
    list_insights,
    revenue_orders_summary,
    simulate_operator_capacity,
)

router = APIRouter(
    prefix="/admin/intelligence",
    tags=["Restaurant Intelligence"],
    dependencies=[Depends(require_admin_session_active)],
)


class IntelligenceQueryBody(BaseModel):
    question: str = Field(..., min_length=2, max_length=1000)
    conversation_id: int | None = None


class InsightPatchBody(BaseModel):
    status: str = Field(..., pattern="^(new|seen|resolved|dismissed)$")


class SimulationBody(BaseModel):
    orders_per_hour: float = Field(..., ge=0, le=500)
    operators: int = Field(..., ge=1, le=100)
    avg_check: float = Field(..., ge=0, le=10_000_000)
    base_cancel_rate_pct: float = Field(default=5.0, ge=0, le=100)


def _insight_public(row: OperationalInsight) -> dict:
    return {
        "id": row.id,
        "insight_type": row.insight_type,
        "severity": row.severity,
        "title": row.title,
        "summary": row.summary,
        "payload": row.payload_json or {},
        "status": row.status,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "resolved_at": row.resolved_at.isoformat() if row.resolved_at else None,
    }


@router.get("/overview")
async def intelligence_overview(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    org_id = admin_org_from_session(request)
    summary = await revenue_orders_summary(db, org_id, "today")
    insights = await list_insights(db, org_id, limit=10)
    snapshot = await build_state_snapshot(db, org_id, persist=True)
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
    }


@router.post("/query")
async def intelligence_query(
    body: IntelligenceQueryBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    org_id = admin_org_from_session(request)
    result = await answer_intelligence_query(
        db,
        org_id=org_id,
        question=body.question,
        conversation_id=body.conversation_id,
    )
    await db.commit()
    return result


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
    await db.commit()
    return {"ok": True, "item": _insight_public(row)}


@router.get("/digital-twin")
async def digital_twin_state(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    org_id = admin_org_from_session(request)
    snapshot = await build_state_snapshot(db, org_id, persist=True)
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
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Агрегированные метрики задержки пайплайна по стадиям (p50/p95/max)."""
    from app.services.pipeline_latency import get_latency_summary, get_sla_violations_count
    org_id = admin_org_from_session(request)
    stages = await get_latency_summary(db, org_id, hours=hours)
    violations = await get_sla_violations_count(db, org_id, hours=hours)
    return {
        "period_hours": hours,
        "stages": stages,
        "sla_violations": violations,
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
