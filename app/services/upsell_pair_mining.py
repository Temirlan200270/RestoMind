"""Revenue Copilot v3 — mining upsell pair scores from confirmed order co-occurrence."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import MenuItem, Order, OrderStatus, UpsellOfferEvent
from app.services.tenant_scope import orders_location_filter

_CONFIRMED = (
    OrderStatus.CONFIRMED.value,
    OrderStatus.SENDING_TO_IIKO.value,
    OrderStatus.SENT_TO_IIKO.value,
    OrderStatus.IN_TRANSIT.value,
    OrderStatus.WAITING_PICKUP.value,
    OrderStatus.COMPLETED.value,
)

_REJECT_STATUSES = ("rejected", "ignored")


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
    p = (period or "30d").strip().lower()
    if p in {"7d", "week", "неделя"}:
        return today - timedelta(days=7), now, "7d"
    if p in {"30d", "month", "месяц"}:
        return today - timedelta(days=30), now, "30d"
    return today - timedelta(days=30), now, "30d"


def _line_iiko_id(line: dict[str, Any]) -> str:
    return str(line.get("iiko_id") or line.get("iiko_item_id") or "").strip().lower()


def _order_iiko_ids(items_json: dict[str, Any] | None) -> list[str]:
    if not isinstance(items_json, dict):
        return []
    raw = items_json.get("items")
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for row in raw:
        if not isinstance(row, dict):
            continue
        iid = _line_iiko_id(row)
        if not iid or iid in seen:
            continue
        seen.add(iid)
        out.append(iid)
    return out


def _pair_scores_from_cooccurrence(
    co_counts: dict[tuple[str, str], int],
    base_counts: Counter[str],
) -> dict[str, dict[str, float]]:
    scores: dict[str, dict[str, float]] = defaultdict(dict)
    for (base, offered), count in co_counts.items():
        if base == offered:
            continue
        base_total = int(base_counts.get(base) or 0)
        if base_total <= 0 or count <= 0:
            continue
        score = round(min(100.0, (count / base_total) * 100.0), 2)
        if score > 0:
            scores[base][offered] = score
    return {base: dict(offered_map) for base, offered_map in scores.items()}


async def build_upsell_pair_scores(
    db: AsyncSession,
    org_id: int,
    period: str = "30d",
    location_id: int | None = None,
    *,
    allowed_location_ids: set[int] | None = None,
) -> dict[str, dict[str, float]]:
    """base_iiko -> {offered_iiko: conditional co-occurrence score 0..100}."""
    start, end, _ = _period_bounds(period)
    start_sql = _sql_dt(start)
    end_sql = _sql_dt(end)

    q = select(Order.items_json).where(
        Order.organization_id == int(org_id),
        orders_location_filter(allowed_location_ids, location_id),
        Order.status.in_(_CONFIRMED),
        Order.updated_at >= start_sql,
        Order.updated_at < end_sql,
    )
    rows = (await db.scalars(q)).all()

    co_counts: dict[tuple[str, str], int] = defaultdict(int)
    base_counts: Counter[str] = Counter()

    for items_json in rows:
        ids = _order_iiko_ids(items_json if isinstance(items_json, dict) else None)
        if len(ids) < 2:
            for iid in ids:
                base_counts[iid] += 1
            continue
        for base in ids:
            base_counts[base] += 1
            for offered in ids:
                if base == offered:
                    continue
                co_counts[(base, offered)] += 1

    return _pair_scores_from_cooccurrence(co_counts, base_counts)


async def get_best_pairs_for_item(
    db: AsyncSession,
    org_id: int,
    item_iiko_id: str,
    limit: int = 5,
    *,
    period: str = "30d",
    location_id: int | None = None,
    allowed_location_ids: set[int] | None = None,
) -> list[dict[str, Any]]:
    """Top co-occurring items for a base iiko_id with menu labels."""
    base = (item_iiko_id or "").strip().lower()
    if not base:
        return []

    scores = await build_upsell_pair_scores(
        db,
        int(org_id),
        period=period,
        location_id=location_id,
        allowed_location_ids=allowed_location_ids,
    )
    offered_map = scores.get(base) or {}
    if not offered_map:
        return []

    ranked = sorted(offered_map.items(), key=lambda kv: (-kv[1], kv[0]))[: max(1, int(limit))]
    offered_ids = [oid for oid, _ in ranked]
    name_by_iiko: dict[str, str] = {}
    if offered_ids:
        menu_rows = (
            await db.scalars(
                select(MenuItem).where(
                    MenuItem.organization_id == int(org_id),
                    MenuItem.iiko_id.in_(offered_ids),
                ),
            )
        ).all()
        for row in menu_rows:
            iid = (row.iiko_id or "").strip().lower()
            if iid:
                name_by_iiko[iid] = (row.name or "").strip()

    base_name = ""
    base_row = await db.scalar(
        select(MenuItem.name).where(
            MenuItem.organization_id == int(org_id),
            MenuItem.iiko_id == base,
        ).limit(1),
    )
    if base_row:
        base_name = str(base_row).strip()

    return [
        {
            "base_iiko_id": base,
            "base_item_name": base_name or base,
            "offered_iiko_id": offered_id,
            "offered_item_name": name_by_iiko.get(offered_id, offered_id),
            "score": float(score),
            "period": period,
        }
        for offered_id, score in ranked
    ]


async def build_offer_rejection_penalties(
    db: AsyncSession,
    org_id: int,
    period: str = "30d",
    location_id: int | None = None,
    *,
    allowed_location_ids: set[int] | None = None,
) -> dict[str, float]:
    """Negative score penalty per offered_item_id from reject/ignored UpsellOfferEvent frequency."""
    from app.services.menu_profit_lab import _location_allowed_upsell

    start, end, _ = _period_bounds(period)
    start_sql = _sql_dt(start)
    end_sql = _sql_dt(end)
    location_scope = _location_allowed_upsell(allowed_location_ids, location_id)

    rows = (
        await db.execute(
            select(UpsellOfferEvent.offered_item_id, UpsellOfferEvent.status).where(
                UpsellOfferEvent.organization_id == int(org_id),
                location_scope,
                UpsellOfferEvent.created_at >= start_sql,
                UpsellOfferEvent.created_at < end_sql,
                UpsellOfferEvent.status.in_(_REJECT_STATUSES),
            ),
        )
    ).all()

    counts: Counter[str] = Counter()
    for offered_id, _status in rows:
        iid = str(offered_id or "").strip().lower()
        if iid:
            counts[iid] += 1

    return {iid: round(-min(80.0, float(count) * 8.0), 2) for iid, count in counts.items()}


async def flatten_top_mined_pairs(
    db: AsyncSession,
    org_id: int,
    *,
    period: str = "30d",
    location_id: int | None = None,
    allowed_location_ids: set[int] | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Global top mined pairs with menu names for admin preview."""
    scores = await build_upsell_pair_scores(
        db,
        int(org_id),
        period=period,
        location_id=location_id,
        allowed_location_ids=allowed_location_ids,
    )
    flat: list[tuple[str, str, float]] = []
    for base, offered_map in scores.items():
        for offered, score in offered_map.items():
            flat.append((base, offered, float(score)))
    flat.sort(key=lambda row: (-row[2], row[0], row[1]))

    iiko_ids = {base for base, _, _ in flat[: limit * 2]}
    iiko_ids.update({offered for _, offered, _ in flat[: limit * 2]})
    name_by_iiko: dict[str, str] = {}
    if iiko_ids:
        menu_rows = (
            await db.scalars(
                select(MenuItem).where(
                    MenuItem.organization_id == int(org_id),
                    MenuItem.iiko_id.in_(list(iiko_ids)),
                ),
            )
        ).all()
        for row in menu_rows:
            iid = (row.iiko_id or "").strip().lower()
            if iid:
                name_by_iiko[iid] = (row.name or "").strip()

    out: list[dict[str, Any]] = []
    for base, offered, score in flat[: max(1, int(limit))]:
        out.append(
            {
                "base_iiko_id": base,
                "base_item_name": name_by_iiko.get(base, base),
                "offered_iiko_id": offered,
                "offered_item_name": name_by_iiko.get(offered, offered),
                "score": round(score, 2),
                "period": period,
            },
        )
    return out
