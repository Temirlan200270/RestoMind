"""Feedback loop for measuring recommendation ROI."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import RecommendationOutcome, SalesDailyAgg
from app.services.data_quality import latest_quality_status
from app.services.organization_memory import record_memory_event
from app.services.system_events import BusinessEvent, emit_event


async def create_recommendation_outcome(
    db: AsyncSession,
    org_id: int,
    *,
    recommendation_type: str,
    metric: str,
    baseline_value: float | None = None,
    target_value: float | None = None,
    insight_id: int | None = None,
    action_id: str | None = None,
    baseline_window: dict[str, Any] | None = None,
    measurement_window: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
) -> RecommendationOutcome:
    quality = await latest_quality_status(db, int(org_id), source="iiko_olap", entity_type="sales")
    row = RecommendationOutcome(
        organization_id=int(org_id),
        insight_id=insight_id,
        action_id=action_id,
        recommendation_type=recommendation_type,
        metric=metric,
        baseline_value=baseline_value,
        target_value=target_value,
        baseline_window_json=baseline_window,
        measurement_window_json=measurement_window,
        data_quality_confidence=float(quality.get("confidence_score") or 0),
        payload_json=payload or {},
        status="proposed",
    )
    db.add(row)
    await db.flush()
    return row


async def mark_outcome_applied(
    db: AsyncSession,
    org_id: int,
    outcome_id: int,
) -> RecommendationOutcome | None:
    row = await db.get(RecommendationOutcome, int(outcome_id))
    if row is None or int(row.organization_id) != int(org_id):
        return None
    row.status = "applied"
    row.applied_at = datetime.now(tz=timezone.utc)
    if row.measure_after is None:
        row.measure_after = row.applied_at + timedelta(days=7)
    if not row.measurement_window_json:
        start = row.applied_at.date().isoformat()
        end = row.measure_after.date().isoformat()
        row.measurement_window_json = {"date_from": start, "date_to": end}
    await db.flush()
    return row


def _date_from_payload(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)).date()
    except ValueError:
        return None


async def _sum_revenue(db: AsyncSession, org_id: int, start: date, end: date) -> float | None:
    value = await db.scalar(
        select(func.coalesce(func.sum(SalesDailyAgg.total_revenue), 0)).where(
            SalesDailyAgg.organization_id == int(org_id),
            SalesDailyAgg.source == "iiko_olap",
            SalesDailyAgg.date >= start,
            SalesDailyAgg.date <= end,
        ),
    )
    return float(value or 0)


async def measure_due_outcomes(db: AsyncSession, org_id: int) -> int:
    now = datetime.now(tz=timezone.utc)
    rows = (
        await db.execute(
            select(RecommendationOutcome)
            .where(
                RecommendationOutcome.organization_id == int(org_id),
                RecommendationOutcome.status.in_(["applied", "proposed"]),
                RecommendationOutcome.measured_at.is_(None),
            )
            .limit(50),
        )
    ).scalars().all()

    measured = 0
    for row in rows:
        if row.measure_after is not None and row.measure_after > now:
            continue
        if row.metric != "revenue":
            continue
        measurement_window = row.measurement_window_json or {}
        baseline_window = row.baseline_window_json or {}
        measurement_start = _date_from_payload(measurement_window.get("date_from"))
        measurement_end = _date_from_payload(measurement_window.get("date_to"))
        baseline_start = _date_from_payload(baseline_window.get("date_from"))
        baseline_end = _date_from_payload(baseline_window.get("date_to"))

        measured_value: float | None = None
        if measurement_start and measurement_end:
            measured_value = await _sum_revenue(db, int(org_id), measurement_start, measurement_end)
        if measured_value is None:
            latest = await db.scalar(
                select(SalesDailyAgg.total_revenue)
                .where(SalesDailyAgg.organization_id == int(org_id), SalesDailyAgg.source == "iiko_olap")
                .order_by(SalesDailyAgg.date.desc())
                .limit(1),
            )
            measured_value = float(latest or 0) if latest is not None else None

        baseline_value = float(row.baseline_value or 0) if row.baseline_value is not None else None
        if baseline_value is None and baseline_start and baseline_end:
            baseline_value = await _sum_revenue(db, int(org_id), baseline_start, baseline_end)
            row.baseline_value = baseline_value
        if measured_value is None or baseline_value is None:
            continue

        quality = await latest_quality_status(db, int(org_id), source="iiko_olap", entity_type="sales")
        row.data_quality_confidence = float(quality.get("confidence_score") or 0)
        realized_delta = float(measured_value or 0) - float(baseline_value or 0)
        row.realized_delta = round(realized_delta, 2)
        row.realized_money = round(realized_delta, 2)
        row.measured_at = now
        row.status = "measured"
        row.measurement_window_json = {
            **(row.measurement_window_json or {}),
            "measured_value": round(float(measured_value or 0), 2),
            "data_quality_confidence": row.data_quality_confidence,
        }
        await emit_event(
            db,
            BusinessEvent(
                org_id=int(org_id),
                type="recommendation.measured",
                actor="system",
                entity_type="recommendation_outcome",
                entity_id=int(row.id),
                payload={
                    "recommendation_type": row.recommendation_type,
                    "metric": row.metric,
                    "realized_delta": row.realized_delta,
                    "realized_money": row.realized_money,
                    "baseline_window": row.baseline_window_json,
                    "measurement_window": row.measurement_window_json,
                    "data_quality_confidence": row.data_quality_confidence,
                },
            ),
        )
        await record_memory_event(
            db,
            int(org_id),
            event_type="recommendation_measured",
            entity_type="recommendation_outcome",
            entity_id=str(row.id),
            summary=(
                f"Recommendation {row.recommendation_type} measured: "
                f"{float(row.realized_money or 0):.0f} money delta on {row.metric}."
            ),
            payload={
                "recommendation_type": row.recommendation_type,
                "metric": row.metric,
                "realized_delta": row.realized_delta,
                "realized_money": row.realized_money,
                "baseline_window": row.baseline_window_json,
                "measurement_window": row.measurement_window_json,
            },
            source="roi_loop",
            confidence_score=float(row.data_quality_confidence or 0),
        )
        measured += 1
    if measured:
        await db.flush()
    return measured


def recommendation_outcome_public(row: RecommendationOutcome) -> dict[str, Any]:
    quality = float(row.data_quality_confidence or 0)
    causality = "measured_delta"
    if quality < 0.7:
        causality = "measured_delta_low_data_confidence"
    elif row.insight_id is None and not row.action_id:
        causality = "measured_delta_unlinked_action"
    created_at = row.__dict__.get("created_at")
    updated_at = row.__dict__.get("updated_at")
    return {
        "id": int(row.id),
        "organization_id": int(row.organization_id),
        "insight_id": int(row.insight_id) if row.insight_id is not None else None,
        "action_id": row.action_id,
        "recommendation_type": row.recommendation_type,
        "status": row.status,
        "metric": row.metric,
        "baseline_value": float(row.baseline_value) if row.baseline_value is not None else None,
        "target_value": float(row.target_value) if row.target_value is not None else None,
        "realized_delta": float(row.realized_delta) if row.realized_delta is not None else None,
        "realized_money": float(row.realized_money) if row.realized_money is not None else None,
        "baseline_window": row.baseline_window_json or {},
        "measurement_window": row.measurement_window_json or {},
        "data_quality_confidence": round(quality, 4),
        "causality": causality,
        "chain": {
            "advice": row.payload_json or {},
            "applied_at": row.applied_at.isoformat() if row.applied_at else None,
            "measure_after": row.measure_after.isoformat() if row.measure_after else None,
            "measured_at": row.measured_at.isoformat() if row.measured_at else None,
            "result": {
                "delta": float(row.realized_delta) if row.realized_delta is not None else None,
                "money": float(row.realized_money) if row.realized_money is not None else None,
            },
        },
        "created_at": created_at.isoformat() if created_at else None,
        "updated_at": updated_at.isoformat() if updated_at else None,
    }


async def list_recommendation_outcomes(
    db: AsyncSession,
    org_id: int,
    *,
    limit: int = 50,
    status: str | None = None,
) -> list[RecommendationOutcome]:
    stmt = (
        select(RecommendationOutcome)
        .where(RecommendationOutcome.organization_id == int(org_id))
        .order_by(RecommendationOutcome.created_at.desc(), RecommendationOutcome.id.desc())
        .limit(max(1, min(int(limit), 100)))
    )
    if status:
        stmt = stmt.where(RecommendationOutcome.status == status)
    return list((await db.execute(stmt)).scalars().all())


async def recommendation_outcomes_scheduled_tick(_ctx: dict[str, Any] | None = None) -> None:
    from sqlalchemy import select as _select

    from app.db.models import Organization
    from app.db.session import async_session_factory

    async with async_session_factory() as db:
        org_ids = list(
            (await db.execute(_select(Organization.id).where(Organization.is_active.is_(True)))).scalars().all(),
        )
    for org_id in org_ids:
        async with async_session_factory() as db:
            await measure_due_outcomes(db, int(org_id))
            await db.commit()
