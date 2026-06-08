"""Owner Intelligence — единая сводка «деньги → причина → действие → эффект» для владельца."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import AiOrderAudit, AiUsageLog, BusinessRecommendation, DailyOrgStats, Order, OrderStatus, Organization, SystemEvent
from app.services.analytics_consumer import get_event_stats_for_range
from app.services.db_schema_fallback import with_location_scope_fallback
from app.services.owner_dashboard import build_recommendation_target
from app.services.owner_roi import aggregate_org_window
from app.services.revenue_leak import build_revenue_leak
from app.services.tenant_scope import orders_location_filter, orders_tenant_clause
from app.services.timezones import zoneinfo_or_default

logger = logging.getLogger(__name__)

_VALID_PERIODS = frozenset({"today", "7d", "30d", "prev_week"})


def _dt_as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _sql_dt_for_filter(dt: datetime) -> datetime:
    u = _dt_as_utc(dt)
    return u


def _round_money(value: float) -> float:
    return round(float(value or 0), 2)


def _period_window(period: str, org_tz: str | None) -> tuple[datetime, datetime, date, date, str]:
    """Границы окна в UTC + локальные даты org (для DailyOrgStats)."""
    tag = (period or "today").strip().lower()
    if tag not in _VALID_PERIODS:
        tag = "today"
    zi = zoneinfo_or_default(org_tz, default="Etc/GMT-5").zone
    now_local = datetime.now(zi)
    end_local = now_local
    day_start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    if tag == "7d":
        start_local = day_start_local - timedelta(days=6)
    elif tag == "30d":
        start_local = day_start_local - timedelta(days=29)
    elif tag == "prev_week":
        this_monday = (now_local - timedelta(days=now_local.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0,
        )
        start_local = this_monday - timedelta(days=7)
        end_local = this_monday - timedelta(microseconds=1)
        tag = "prev_week"
    else:
        start_local = day_start_local
        tag = "today"
    if tag != "prev_week":
        start_utc = start_local.astimezone(timezone.utc)
        end_utc = end_local.astimezone(timezone.utc)
        return start_utc, end_utc, start_local.date(), end_local.date(), tag
    start_utc = start_local.astimezone(timezone.utc)
    end_utc = end_local.astimezone(timezone.utc)
    return start_utc, end_utc, start_local.date(), end_local.date(), tag


async def _cancelled_revenue_period(
    db: AsyncSession,
    org_id: int,
    ts_lo: datetime,
    ts_hi: datetime,
    *,
    location_id: int | None,
    allowed_location_ids: set[int] | None,
) -> float:
    total = float(
        await db.scalar(
            select(func.coalesce(func.sum(Order.total_price), 0)).where(
                orders_tenant_clause(org_id),
                Order.status == OrderStatus.CANCELLED.value,
                Order.created_at >= _sql_dt_for_filter(ts_lo),
                Order.created_at <= _sql_dt_for_filter(ts_hi),
                Order.total_price > 0,
                orders_location_filter(allowed_location_ids, location_id),
            ),
        )
        or 0,
    )
    return _round_money(total)


async def _sum_daily_stats(
    db: AsyncSession,
    org_id: int,
    start_d: date,
    end_d: date,
) -> dict[str, float | int]:
    rows = (
        await db.execute(
            select(DailyOrgStats).where(
                DailyOrgStats.organization_id == org_id,
                DailyOrgStats.day >= start_d,
                DailyOrgStats.day <= end_d,
            ),
        )
    ).scalars().all()
    totals: dict[str, float | int] = {
        "recovered_kzt": 0.0,
        "draft_recovery_sent": 0,
        "focus_completed_count": 0,
        "revenue_kzt": 0.0,
    }
    for row in rows:
        totals["recovered_kzt"] = float(totals["recovered_kzt"]) + float(row.recovered_kzt or 0)
        totals["draft_recovery_sent"] = int(totals["draft_recovery_sent"]) + int(row.draft_recovery_sent or 0)
        totals["focus_completed_count"] = int(totals["focus_completed_count"]) + int(row.focus_completed_count or 0)
        totals["revenue_kzt"] = float(totals["revenue_kzt"]) + float(row.revenue_kzt or 0)
    totals["recovered_kzt"] = _round_money(float(totals["recovered_kzt"]))
    totals["revenue_kzt"] = _round_money(float(totals["revenue_kzt"]))
    return totals


async def _estimate_ai_cost(
    db: AsyncSession,
    org_id: int,
    start_d: date,
    end_d: date,
    period_tag: str,
) -> float:
    """Оценка стоимости AI за период: подписка (prorate) или токены из AiUsageLog."""
    days = max(1, (end_d - start_d).days + 1)
    sub_monthly = int(getattr(settings, "owner_subscription_monthly_kzt", 0) or 0)
    if sub_monthly > 0:
        return _round_money(sub_monthly * days / 30.0)

    token_row = (
        await db.execute(
            select(func.coalesce(func.sum(AiUsageLog.total_tokens), 0)).where(
                AiUsageLog.organization_id == org_id,
                AiUsageLog.day >= start_d,
                AiUsageLog.day <= end_d,
            ),
        )
    ).one()
    total_tokens = int(token_row[0] or 0)
    # ~30 ₸ за 1K токенов — грубая оценка, если подписка не задана
    return _round_money(total_tokens * 0.03)


async def _prevented_risk_value(
    db: AsyncSession,
    org_id: int,
    ts_lo: datetime,
    ts_hi: datetime,
    *,
    location_id: int | None,
    allowed_location_ids: set[int] | None,
) -> float:
    stmt = select(func.coalesce(func.sum(AiOrderAudit.prevented_value), 0)).where(
        AiOrderAudit.organization_id == org_id,
        AiOrderAudit.created_at >= _sql_dt_for_filter(ts_lo),
        AiOrderAudit.created_at <= _sql_dt_for_filter(ts_hi),
    )
    if location_id is not None:
        stmt = stmt.where(AiOrderAudit.location_id == int(location_id))
    elif allowed_location_ids is not None:
        stmt = stmt.where(AiOrderAudit.location_id.in_(list(allowed_location_ids)))
    total = float(await db.scalar(stmt) or 0)
    return _round_money(total)


async def _count_replacement_events(
    db: AsyncSession,
    org_id: int,
    ts_lo: datetime,
    ts_hi: datetime,
    *,
    location_id: int | None,
    allowed_location_ids: set[int] | None,
) -> int:
    _ = location_id, allowed_location_ids
    stmt = select(func.count(SystemEvent.id)).where(
        SystemEvent.organization_id == int(org_id),
        SystemEvent.event_type == "kitchen_gate.replacement_suggested",
        SystemEvent.created_at >= _sql_dt_for_filter(ts_lo),
        SystemEvent.created_at <= _sql_dt_for_filter(ts_hi),
    )
    return int(await db.scalar(stmt) or 0)


async def _kitchen_gate_impact_block(
    db: AsyncSession,
    org_id: int,
    ts_lo: datetime,
    ts_hi: datetime,
    *,
    location_id: int | None,
    allowed_location_ids: set[int] | None,
) -> dict[str, Any]:
    _ = location_id, allowed_location_ids
    blocked = int(
        await db.scalar(
            select(func.count(SystemEvent.id)).where(
                SystemEvent.organization_id == int(org_id),
                SystemEvent.event_type == "kitchen_gate.order_blocked",
                SystemEvent.created_at >= _sql_dt_for_filter(ts_lo),
                SystemEvent.created_at <= _sql_dt_for_filter(ts_hi),
            ),
        )
        or 0,
    )
    by_rule: dict[str, int] = {}
    rows = (
        await db.execute(
            select(SystemEvent.payload_json).where(
                SystemEvent.organization_id == int(org_id),
                SystemEvent.event_type == "kitchen_gate.order_blocked",
                SystemEvent.created_at >= _sql_dt_for_filter(ts_lo),
                SystemEvent.created_at <= _sql_dt_for_filter(ts_hi),
            ).limit(500),
        )
    ).scalars().all()
    for payload in rows:
        data = payload if isinstance(payload, dict) else {}
        rule = str(data.get("block_rule") or "unknown")
        by_rule[rule] = by_rule.get(rule, 0) + 1
    return {
        "orders_blocked": blocked,
        "by_rule": by_rule,
        "ready": blocked > 0,
        "source": "system_events",
    }


async def _qa_risk_summary(
    db: AsyncSession,
    org_id: int,
    ts_lo: datetime,
    ts_hi: datetime,
    *,
    location_id: int | None,
    allowed_location_ids: set[int] | None,
) -> dict[str, Any]:
    try:
        from app.services.order_ai_audit import build_qa_risk_summary

        return await build_qa_risk_summary(
            db,
            org_id,
            ts_lo=ts_lo,
            ts_hi=ts_hi,
            location_id=location_id,
            allowed_location_ids=allowed_location_ids,
        )
    except ImportError:
        pass
    except Exception:
        logger.exception("order_ai_audit summary failed org=%s", org_id)

    open_stmt = select(func.count(AiOrderAudit.id)).where(
        AiOrderAudit.organization_id == org_id,
        AiOrderAudit.status == "open",
        AiOrderAudit.created_at >= _sql_dt_for_filter(ts_lo),
        AiOrderAudit.created_at <= _sql_dt_for_filter(ts_hi),
    )
    if location_id is not None:
        open_stmt = open_stmt.where(AiOrderAudit.location_id == int(location_id))
    elif allowed_location_ids is not None:
        open_stmt = open_stmt.where(AiOrderAudit.location_id.in_(list(allowed_location_ids)))
    open_count = int(await db.scalar(open_stmt) or 0)
    closed_stmt = select(func.count(AiOrderAudit.id)).where(
        AiOrderAudit.organization_id == org_id,
        AiOrderAudit.status.in_(["reviewed", "dismissed", "resolved"]),
        AiOrderAudit.created_at >= _sql_dt_for_filter(ts_lo),
        AiOrderAudit.created_at <= _sql_dt_for_filter(ts_hi),
    )
    if location_id is not None:
        closed_stmt = closed_stmt.where(AiOrderAudit.location_id == int(location_id))
    elif allowed_location_ids is not None:
        closed_stmt = closed_stmt.where(AiOrderAudit.location_id.in_(list(allowed_location_ids)))
    closed_count = int(await db.scalar(closed_stmt) or 0)
    return {
        "open_count": open_count,
        "closed_count": closed_count,
        "ready": open_count > 0,
        "high_count": 0,
        "critical_count": 0,
    }


async def _upsell_impact_block(
    db: AsyncSession,
    org_id: int,
    period_tag: str,
    ts_lo: datetime,
    ts_hi: datetime,
    metrics: dict[str, Any],
    *,
    location_id: int | None,
    allowed_location_ids: set[int] | None,
) -> dict[str, Any]:
    offered = int(metrics.get("upsell_offers") or 0)
    accepted = int(metrics.get("upsell_accepts") or 0)
    revenue = _round_money(float(metrics.get("upsell_revenue") or 0))
    conversion: float | None = None
    if offered > 0:
        conversion = round(accepted / offered * 100, 1)

    fallback = {
        "offered": offered,
        "accepted": accepted,
        "shown": offered,
        "conversion_rate": conversion,
        "conversion_pct": conversion,
        "added_revenue": revenue,
        "revenue_kzt": revenue,
        "top_pairs": [],
        "best_variants": [],
        "rejected_items": [],
        "source": "orders_items_json",
    }

    try:
        from app.services.upsell_attribution import build_upsell_impact_summary

        attributed = await build_upsell_impact_summary(
            db,
            org_id,
            period=period_tag,
            location_id=location_id,
            allowed_location_ids=allowed_location_ids,
        )
        has_events = int(attributed.get("shown") or attributed.get("offered") or 0) > 0
        has_events = has_events or float(attributed.get("added_revenue") or 0) > 0
        if has_events:
            return attributed
    except ImportError:
        pass
    except Exception:
        logger.exception("upsell_attribution summary failed org=%s", org_id)

    return fallback


def _top_losses_from_leak(leak: dict[str, Any]) -> list[dict[str, Any]]:
    surfaces = list(leak.get("surfaces") or [])
    breakdown = leak.get("breakdown") or {}
    labels = leak.get("labels") or {}
    losses: list[dict[str, Any]] = []
    for surface in surfaces:
        sid = str(surface.get("id") or "")
        losses.append(
            {
                "id": sid,
                "title": str(surface.get("title") or sid),
                "amount_kzt": _round_money(float(surface.get("risk_kzt") or 0)),
                "count": int(surface.get("count") or 0),
                "severity": str(surface.get("severity") or "info"),
            },
        )
    if not losses and breakdown:
        for key, amount in breakdown.items():
            if float(amount or 0) <= 0:
                continue
            label_key = key.replace("_kzt", "")
            losses.append(
                {
                    "id": label_key,
                    "title": str(labels.get(label_key) or label_key),
                    "amount_kzt": _round_money(float(amount)),
                    "count": 0,
                    "severity": "warning",
                },
            )
    losses.sort(key=lambda x: float(x.get("amount_kzt") or 0), reverse=True)
    return losses[:5]


async def _top_actions(
    db: AsyncSession,
    org_id: int,
    leak: dict[str, Any],
) -> list[dict[str, Any]]:
    rec_rows = (
        await db.execute(
            select(BusinessRecommendation)
            .where(
                BusinessRecommendation.organization_id == org_id,
                BusinessRecommendation.status.in_(["new", "viewed"]),
            )
            .order_by(
                BusinessRecommendation.expected_impact_kzt.desc().nulls_last(),
                BusinessRecommendation.created_at.desc(),
            )
            .limit(3),
        )
    ).scalars().all()
    actions: list[dict[str, Any]] = []
    for rec in rec_rows:
        rec_type = str(rec.recommendation_type or "")
        actions.append(
            {
                "id": f"rec_{rec.id}",
                "type": rec_type,
                "title": rec.title,
                "body": rec.body,
                "impact_kzt": rec.expected_impact_kzt,
                "confidence_pct": rec.confidence_pct,
                "cta_label": build_recommendation_target(rec_type).get("label"),
                "target": build_recommendation_target(rec_type),
                "source": "business_recommendation",
            },
        )
    for surface in list(leak.get("surfaces") or [])[:3]:
        surface_actions = list(surface.get("actions") or [])
        if not surface_actions:
            continue
        primary = surface_actions[0]
        actions.append(
            {
                "id": f"leak_{surface.get('id')}",
                "type": "revenue_leak",
                "title": str(surface.get("title") or ""),
                "body": f"Риск {_round_money(float(surface.get('risk_kzt') or 0))} ₸",
                "impact_kzt": surface.get("risk_kzt"),
                "confidence_pct": None,
                "cta_label": str(primary.get("label") or "Открыть"),
                "target": primary,
                "source": "revenue_leak",
            },
        )
        if len(actions) >= 5:
            break
    return actions[:5]


async def _build_owner_intelligence_summary_impl(
    db: AsyncSession,
    organization_id: int,
    *,
    location_id: int | None = None,
    period: str = "today",
    allowed_location_ids: set[int] | None = None,
) -> dict[str, Any]:
    org = await db.get(Organization, int(organization_id))
    org_tz = getattr(org, "timezone", None) if org is not None else None
    ts_lo, ts_hi, start_d, end_d, period_tag = _period_window(period, org_tz)

    metrics = await aggregate_org_window(
        db,
        int(organization_id),
        ts_lo,
        ts_hi,
        location_id=location_id,
        allowed_location_ids=allowed_location_ids,
    )
    daily = await _sum_daily_stats(db, int(organization_id), start_d, end_d)
    leak = await build_revenue_leak(
        db,
        int(organization_id),
        location_id=location_id,
        allowed_location_ids=allowed_location_ids,
    )

    accepted_revenue = _round_money(float(metrics.get("revenue") or 0))
    upsell_revenue = _round_money(float(metrics.get("upsell_revenue") or 0))
    recovered_revenue = _round_money(float(daily.get("recovered_kzt") or 0))
    if recovered_revenue <= 0 and period_tag == "today":
        recovered_revenue = _round_money(float(leak.get("recovered_today_kzt") or 0))

    cancelled_kzt = await _cancelled_revenue_period(
        db,
        int(organization_id),
        ts_lo,
        ts_hi,
        location_id=location_id,
        allowed_location_ids=allowed_location_ids,
    )
    if period_tag == "today":
        lost_revenue = _round_money(float(leak.get("total_leak_kzt") or 0))
    else:
        lost_revenue = max(cancelled_kzt, _round_money(float(leak.get("action_risk_kzt") or 0)))

    prevented_risk_value = await _prevented_risk_value(
        db,
        int(organization_id),
        ts_lo,
        ts_hi,
        location_id=location_id,
        allowed_location_ids=allowed_location_ids,
    )
    ai_cost = await _estimate_ai_cost(db, int(organization_id), start_d, end_d, period_tag)
    net_roi = _round_money(recovered_revenue + upsell_revenue + prevented_risk_value - ai_cost)

    breakdown = leak.get("breakdown") or {}
    menu_confusion_kzt = _round_money(float(breakdown.get("menu_confusion_kzt") or 0))
    menu_confusion_count = 0
    for surface in list(leak.get("surfaces") or []):
        if str(surface.get("id")) == "menu_confusion":
            menu_confusion_count = int(surface.get("count") or 0)
            break

    upsell_impact = await _upsell_impact_block(
        db,
        int(organization_id),
        period_tag,
        ts_lo,
        ts_hi,
        metrics,
        location_id=location_id,
        allowed_location_ids=allowed_location_ids,
    )
    recovery_impact = {
        "recovered_kzt": recovered_revenue,
        "draft_recovery_sent": int(daily.get("draft_recovery_sent") or 0),
        "focus_completed_count": int(daily.get("focus_completed_count") or 0),
        "source": "daily_org_stats",
    }
    stoplist_impact = {
        "incidents": menu_confusion_count,
        "estimated_loss_kzt": menu_confusion_kzt,
        "replacements_suggested": await _count_replacement_events(
            db,
            int(organization_id),
            ts_lo,
            ts_hi,
            location_id=location_id,
            allowed_location_ids=allowed_location_ids,
        ),
        "source": "revenue_leak_menu_confusion",
    }

    event_rows = await get_event_stats_for_range(
        db,
        int(organization_id),
        start_date=start_d,
        end_date=end_d,
    )
    event_revenue = _round_money(sum(float(r.get("revenue_kzt") or 0) for r in event_rows))
    if accepted_revenue <= 0 and event_revenue > 0 and location_id is None and allowed_location_ids is None:
        accepted_revenue = event_revenue

    menu_profit_preview: dict[str, Any] = {
        "promote_today": [],
        "price_increase_candidates": [],
        "price_recommendations": [],
        "missing_cost_checklist": {
            "total_items": 0,
            "missing_count": 0,
            "missing_pct": 0.0,
            "has_cost_count": 0,
            "onboarding_complete": False,
            "top_missing": [],
        },
        "promote_today_copilot": [],
    }
    try:
        from app.services.menu_profit_lab import build_menu_profit_report, get_copilot_candidate_lists

        menu_period = period_tag if period_tag != "today" else "7d"
        menu_report = await build_menu_profit_report(
            db,
            int(organization_id),
            location_id=location_id,
            period=menu_period,
            allowed_location_ids=allowed_location_ids,
        )
        copilot_feed = await get_copilot_candidate_lists(
            db,
            int(organization_id),
            period=menu_period,
            location_id=location_id,
            allowed_location_ids=allowed_location_ids,
        )
        menu_profit_preview = {
            "promote_today": (copilot_feed.get("promote_today_candidates") or [])[:5],
            "price_increase_candidates": (menu_report.get("price_increase_candidates") or [])[:5],
            "price_recommendations": (menu_report.get("price_recommendations") or [])[:5],
            "missing_cost_checklist": menu_report.get("missing_cost_checklist")
            or menu_profit_preview["missing_cost_checklist"],
            "promote_today_copilot": (menu_report.get("promote_today_copilot") or [])[:5],
        }
    except Exception as exc:
        logger.debug("menu_profit_preview skipped org=%s: %s", organization_id, exc)

    location_benchmark_preview: dict[str, Any] = {"enabled": False}
    try:
        from app.services.network_benchmark import build_network_benchmark

        bench = await build_network_benchmark(
            db,
            int(organization_id),
            period=period_tag if period_tag != "today" else "7d",
            allowed_location_ids=allowed_location_ids,
        )
        if bench.get("enabled"):
            location_benchmark_preview = {
                "enabled": True,
                "org_revenue_kzt": bench.get("org_revenue_kzt"),
                "network_avg_kzt": bench.get("network_avg_kzt"),
                "rank_label": bench.get("rank_label"),
                "decline_reasons": bench.get("decline_reasons") or [],
                "top_decline_reason": bench.get("top_decline_reason"),
                "location_decline_reasons": bench.get("location_decline_reasons") or [],
                "network_averages": bench.get("network_averages") or {},
                "best_location": bench.get("best_location"),
                "worst_location": bench.get("worst_location"),
                "locations": (bench.get("locations") or [])[:5],
            }
    except Exception as exc:
        logger.debug("location_benchmark_preview skipped org=%s: %s", organization_id, exc)

    summary: dict[str, object] = {
        "period": period_tag,
        "from": ts_lo.isoformat(),
        "to": ts_hi.isoformat(),
        "accepted_revenue": accepted_revenue,
        "recovered_revenue": recovered_revenue,
        "upsell_revenue": upsell_revenue,
        "lost_revenue": lost_revenue,
        "prevented_risk_value": prevented_risk_value,
        "ai_cost": ai_cost,
        "net_roi": net_roi,
        "top_losses": _top_losses_from_leak(leak),
        "top_actions": await _top_actions(db, int(organization_id), leak),
        "stoplist_impact": stoplist_impact,
        "upsell_impact": upsell_impact,
        "recovery_impact": recovery_impact,
        "qa_risk_summary": await _qa_risk_summary(
            db,
            int(organization_id),
            ts_lo,
            ts_hi,
            location_id=location_id,
            allowed_location_ids=allowed_location_ids,
        ),
        "kitchen_gate_impact": await _kitchen_gate_impact_block(
            db,
            int(organization_id),
            ts_lo,
            ts_hi,
            location_id=location_id,
            allowed_location_ids=allowed_location_ids,
        ),
        "menu_profit_preview": menu_profit_preview,
        "location_benchmark_preview": location_benchmark_preview,
        "location_id": location_id,
    }

    try:
        from app.services.llm_reliability import build_llm_reliability_metrics

        summary["llm_reliability"] = await build_llm_reliability_metrics(
            db, int(organization_id), period_days=7 if period_tag == "7d" else 1,
        )
    except Exception as exc:
        logger.debug("llm_reliability metrics skipped org=%s: %s", organization_id, exc)

    return summary


async def build_owner_intelligence_summary(
    db: AsyncSession,
    organization_id: int,
    location_id: int | None = None,
    period: str = "today",
    allowed_location_ids: set[int] | None = None,
) -> dict[str, Any]:
    """Сводка Owner Intelligence за период (today | 7d | 30d) с учётом location scope."""
    return await with_location_scope_fallback(
        db=db,
        location_id=location_id,
        allowed_location_ids=allowed_location_ids,
        run=lambda loc_id, allowed: _build_owner_intelligence_summary_impl(
            db,
            organization_id,
            location_id=loc_id,
            period=period,
            allowed_location_ids=allowed,
        ),
    )
