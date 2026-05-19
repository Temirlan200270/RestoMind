"""Restaurant Intelligence and Digital Twin admin API."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin.deps import admin_org_from_session, require_admin_session_active
from sqlalchemy import select
from app.db.models import AIContextSnapshot, BusinessRecommendation, MenuItem, OperationalInsight, Organization
from app.db.session import get_db
from app.services.analytics_consumer import get_event_stats, get_today_event_summary
from app.services.owner_dashboard import (
    build_autopilot_pricing,
    build_cancellation_forecast,
    build_demand_forecast,
    build_overload_risk,
    build_stock_alerts_stub,
    build_week_forecast,
    fetch_daily_revenue_history_from_events,
)
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
    was_useful: bool | None = Field(default=None, description="Оператор отметил инсайт полезным (true) или нет (false)")
    notes: str | None = Field(default=None, max_length=500, description="Заметка оператора при закрытии")


class SimulationBody(BaseModel):
    orders_per_hour: float = Field(..., ge=0, le=500)
    operators: int = Field(..., ge=1, le=100)
    avg_check: float = Field(..., ge=0, le=10_000_000)
    base_cancel_rate_pct: float = Field(default=5.0, ge=0, le=100)


def _insight_public(row: OperationalInsight) -> dict:
    payload = row.payload_json or {}
    return {
        "id": row.id,
        "insight_type": row.insight_type,
        "severity": row.severity,
        "title": row.title,
        "summary": row.summary,
        "payload": payload,
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
    if body.was_useful is not None:
        row.was_useful = body.was_useful
    if body.notes is not None:
        row.notes = (body.notes or "").strip() or None
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


# ─── Phase 5 OS: Apply Autopilot Pricing ────────────────────────────────────


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
    db: AsyncSession = Depends(get_db),
) -> dict:
    """OS Autopilot view — ТОЛЬКО event-driven данные, нет SQL к Order/ChatLog.

    Источники: DailyOrgStats + BusinessRecommendation + OperationalInsight + AiUsageLog.
    Идеально для Phase 5 pilot: один endpoint = всё что нужно OS-операторам.
    """
    from datetime import datetime, timezone
    org_id = admin_org_from_session(request)
    now_utc = datetime.now(tz=timezone.utc)

    # Today summary (event-driven)
    today_summary = await get_today_event_summary(db, org_id)

    # Revenue forecast (event-driven, 28 days)
    revenue_history = await fetch_daily_revenue_history_from_events(db, org_id, days=28, now_utc=now_utc)
    week_forecast = build_week_forecast(revenue_history, today=now_utc.date())
    if week_forecast:
        week_forecast = {**week_forecast, "source": "event_driven"}

    # Demand forecast (orders, event-driven) + predictive analytics
    event_rows = await get_event_stats(db, org_id, days=28)
    orders_by_date = {r["date"]: int(r["orders_confirmed"] or 0) for r in event_rows}
    demand_forecast = build_demand_forecast(orders_by_date, today=now_utc.date())
    cancellation_risk = build_cancellation_forecast(event_rows, today=now_utc.date())
    overload_risk = build_overload_risk(event_rows, today=now_utc.date())
    autopilot_pricing = build_autopilot_pricing(event_rows, today=now_utc.date())
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
        "source": "event_driven",
        "generated_at": now_utc.isoformat(),
        "organization_id": org_id,
        "today": today_summary,
        "week_forecast": week_forecast,
        "demand_forecast": demand_forecast,
        "cancellation_risk": cancellation_risk,
        "overload_risk": overload_risk,
        "autopilot_pricing": autopilot_pricing,
        "stock_alerts": stock_alerts,
        "incidents": incidents,
        "top_recommendations": recommendations,
        "note": "Все данные из DailyOrgStats (event bus). Нет SQL к Order/ChatLog.",
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


def _guestcare_reviews(org: Organization) -> list[dict]:
    meta = org.meta_json if isinstance(org.meta_json, dict) else {}
    raw = meta.get("guestcare_external_reviews")
    return list(raw) if isinstance(raw, list) else []


def _save_guestcare_reviews(org: Organization, reviews: list[dict]) -> None:
    meta = dict(org.meta_json) if isinstance(org.meta_json, dict) else {}
    meta["guestcare_external_reviews"] = reviews
    org.meta_json = meta


@router.get("/reviews/external")
async def list_external_reviews(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    org_id = admin_org_from_session(request)
    org = await db.get(Organization, org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    items = _guestcare_reviews(org)
    return {"ok": True, "items": items, "count": len(items)}


@router.post("/reviews/external/import")
async def import_external_review(
    body: ExternalReviewImportBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    from app.integrations.reviews_external import import_review_from_url

    org_id = admin_org_from_session(request)
    org = await db.get(Organization, org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    try:
        item = import_review_from_url(body.url, note=body.note)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    reviews = _guestcare_reviews(org)
    reviews = [r for r in reviews if r.get("id") != item["id"]]
    reviews.insert(0, item)
    _save_guestcare_reviews(org, reviews[:50])
    await db.commit()
    return {"ok": True, "item": item}


@router.post("/reviews/external/{review_id}/reply-draft")
async def external_review_reply_draft(
    review_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    from app.integrations.reviews_external import draft_reply_for_review

    org_id = admin_org_from_session(request)
    org = await db.get(Organization, org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    reviews = _guestcare_reviews(org)
    match = next((r for r in reviews if str(r.get("id")) == review_id), None)
    if match is None:
        raise HTTPException(status_code=404, detail="Review not found")
    draft = draft_reply_for_review(match)
    for r in reviews:
        if str(r.get("id")) == review_id:
            r["reply_draft"] = draft
    _save_guestcare_reviews(org, reviews)
    await db.commit()
    return {"ok": True, "review_id": review_id, "reply_draft": draft}


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


@router.get("/snapshots")
async def list_ai_snapshots(
    request: Request,
    db: AsyncSession = Depends(get_db),
    phone: str | None = Query(None, description="Фильтр по телефону гостя"),
    limit: int = Query(20, ge=1, le=100),
) -> dict:
    """Список последних снимков AI-контекста для организации."""
    org_id = admin_org_from_session(request)
    stmt = (
        select(AIContextSnapshot)
        .where(AIContextSnapshot.organization_id == org_id)
        .order_by(AIContextSnapshot.created_at.desc())
        .limit(limit)
    )
    if phone:
        stmt = stmt.where(AIContextSnapshot.phone == phone.strip())
    rows = (await db.execute(stmt)).scalars().all()
    return {
        "ok": True,
        "items": [
            {
                "id": r.id,
                "phone": r.phone,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "has_business_state": r.business_state is not None,
                "has_customer_state": r.customer_state is not None,
                "menu_items_count": (r.business_state or {}).get("menu_items_count"),
            }
            for r in rows
        ],
    }


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

    # Phase G3: если снапшот содержит frozen menu_context_text — используем его
    # для точного воспроизведения контекста меню как он был в момент решения.
    # Иначе — перезагружаем из БД (старые снапшоты до G3).
    frozen_menu_ctx: str | None = business_state.get("menu_context_text")
    if frozen_menu_ctx:
        menu_context = frozen_menu_ctx
    else:
        # Fallback: используем текущее меню из БД
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
