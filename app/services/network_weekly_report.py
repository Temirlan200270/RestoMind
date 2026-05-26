"""Weekly network benchmark report for Owner Intelligence (Network Benchmark v2)."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.network_benchmark import build_network_benchmark


def _weekly_headline(bench: dict[str, Any]) -> str:
    if not bench.get("enabled"):
        return "Бенчмарк сети недоступен для одиночной точки"
    rank_label = str(bench.get("rank_label") or "—")
    org_rev = float(bench.get("org_revenue_kzt") or 0)
    avg_rev = float(bench.get("network_avg_kzt") or 0)
    if avg_rev <= 0:
        return f"Недельный срез: {rank_label}"
    delta_pct = round((org_rev - avg_rev) / avg_rev * 100.0, 1)
    if delta_pct >= 5:
        return f"Неделя: {rank_label} · выше среднего на {delta_pct}%"
    if delta_pct <= -5:
        return f"Неделя: {rank_label} · ниже среднего на {abs(delta_pct)}%"
    return f"Неделя: {rank_label} · около среднего по сети"


def _location_decline_narratives(locations: list[dict[str, Any]], network_avgs: dict[str, float]) -> list[str]:
    """«Точка A просела из-за X» — по каждому отстающему филиалу."""
    narratives: list[str] = []
    avg_revenue = float(network_avgs.get("revenue") or 0)
    if avg_revenue <= 0:
        return narratives

    for row in locations:
        revenue = float(row.get("org_revenue_kzt") or row.get("revenue") or 0)
        if revenue >= avg_revenue * 0.9:
            continue
        name = str(row.get("name") or "Филиал")
        reason = row.get("top_decline_reason") or row.get("main_issue")
        if not reason and row.get("decline_reasons"):
            reason = row["decline_reasons"][0]
        if reason and reason != "Показатели в норме относительно сети":
            delta_pct = round((revenue - avg_revenue) / avg_revenue * 100.0, 1)
            narratives.append(f"«{name}» просела на {abs(delta_pct)}% — {str(reason).lower()}")
        elif revenue < avg_revenue * 0.85:
            delta_pct = round((revenue - avg_revenue) / avg_revenue * 100.0, 1)
            narratives.append(f"«{name}» просела на {abs(delta_pct)}% относительно среднего по сети")

    return narratives[:5]


def _upsell_leader_narrative(locations: list[dict[str, Any]], network_avgs: dict[str, float]) -> str | None:
    """«Точка B лидер по upsell»."""
    if not locations:
        return None
    leader = max(locations, key=lambda r: float(r.get("upsell_revenue") or 0))
    upsell = float(leader.get("upsell_revenue") or 0)
    avg_upsell = float(network_avgs.get("upsell_revenue") or 0)
    if upsell <= 0:
        return None
    name = str(leader.get("name") or "Филиал")
    if avg_upsell > 0 and upsell >= avg_upsell * 1.2:
        delta_pct = round((upsell - avg_upsell) / avg_upsell * 100.0, 1)
        return f"«{name}» — лидер по upsell (+{delta_pct}% к среднему, {int(upsell):,} ₸)".replace(",", " ")
    if upsell > 0 and len(locations) >= 2:
        return f"«{name}» — лидер по upsell ({int(upsell):,} ₸ за период)".replace(",", " ")
    return None


def _build_weekly_narratives(bench: dict[str, Any]) -> list[str]:
    """Собирает actionable weekly text из метрик бенчмарка."""
    if not bench.get("enabled"):
        return []
    locations = bench.get("locations") or []
    network_avgs = bench.get("network_averages") or {}
    narratives: list[str] = []

    upsell_line = _upsell_leader_narrative(locations, network_avgs)
    if upsell_line:
        narratives.append(upsell_line)

    narratives.extend(_location_decline_narratives(locations, network_avgs))

    best = bench.get("best_location") or {}
    worst = bench.get("worst_location") or {}
    if best.get("name") and worst.get("name"):
        if int(best.get("organization_id") or 0) != int(worst.get("organization_id") or 0):
            narratives.append(f"Лидер недели: «{best['name']}» · требует внимания: «{worst['name']}»")

    for transfer in (bench.get("practice_transfers") or [])[:3]:
        text = str(transfer.get("text") or "")
        if text:
            narratives.append(text)

    return narratives[:8]


async def build_network_weekly_report(
    db: AsyncSession,
    organization_id: int,
    period: str = "7d",
    allowed_location_ids: set[int] | None = None,
) -> dict[str, Any]:
    """Сводный недельный отчёт по сети на базе network benchmark."""
    bench = await build_network_benchmark(
        db,
        int(organization_id),
        period=period,
        allowed_location_ids=allowed_location_ids,
    )
    narratives = _build_weekly_narratives(bench)
    highlights: list[str] = list(narratives)
    if bench.get("enabled") and not highlights:
        best = bench.get("best_location") or {}
        if best.get("name"):
            highlights.append(f"Лидер недели: «{best['name']}»")
        for action in (bench.get("recommended_actions") or [])[:2]:
            highlights.append(str(action))

    return {
        "period": bench.get("period") or period,
        "enabled": bool(bench.get("enabled")),
        "reason": bench.get("reason"),
        "headline": _weekly_headline(bench),
        "highlights": highlights[:8],
        "narratives": narratives,
        "practice_transfers": bench.get("practice_transfers") or [],
        "benchmark": bench,
        "org_revenue_kzt": bench.get("org_revenue_kzt"),
        "network_avg_kzt": bench.get("network_avg_kzt"),
        "rank_label": bench.get("rank_label"),
        "decline_reasons": bench.get("decline_reasons") or [],
        "top_decline_reason": bench.get("top_decline_reason"),
        "location_decline_reasons": bench.get("location_decline_reasons") or [],
        "network_averages": bench.get("network_averages") or {},
        "locations": bench.get("locations") or [],
        "recommended_actions": bench.get("recommended_actions") or [],
    }
