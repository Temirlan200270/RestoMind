"""Menu Profit Lab — рекомендации по меню для Owner Intelligence (Stage 5)."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import DishMarginProfile, MenuItem, Order, OrderStatus, SystemEvent, UpsellOfferEvent
from app.services.intelligence_analytics import menu_engineering_rows
from app.services.restaurant_graph import rebuild_restaurant_graph_profiles
from app.services.tenant_scope import orders_location_filter, orders_tenant_clause

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


def _norm_name(name: str) -> str:
    return " ".join((name or "").strip().lower().split())


def _item_public(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "menu_item_id": row.get("menu_item_id"),
        "name": row.get("name"),
        "category": row.get("category"),
        "iiko_id": row.get("iiko_id"),
        "price": round(float(row.get("price") or 0), 2),
        "cost_price": round(float(row["cost_price"]), 2) if row.get("cost_price") is not None else None,
        "quantity_sold": int(row.get("quantity_sold") or 0),
        "revenue": round(float(row.get("revenue") or 0), 2),
        "margin_pct": round(float(row["margin_pct"]), 1) if row.get("margin_pct") is not None else None,
        "margin_source": row.get("margin_source"),
        "margin_confidence_score": (
            round(float(row["margin_confidence_score"]), 4) if row.get("margin_confidence_score") is not None else None
        ),
        "is_available": bool(row.get("is_available", True)),
        "reason": row.get("reason"),
        "score": round(float(row.get("score") or 0), 2),
        "recommended_increase_pct": row.get("recommended_increase_pct"),
    }


def _recommended_price_increase_pct(row: dict[str, Any]) -> float:
    margin = row.get("margin_pct")
    if margin is not None:
        margin_f = float(margin)
        if margin_f >= 55.0:
            return 5.0
        if margin_f >= 45.0:
            return 7.0
        if margin_f >= 35.0:
            return 10.0
        return 12.0
    return 8.0


def build_price_recommendations(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Полные объекты рекомендаций по цене для Menu Profit Lab."""
    out: list[dict[str, Any]] = []
    for row in items:
        current_price = round(float(row.get("price") or 0), 2)
        if current_price <= 0:
            continue
        cost_raw = row.get("cost_price")
        cost_price = round(float(cost_raw), 2) if cost_raw is not None else None
        margin_pct = row.get("margin_pct")
        if margin_pct is None and cost_price is not None:
            margin_pct = round((current_price - cost_price) / current_price * 100.0, 1)
        elif margin_pct is not None:
            margin_pct = round(float(margin_pct), 1)

        increase_pct = float(
            row.get("recommended_increase_pct")
            if row.get("recommended_increase_pct") is not None
            else _recommended_price_increase_pct(row),
        )
        recommended_price = round(current_price * (1.0 + increase_pct / 100.0), 2)

        qty = int(row.get("quantity_sold") or 0)
        expected_margin_lift = 0.0
        if cost_price is not None and qty > 0:
            old_unit_margin = current_price - cost_price
            new_unit_margin = recommended_price - cost_price
            expected_margin_lift = round((new_unit_margin - old_unit_margin) * qty, 2)

        rec = _item_public(row)
        rec.update(
            {
                "current_price": current_price,
                "cost_price": cost_price,
                "margin_pct": margin_pct,
                "recommended_price": recommended_price,
                "recommended_increase_pct": round(increase_pct, 1),
                "expected_margin_lift": expected_margin_lift,
            },
        )
        out.append(rec)
    return sorted(out, key=lambda x: (-float(x.get("expected_margin_lift") or 0), -float(x.get("revenue") or 0)))


async def build_missing_cost_checklist(
    db: AsyncSession,
    org_id: int,
) -> dict[str, Any]:
    """Онбординг: сколько позиций без себестоимости и топ для заполнения."""
    org_id = int(org_id)
    menu_rows = (
        await db.execute(
            select(MenuItem).where(MenuItem.organization_id == org_id),
        )
    ).scalars().all()
    total = len(menu_rows)
    missing = [mi for mi in menu_rows if mi.cost_price is None]
    missing_count = len(missing)
    missing_pct = round(100.0 * missing_count / total, 1) if total else 0.0
    top_missing = sorted(
        missing,
        key=lambda mi: (-float(mi.price or 0), str(mi.name or "")),
    )[:10]
    return {
        "total_items": total,
        "missing_count": missing_count,
        "missing_pct": missing_pct,
        "has_cost_count": total - missing_count,
        "onboarding_complete": total > 0 and missing_count == 0,
        "top_missing": [
            {
                "menu_item_id": int(mi.id),
                "name": mi.name,
                "category": mi.category or "",
                "price": round(float(mi.price or 0), 2),
                "iiko_id": mi.iiko_id,
                "is_available": bool(mi.is_available),
            }
            for mi in top_missing
        ],
    }


