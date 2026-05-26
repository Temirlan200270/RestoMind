"""Network Benchmark — сравнение филиалов сети для Owner Intelligence (Stage 6)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import AiOrderAudit, DailyOrgStats, Order, OrderStatus, Organization, SystemEvent, Tenant, UpsellOfferEvent
from app.services.tenant_scope import _tenant_org_list, orders_location_filter, orders_tenant_clause

_COMPLETED = (
    OrderStatus.CONFIRMED.value,
    OrderStatus.SENDING_TO_IIKO.value,
    OrderStatus.SENT_TO_IIKO.value,
    OrderStatus.IN_TRANSIT.value,
    OrderStatus.WAITING_PICKUP.value,
    OrderStatus.COMPLETED.value,
)


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _sql_dt(dt: datetime) -> datetime:
    u = _utc(dt)
    return u.replace(tzinfo=None) if settings.db_mode == "sqlite" else u


def _period_bounds(period: str) -> tuple[datetime, datetime, str]:
    now = datetime.now(timezone.utc)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    p = (period or "7d").strip().lower()
    if p in {"today", "сегодня"}:
        return today, now, "today"
    if p in {"30d", "month", "месяц"}:
        return today - timedelta(days=30), now, "30d"
    return today - timedelta(days=7), now, "7d"


def _disabled(reason: str, period: str) -> dict[str, Any]:
    return {
        "enabled": False,
        "reason": reason,
        "period": period,
        "locations": [],
    }


async def _org_metrics(
    db: AsyncSession,
    org_id: int,
    start: datetime,
    end: datetime,
    allowed_location_ids: set[int] | None,
) -> dict[str, Any]:
    org_orders = orders_tenant_clause(org_id)
    order_scope = orders_location_filter(allowed_location_ids, None)
    start_sql = _sql_dt(start)
    end_sql = _sql_dt(end)
    day_from = start.date()
    day_to = end.date()

    revenue_row = (
        await db.execute(
            select(
                func.coalesce(func.sum(Order.total_price), 0),
                func.count(Order.id),
            ).where(
                org_orders,
                order_scope,
                Order.status.in_(list(_COMPLETED)),
                Order.created_at >= start_sql,
                Order.created_at <= end_sql,
            ),
        )
    ).one()
    order_count = int(revenue_row[1] or 0)
    revenue = round(float(revenue_row[0] or 0), 2)
    avg_check = round(revenue / order_count, 2) if order_count else 0.0

    cancelled_row = (
        await db.execute(
            select(func.count(Order.id), func.coalesce(func.sum(Order.total_price), 0)).where(
                org_orders,
                order_scope,
                Order.status == OrderStatus.CANCELLED.value,
                Order.created_at >= start_sql,
                Order.created_at <= end_sql,
            ),
        )
    ).one()
    cancellation_count = int(cancelled_row[0] or 0)
    lost_revenue = round(float(cancelled_row[1] or 0), 2)
    if lost_revenue <= 0 and cancellation_count > 0:
        lost_revenue = round(cancellation_count * (avg_check or 0), 2)

    stats_rows = (
        await db.execute(
            select(
                func.coalesce(func.sum(DailyOrgStats.recovered_kzt), 0),
            ).where(
                DailyOrgStats.organization_id == int(org_id),
                DailyOrgStats.day >= day_from,
                DailyOrgStats.day <= day_to,
            ),
        )
    ).one()
    recovered_revenue = round(float(stats_rows[0] or 0), 2)

    upsell_revenue = round(
        float(
            await db.scalar(
                select(func.coalesce(func.sum(UpsellOfferEvent.added_revenue), 0)).where(
                    UpsellOfferEvent.organization_id == int(org_id),
                    UpsellOfferEvent.status == "accepted",
                    UpsellOfferEvent.created_at >= start_sql,
                    UpsellOfferEvent.created_at <= end_sql,
                    _upsell_location_filter(allowed_location_ids),
                ),
            )
            or 0,
        ),
        2,
    )

    stoplist_incidents = int(
        await db.scalar(
            select(func.count(SystemEvent.id)).where(
                SystemEvent.organization_id == int(org_id),
                SystemEvent.event_type == "stoplist_update",
                SystemEvent.created_at >= start_sql,
                SystemEvent.created_at <= end_sql,
            ),
        )
        or 0,
    )

    qa_risk_count = int(
        await db.scalar(
            select(func.count(AiOrderAudit.id)).where(
                AiOrderAudit.organization_id == int(org_id),
                AiOrderAudit.status == "open",
                AiOrderAudit.risk_level.in_(("medium", "high", "critical")),
                AiOrderAudit.created_at >= start_sql,
                AiOrderAudit.created_at <= end_sql,
            ),
        )
        or 0,
    )

    return {
        "organization_id": int(org_id),
        "revenue": revenue,
        "lost_revenue": lost_revenue,
        "recovered_revenue": recovered_revenue,
        "upsell_revenue": upsell_revenue,
        "order_count": order_count,
        "avg_check": avg_check,
        "stoplist_incidents": stoplist_incidents,
        "qa_risk_count": qa_risk_count,
        "cancellation_count": cancellation_count,
    }


def _upsell_location_filter(allowed_location_ids: set[int] | None):
    from sqlalchemy import or_

    if allowed_location_ids is None:
        return True
    if not allowed_location_ids:
        return UpsellOfferEvent.id == -1
    return or_(
        UpsellOfferEvent.location_id.is_(None),
        UpsellOfferEvent.location_id.in_(list(allowed_location_ids)),
    )


def _score_location(row: dict[str, Any]) -> float:
    return (
        float(row["revenue"])
        + float(row["recovered_revenue"])
        + float(row["upsell_revenue"])
        - float(row["lost_revenue"])
        - float(row["stoplist_incidents"]) * 500.0
        - float(row["qa_risk_count"]) * 1000.0
        - float(row["cancellation_count"]) * 300.0
    )


def _main_issue(row: dict[str, Any]) -> str:
    issues = [
        ("lost_revenue", float(row["lost_revenue"]), "Высокие потери от отмен"),
        ("stoplist_incidents", float(row["stoplist_incidents"]), "Частые стоп-листы"),
        ("qa_risk_count", float(row["qa_risk_count"]), "Рискованные AI-заказы"),
        ("cancellation_count", float(row["cancellation_count"]), "Много отмен"),
    ]
    key, _val, label = max(issues, key=lambda x: x[1])
    if _val <= 0:
        return "Стабильная работа"
    return label


def _rank_label(rank: int, total: int) -> str:
    if total <= 0 or rank <= 0:
        return "—"
    if rank == 1:
        return f"Лидер ({rank} из {total})"
    if rank == total and total > 1:
        return f"Отстаёт ({rank} из {total})"
    return f"{rank} из {total}"


def _delta_vs_avg(value: float, avg: float) -> dict[str, float | None]:
    delta_kzt = round(value - avg, 2)
    delta_pct = round((delta_kzt / avg) * 100.0, 1) if avg > 0 else None
    return {"delta_kzt": delta_kzt, "delta_pct": delta_pct}


def _enrich_location_row(row: dict[str, Any], network_avgs: dict[str, float]) -> None:
    """Добавляет DTO-поля для UI/API: org_revenue_kzt, network_avg_kzt, recovery, delta_vs_avg."""
    revenue = float(row.get("revenue") or 0)
    avg_revenue = float(network_avgs.get("revenue") or 0)
    row["org_revenue_kzt"] = round(revenue, 2)
    row["network_avg_kzt"] = avg_revenue
    row["recovery"] = round(float(row.get("recovered_revenue") or 0), 2)
    row["delta_vs_avg"] = {
        "revenue": _delta_vs_avg(revenue, avg_revenue),
        "lost_revenue": _delta_vs_avg(float(row.get("lost_revenue") or 0), float(network_avgs.get("lost_revenue") or 0)),
        "upsell_revenue": _delta_vs_avg(float(row.get("upsell_revenue") or 0), float(network_avgs.get("upsell_revenue") or 0)),
        "recovery": _delta_vs_avg(float(row.get("recovered_revenue") or 0), float(network_avgs.get("recovered_revenue") or 0)),
        "stoplist_incidents": {
            "delta": int(row.get("stoplist_incidents") or 0) - int(round(network_avgs.get("stoplist_incidents") or 0)),
            "network_avg": round(network_avgs.get("stoplist_incidents") or 0, 1),
        },
        "qa_risk_count": {
            "delta": int(row.get("qa_risk_count") or 0) - int(round(network_avgs.get("qa_risk_count") or 0)),
            "network_avg": round(network_avgs.get("qa_risk_count") or 0, 1),
        },
    }


def _network_metric_averages(locations: list[dict[str, Any]]) -> dict[str, float]:
    if not locations:
        return {
            "revenue": 0.0,
            "lost_revenue": 0.0,
            "recovered_revenue": 0.0,
            "upsell_revenue": 0.0,
            "stoplist_incidents": 0.0,
            "qa_risk_count": 0.0,
            "cancellation_count": 0.0,
        }
    count = float(len(locations))
    return {
        "revenue": round(sum(float(row.get("revenue") or 0) for row in locations) / count, 2),
        "lost_revenue": round(sum(float(row.get("lost_revenue") or 0) for row in locations) / count, 2),
        "recovered_revenue": round(sum(float(row.get("recovered_revenue") or 0) for row in locations) / count, 2),
        "upsell_revenue": round(sum(float(row.get("upsell_revenue") or 0) for row in locations) / count, 2),
        "stoplist_incidents": round(sum(float(row.get("stoplist_incidents") or 0) for row in locations) / count, 2),
        "qa_risk_count": round(sum(float(row.get("qa_risk_count") or 0) for row in locations) / count, 2),
        "cancellation_count": round(sum(float(row.get("cancellation_count") or 0) for row in locations) / count, 2),
    }


def _decline_reasons(
    row: dict[str, Any] | None,
    network_avg: float,
    network_avgs: dict[str, float] | None = None,
) -> list[str]:
    if row is None:
        return []
    avgs = network_avgs or {}
    reasons: list[str] = []
    home_revenue = float(row.get("revenue") or 0)
    avg_revenue = float(avgs.get("revenue") or network_avg or 0)
    if avg_revenue > 0 and home_revenue < avg_revenue * 0.85:
        reasons.append("Выручка ниже среднего по сети")
    avg_lost = float(avgs.get("lost_revenue") or 0)
    if float(row.get("lost_revenue") or 0) > max(avg_lost * 1.25, avg_revenue * 0.05, 1000.0):
        reasons.append("Высокие потери от отмен")
    avg_stoplist = float(avgs.get("stoplist_incidents") or 0)
    stoplist_incidents = int(row.get("stoplist_incidents") or 0)
    if stoplist_incidents >= max(2, int(round(avg_stoplist + 1))):
        reasons.append("Частые стоп-листы")
    avg_qa = float(avgs.get("qa_risk_count") or 0)
    qa_count = int(row.get("qa_risk_count") or 0)
    if qa_count > 0 and qa_count >= max(1, int(round(avg_qa + 0.5))):
        reasons.append("Рискованные AI-заказы")
    avg_upsell = float(avgs.get("upsell_revenue") or 0)
    upsell_revenue = float(row.get("upsell_revenue") or 0)
    if avg_upsell > 0 and upsell_revenue < avg_upsell * 0.6:
        reasons.append("Слабый upsell относительно сети")
    avg_recovery = float(avgs.get("recovered_revenue") or 0)
    recovered_revenue = float(row.get("recovered_revenue") or 0)
    if avg_recovery > 0 and recovered_revenue < avg_recovery * 0.5:
        reasons.append("Слабое восстановление черновиков")
    avg_cancel = float(avgs.get("cancellation_count") or 0)
    cancellation_count = int(row.get("cancellation_count") or 0)
    if cancellation_count >= max(3, int(round(avg_cancel + 1))):
        reasons.append("Много отмен за период")
    if not reasons and avg_revenue > 0 and home_revenue >= avg_revenue * 0.95:
        reasons.append("Показатели в норме относительно сети")
    return reasons[:5]


def _network_summary_dto(
    org_id: int,
    ranked: list[dict[str, Any]],
    network_avgs: dict[str, float],
) -> dict[str, Any]:
    home = next((row for row in ranked if int(row["organization_id"]) == int(org_id)), None)
    network_avg = float(network_avgs.get("revenue") or 0)
    org_revenue = round(float(home.get("revenue") or 0), 2) if home else 0.0
    rank = int(home.get("rank") or 0) if home else 0
    total = len(ranked)
    decline_reasons = _decline_reasons(home, network_avg, network_avgs)
    return {
        "org_revenue_kzt": org_revenue,
        "network_avg_kzt": network_avg,
        "rank_label": _rank_label(rank, total),
        "decline_reasons": decline_reasons,
        "top_decline_reason": decline_reasons[0] if decline_reasons else None,
        "network_averages": network_avgs,
    }


async def build_network_benchmark(
    db: AsyncSession,
    org_id: int,
    period: str = "7d",
    allowed_location_ids: set[int] | None = None,
) -> dict[str, Any]:
    """Сравнение филиалов сети; для одиночного ресторана — disabled."""
    start, end, label = _period_bounds(period)
    home = await db.get(Organization, int(org_id))
    if home is None:
        return _disabled("organization_not_found", label)

    if home.tenant_id is None:
        return _disabled("single_location", label)

    tenant = await db.get(Tenant, int(home.tenant_id))
    if tenant is None or not bool(getattr(tenant, "is_network", False)):
        return _disabled("single_location", label)

    orgs = await _tenant_org_list(db, int(tenant.id))
    if len(orgs) < 2:
        return _disabled("single_location", label)

    locations: list[dict[str, Any]] = []
    for org in orgs:
        metrics = await _org_metrics(db, int(org.id), start, end, allowed_location_ids)
        metrics["name"] = org.name
        metrics["slug"] = org.slug
        metrics["score"] = round(_score_location(metrics), 2)
        metrics["main_issue"] = _main_issue(metrics)
        locations.append(metrics)

    network_avgs = _network_metric_averages(locations)
    for row in locations:
        _enrich_location_row(row, network_avgs)
        row["decline_reasons"] = _decline_reasons(row, float(network_avgs.get("revenue") or 0), network_avgs)
        row["top_decline_reason"] = (row["decline_reasons"][0] if row.get("decline_reasons") else None)
        row["vs_network"] = {
            "revenue_delta_pct": round(
                ((float(row.get("revenue") or 0) - float(network_avgs.get("revenue") or 0))
                 / max(float(network_avgs.get("revenue") or 0), 1.0)) * 100.0,
                1,
            ),
            "upsell_delta_pct": round(
                ((float(row.get("upsell_revenue") or 0) - float(network_avgs.get("upsell_revenue") or 0))
                 / max(float(network_avgs.get("upsell_revenue") or 0), 1.0)) * 100.0,
                1,
            ) if float(network_avgs.get("upsell_revenue") or 0) > 0 else None,
            "recovery_delta_pct": round(
                ((float(row.get("recovered_revenue") or 0) - float(network_avgs.get("recovered_revenue") or 0))
                 / max(float(network_avgs.get("recovered_revenue") or 0), 1.0)) * 100.0,
                1,
            ) if float(network_avgs.get("recovered_revenue") or 0) > 0 else None,
            "stoplist_delta": int(row.get("stoplist_incidents") or 0) - int(round(network_avgs.get("stoplist_incidents") or 0)),
            "qa_delta": int(row.get("qa_risk_count") or 0) - int(round(network_avgs.get("qa_risk_count") or 0)),
        }

    ranked = sorted(locations, key=lambda x: float(x["score"]), reverse=True)
    for idx, row in enumerate(ranked, start=1):
        row["rank"] = idx

    best = ranked[0] if ranked else None
    worst = ranked[-1] if ranked else None
    summary = _network_summary_dto(int(org_id), ranked, network_avgs)
    practice_transfers = _practice_transfer_suggestions(ranked, network_avgs)

    return {
        "enabled": True,
        "period": label,
        "tenant_id": int(tenant.id),
        "tenant_name": tenant.name,
        "date_from": start.date().isoformat(),
        "date_to": end.date().isoformat(),
        "locations": ranked,
        "best_location": best,
        "worst_location": worst,
        "practice_transfers": practice_transfers,
        "recommended_actions": _recommended_actions(worst, best, practice_transfers),
        "location_decline_reasons": [
            {
                "organization_id": int(row["organization_id"]),
                "name": row.get("name"),
                "reasons": row.get("decline_reasons") or [],
            }
            for row in ranked
            if row.get("decline_reasons")
        ],
        **summary,
    }


def _practice_transfer_suggestions(
    ranked: list[dict[str, Any]],
    network_avgs: dict[str, float],
) -> list[dict[str, str]]:
    """Конкретные предложения переноса практик между филиалами."""
    if len(ranked) < 2:
        return []
    suggestions: list[dict[str, str]] = []
    avg_upsell = float(network_avgs.get("upsell_revenue") or 0)
    avg_recovery = float(network_avgs.get("recovered_revenue") or 0)

    upsell_leader = max(ranked, key=lambda r: float(r.get("upsell_revenue") or 0))
    recovery_leader = max(ranked, key=lambda r: float(r.get("recovered_revenue") or 0))

    for row in ranked:
        if int(row.get("rank") or 0) <= 2:
            continue
        name = str(row.get("name") or "Филиал")
        gaps: list[tuple[str, dict[str, Any], str]] = []

        upsell_rev = float(row.get("upsell_revenue") or 0)
        if avg_upsell > 0 and upsell_rev < avg_upsell * 0.7:
            leader_upsell = float(upsell_leader.get("upsell_revenue") or 0)
            if leader_upsell > upsell_rev * 1.3 and int(upsell_leader["organization_id"]) != int(row["organization_id"]):
                gaps.append(("upsell", upsell_leader, f"upsell {int(leader_upsell):,} ₸ vs {int(upsell_rev):,} ₸".replace(",", " ")))

        recovery_rev = float(row.get("recovered_revenue") or 0)
        if avg_recovery > 0 and recovery_rev < avg_recovery * 0.6:
            leader_recovery = float(recovery_leader.get("recovered_revenue") or 0)
            if leader_recovery > recovery_rev * 1.5 and int(recovery_leader["organization_id"]) != int(row["organization_id"]):
                gaps.append(("recovery", recovery_leader, f"recovery {int(leader_recovery):,} ₸ vs {int(recovery_rev):,} ₸".replace(",", " ")))

        stoplist = int(row.get("stoplist_incidents") or 0)
        avg_stop = float(network_avgs.get("stoplist_incidents") or 0)
        if stoplist >= max(2, int(round(avg_stop + 1))):
            best_ops = min(ranked, key=lambda r: int(r.get("stoplist_incidents") or 0))
            if int(best_ops["organization_id"]) != int(row["organization_id"]):
                gaps.append(("stoplist", best_ops, f"стоп-листы {stoplist} vs {int(best_ops.get('stoplist_incidents') or 0)} у лидера"))

        for metric, leader, detail in gaps[:2]:
            suggestions.append({
                "from_org": str(leader.get("name") or ""),
                "to_org": name,
                "metric": metric,
                "text": f"Перенести практику «{metric}» из «{leader.get('name')}» в «{name}» ({detail})",
            })

    return suggestions[:6]


def _recommended_actions(
    worst: dict[str, Any] | None,
    best: dict[str, Any] | None,
    practice_transfers: list[dict[str, str]] | None = None,
) -> list[str]:
    actions: list[str] = []
    if worst is None:
        return actions
    if float(worst.get("lost_revenue") or 0) > 0:
        actions.append(f"Разобрать отмены в «{worst['name']}» — потери ~{int(worst['lost_revenue']):,} ₸".replace(",", " "))
    if int(worst.get("stoplist_incidents") or 0) >= 2:
        actions.append(f"Проверить поставки и стоп-лист в «{worst['name']}»")
    if int(worst.get("qa_risk_count") or 0) > 0:
        actions.append(f"Проверить {worst['qa_risk_count']} рискованных AI-заказов в «{worst['name']}»")
    if best is not None and float(best.get("upsell_revenue") or 0) > float(worst.get("upsell_revenue") or 0):
        actions.append(f"Перенести upsell-практики из «{best['name']}» в «{worst['name']}»")
    for transfer in (practice_transfers or [])[:2]:
        text = str(transfer.get("text") or "")
        if text and text not in actions:
            actions.append(text)
    if not actions:
        actions.append("Сеть работает стабильно — сфокусируйтесь на росте среднего чека")
    return actions[:6]