def promote_today_for_copilot(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Явный экспорт для Revenue Copilot: reason + score на каждую позицию."""
    out: list[dict[str, Any]] = []
    for row in candidates:
        out.append(
            {
                "menu_item_id": row.get("menu_item_id"),
                "iiko_id": row.get("iiko_id"),
                "name": row.get("name"),
                "category": row.get("category"),
                "reason": row.get("reason") or "promote_today",
                "score": round(float(row.get("score") or 0), 2),
                "margin_pct": row.get("margin_pct"),
                "revenue": round(float(row.get("revenue") or 0), 2),
                "quantity_sold": int(row.get("quantity_sold") or 0),
            },
        )
    return out


async def _load_stoplist_counts(
    db: AsyncSession,
    org_id: int,
    start: datetime,
    end: datetime,
) -> Counter[str]:
    rows = (
        await db.execute(
            select(SystemEvent.payload_json).where(
                SystemEvent.organization_id == int(org_id),
                SystemEvent.event_type == "stoplist_update",
                SystemEvent.created_at >= _sql_dt(start),
                SystemEvent.created_at <= _sql_dt(end),
            ).limit(500),
        )
    ).scalars().all()
    counts: Counter[str] = Counter()
    for payload in rows:
        if not isinstance(payload, dict):
            continue
        for name in payload.get("items_added_to_stop") or []:
            key = _norm_name(str(name))
            if key:
                counts[key] += 1
    return counts


async def build_menu_profit_report(
    db: AsyncSession,
    organization_id: int,
    location_id: int | None = None,
    period: str = "7d",
    allowed_location_ids: set[int] | None = None,
) -> dict[str, Any]:
    """Отчёт Menu Profit Lab: выручка, маржа, стоп-лист, кандидаты на действия."""
    org_id = int(organization_id)
    start, end, label = _period_bounds(period)
    org_orders = orders_tenant_clause(org_id)
    order_scope = orders_location_filter(allowed_location_ids, location_id)

    menu_rows = (
        await db.execute(
            select(MenuItem).where(MenuItem.organization_id == org_id),
        )
    ).scalars().all()
    graph_stats = await rebuild_restaurant_graph_profiles(db, org_id, days=30 if label == "30d" else 7)
    margin_profiles = {
        row.dish_product_id: row
        for row in (
            await db.execute(
                select(DishMarginProfile).where(DishMarginProfile.organization_id == org_id),
            )
        ).scalars().all()
    }

    by_iiko: dict[str, MenuItem] = {}
    by_name: dict[str, MenuItem] = {}
    for mi in menu_rows:
        if mi.iiko_id:
            by_iiko[str(mi.iiko_id).strip().lower()] = mi
        by_name[_norm_name(mi.name)] = mi

    stats: dict[int, dict[str, Any]] = {}
    unattributed_revenue = 0.0
    unattributed_qty = 0

    def ensure_menu_item(mi: MenuItem) -> dict[str, Any]:
        if mi.id not in stats:
            price = float(mi.price or 0)
            profile = margin_profiles.get(str(mi.iiko_id or mi.id))
            cost = (
                float(profile.estimated_cost)
                if profile is not None and profile.estimated_cost is not None
                else float(mi.cost_price)
                if mi.cost_price is not None
                else None
            )
            margin_pct = None
            if profile is not None and profile.margin_pct is not None:
                margin_pct = round(float(profile.margin_pct), 1)
            elif cost is not None and price > 0:
                margin_pct = round((price - cost) / price * 100.0, 1)
            stats[mi.id] = {
                "menu_item_id": int(mi.id),
                "name": mi.name,
                "category": mi.category or "",
                "iiko_id": mi.iiko_id,
                "price": price,
                "cost_price": cost,
                "margin_pct": margin_pct,
                "margin_source": "dish_margin_profile" if profile is not None else "menu_item_cost_price",
                "margin_confidence_score": (
                    round(float(profile.confidence_score or 0), 4)
                    if profile is not None and profile.confidence_score is not None
                    else (0.6 if cost is not None else 0.0)
                ),
                "is_available": bool(mi.is_available),
                "quantity_sold": 0,
                "revenue": 0.0,
            }
        return stats[mi.id]

    order_rows = await db.execute(
        select(Order.items_json, Order.total_price).where(
            org_orders,
            order_scope,
            Order.status.in_(list(_COMPLETED)),
            Order.created_at >= _sql_dt(start),
            Order.created_at <= _sql_dt(end),
        ),
    )
    for items_json, _total in order_rows.all():
        if not isinstance(items_json, dict):
            continue
        for line in items_json.get("items") or []:
            if not isinstance(line, dict):
                continue
            qty = int(line.get("quantity") or line.get("qty") or 0)
            if qty <= 0:
                qty = 1
            rev = float(line.get("item_total") or 0)
            iiko_key = str(line.get("iiko_id") or "").strip().lower()
            name_key = _norm_name(str(line.get("name") or ""))
            mi = by_iiko.get(iiko_key) if iiko_key else None
            if mi is None and name_key:
                mi = by_name.get(name_key)
            if mi is None:
                unattributed_qty += qty
                unattributed_revenue += rev
                continue
            st = ensure_menu_item(mi)
            st["quantity_sold"] += qty
            st["revenue"] += rev

    for mi in menu_rows:
        if mi.id not in stats and not mi.is_available:
            ensure_menu_item(mi)

    stoplist_counts = await _load_stoplist_counts(db, org_id, start, end)
    cost_data_available = any(mi.cost_price is not None for mi in menu_rows) or any(
        p.estimated_cost is not None for p in margin_profiles.values()
    )

    item_list = list(stats.values())
    for st in item_list:
        name_key = _norm_name(st["name"])
        st["stoplist_incidents"] = int(stoplist_counts.get(name_key, 0))

    ranked_revenue = sorted(item_list, key=lambda x: (-float(x["revenue"]), -int(x["quantity_sold"])))
    top_revenue_items = [_item_public(x) for x in ranked_revenue[:10]]

    unknown_cost_items = [
        _item_public({**x, "reason": "no_cost_price"})
        for x in ranked_revenue
        if x.get("cost_price") is None and (int(x["quantity_sold"]) > 0 or float(x["revenue"]) > 0)
    ][:10]

    low_margin_items: list[dict[str, Any]] = []
    if cost_data_available:
        low_margin_items = [
            _item_public({**x, "reason": "low_margin"})
            for x in sorted(
                [s for s in item_list if s.get("margin_pct") is not None and int(s["quantity_sold"]) > 0],
                key=lambda x: (float(x["margin_pct"]), -float(x["revenue"])),
            )
            if float(x["margin_pct"]) < 35.0
        ][:10]

    frequent_stoplist_items = [
        _item_public({**x, "reason": "frequent_stoplist", "score": float(x["stoplist_incidents"])})
        for x in sorted(item_list, key=lambda i: (-int(i["stoplist_incidents"]), -float(i["revenue"])))
        if int(x["stoplist_incidents"]) >= 2 or (not x["is_available"] and int(x["stoplist_incidents"]) >= 1)
    ][:10]

    upsell_events = (
        await db.execute(
            select(
                UpsellOfferEvent.offered_item_name,
                UpsellOfferEvent.offered_item_id,
                UpsellOfferEvent.status,
                func.count(UpsellOfferEvent.id),
                func.coalesce(func.sum(UpsellOfferEvent.added_revenue), 0),
            ).where(
                UpsellOfferEvent.organization_id == org_id,
                UpsellOfferEvent.created_at >= _sql_dt(start),
                UpsellOfferEvent.created_at <= _sql_dt(end),
                _location_allowed_upsell(allowed_location_ids, location_id),
            ).group_by(
                UpsellOfferEvent.offered_item_name,
                UpsellOfferEvent.offered_item_id,
                UpsellOfferEvent.status,
            ),
        )
    ).all()

    upsell_stats: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"offers": 0, "accepts": 0, "revenue": 0.0, "offered_item_id": None},
    )
    for offered_name, offered_id, status, cnt, rev in upsell_events:
        key = _norm_name(str(offered_name or offered_id or "?"))
        st = upsell_stats[key]
        st["name"] = str(offered_name or offered_id or "?")
        st["offered_item_id"] = offered_id
        st["offers"] += int(cnt or 0)
        if str(status) == "accepted":
            st["accepts"] += int(cnt or 0)
            st["revenue"] += float(rev or 0)

    trace_rows = (
        await db.execute(
            select(Order.items_json).where(
                org_orders,
                order_scope,
                Order.status.in_(list(_COMPLETED)),
                Order.created_at >= _sql_dt(start),
                Order.created_at <= _sql_dt(end),
            ).limit(300),
        )
    ).scalars().all()
    engineering = menu_engineering_rows(
        [type("O", (), {"items_json": ij})() for ij in trace_rows if isinstance(ij, dict)],
    )

    upsell_candidates: list[dict[str, Any]] = []
    for key, st in sorted(upsell_stats.items(), key=lambda kv: (-kv[1]["offers"], -kv[1]["revenue"]))[:10]:
        conv = round(100.0 * st["accepts"] / st["offers"], 1) if st["offers"] else 0.0
        upsell_candidates.append(
            {
                "name": st["name"],
                "offered_item_id": st.get("offered_item_id"),
                "offers": st["offers"],
                "accepts": st["accepts"],
                "conversion_pct": conv,
                "revenue": round(float(st["revenue"]), 2),
                "reason": "high_offers_low_conversion" if st["offers"] >= 3 and conv < 25 else "top_upsell_offer",
            },
        )
    for row in engineering[:5]:
        if row["offers"] >= 2:
            upsell_candidates.append(
                {
                    "name": row["label"],
                    "offered_item_id": row["key"].removeprefix("iiko:") if row["key"].startswith("iiko:") else None,
                    "offers": row["offers"],
                    "accepts": row["accepts"],
                    "conversion_pct": row["conversion_pct"],
                    "revenue": row["revenue"],
                    "reason": "ai_recommendation_trace",
                },
            )
    upsell_candidates = upsell_candidates[:10]

    total_qty = sum(int(x["quantity_sold"]) for x in item_list) or 1
    price_increase_pool = [
        {
            **x,
            "reason": "high_demand_stable",
            "score": float(x["revenue"]) / max(float(x["price"]), 1.0),
            "recommended_increase_pct": _recommended_price_increase_pct(x),
        }
        for x in ranked_revenue
        if int(x["quantity_sold"]) >= 3
        and float(x["revenue"]) >= 5000
        and bool(x["is_available"])
        and int(x["stoplist_incidents"]) <= 1
        and (x.get("margin_pct") is None or float(x["margin_pct"]) >= 40.0)
    ][:10]
    price_increase_candidates = build_price_recommendations(price_increase_pool)

    price_recommendation_pool = [
        {
            **x,
            "reason": "price_optimization",
            "recommended_increase_pct": _recommended_price_increase_pct(x),
        }
        for x in ranked_revenue
        if int(x["quantity_sold"]) > 0 and bool(x["is_available"])
    ]
    price_recommendations = build_price_recommendations(price_recommendation_pool)[:15]

    remove_or_review_candidates = [
        _item_public(
            {
                **x,
                "reason": "low_sales_or_stoplist",
                "score": float(x["stoplist_incidents"]) * 10 - float(x["revenue"]) / 1000.0,
            },
        )
        for x in sorted(
            item_list,
            key=lambda i: (-int(i["stoplist_incidents"]), int(i["quantity_sold"]), float(i["revenue"])),
        )
        if (not x["is_available"] and int(x["stoplist_incidents"]) >= 1)
        or (int(x["quantity_sold"]) <= 1 and int(x["stoplist_incidents"]) >= 2)
        or (int(x["quantity_sold"]) == 0 and not x["is_available"])
    ][:10]

    promote_today_raw = [
        {
            **x,
            "reason": "promote_high_margin" if x.get("margin_pct") is not None else "promote_top_seller",
            "score": (
                float(x.get("margin_pct") or 0) * 0.4
                + (int(x["quantity_sold"]) / total_qty) * 100.0
                + (10.0 if x["is_available"] else -50.0)
            ),
        }
        for x in sorted(
            [s for s in item_list if s["is_available"] and int(s["quantity_sold"]) > 0],
            key=lambda i: (
                -(float(i.get("margin_pct") or 50.0)),
                -float(i["revenue"]),
            ),
        )
    ][:10]
    promote_today_candidates = [_item_public(x) for x in promote_today_raw]
    promote_today_copilot = promote_today_for_copilot(promote_today_raw)

    missing_cost_checklist = await build_missing_cost_checklist(db, org_id)

    return {
        "period": label,
        "organization_id": org_id,
        "location_id": int(location_id) if location_id is not None else None,
        "date_from": start.date().isoformat(),
        "date_to": end.date().isoformat(),
        "cost_data_available": cost_data_available,
        "lite_mode": not cost_data_available,
        "knowledge_graph": graph_stats,
        "items_analyzed": len(item_list),
        "unattributed_quantity": unattributed_qty,
        "unattributed_revenue": round(unattributed_revenue, 2),
        "top_revenue_items": top_revenue_items,
        "low_margin_items": low_margin_items,
        "unknown_cost_items": unknown_cost_items,
        "frequent_stoplist_items": frequent_stoplist_items,
        "upsell_candidates": upsell_candidates,
        "price_increase_candidates": price_increase_candidates,
        "price_recommendations": price_recommendations,
        "missing_cost_checklist": missing_cost_checklist,
        "promote_today_candidates": promote_today_candidates,
        "promote_today_copilot": promote_today_copilot,
    }


def _copilot_feed_item(
    row: dict[str, Any],
    *,
    reason: str | None = None,
    score: float | None = None,
) -> dict[str, Any]:
    return {
        "iiko_id": row.get("iiko_id"),
        "name": row.get("name"),
        "score": round(float(score if score is not None else row.get("score") or 0), 2),
        "reason": reason or row.get("reason") or "",
    }


def _dedupe_menu_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for row in rows:
        key = str(row.get("iiko_id") or row.get("name") or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


async def get_copilot_candidate_lists(
    db: AsyncSession,
    org_id: int,
    period: str = "7d",
    location_id: int | None = None,
    allowed_location_ids: set[int] | None = None,
) -> dict[str, Any]:
    """Read-only copilot feed: slim candidate lists for RC-A scoring import."""
    report = await build_menu_profit_report(
        db,
        int(org_id),
        location_id=location_id,
        period=period,
        allowed_location_ids=allowed_location_ids,
    )

    promote_today_candidates = [
        _copilot_feed_item(row) for row in report.get("promote_today_candidates") or []
    ]

    margin_pool = _dedupe_menu_rows(
        list(report.get("promote_today_candidates") or [])
        + list(report.get("top_revenue_items") or [])
        + list(report.get("price_increase_candidates") or []),
    )
    high_margin_candidates = sorted(
        [
            _copilot_feed_item(row, reason="high_margin", score=float(row.get("margin_pct") or 0))
            for row in margin_pool
            if row.get("margin_pct") is not None and float(row["margin_pct"]) >= 50.0
        ],
        key=lambda item: (-float(item["score"]), str(item.get("name") or "")),
    )[:10]

    overstock_candidates = [
        _copilot_feed_item(row, reason="overstock_stoplist")
        for row in report.get("frequent_stoplist_items") or []
    ]

    low_performing_but_profitable = sorted(
        [
            _copilot_feed_item(
                row,
                reason="low_volume_high_margin",
                score=round(
                    float(row.get("margin_pct") or 0) * max(int(row.get("quantity_sold") or 0), 1),
                    2,
                ),
            )
            for row in _dedupe_menu_rows(
                list(report.get("promote_today_candidates") or [])
                + list(report.get("top_revenue_items") or []),
            )
            if row.get("margin_pct") is not None
            and float(row["margin_pct"]) >= 40.0
            and int(row.get("quantity_sold") or 0) <= 2
            and bool(row.get("is_available", True))
        ],
        key=lambda item: (-float(item["score"]), str(item.get("name") or "")),
    )[:10]

    return {
        "promote_today_candidates": promote_today_candidates,
        "high_margin_candidates": high_margin_candidates,
        "overstock_candidates": overstock_candidates,
        "low_performing_but_profitable": low_performing_but_profitable,
    }


def _location_allowed_upsell(allowed_location_ids: set[int] | None, location_id: int | None):
    from sqlalchemy import or_

    if location_id is not None:
        lid = int(location_id)
        if allowed_location_ids is not None and lid not in allowed_location_ids:
            return UpsellOfferEvent.id == -1
        return or_(UpsellOfferEvent.location_id.is_(None), UpsellOfferEvent.location_id == lid)
    if allowed_location_ids is None:
        return True
    if not allowed_location_ids:
        return UpsellOfferEvent.id == -1
    return or_(
        UpsellOfferEvent.location_id.is_(None),
        UpsellOfferEvent.location_id.in_(list(allowed_location_ids)),
    )
