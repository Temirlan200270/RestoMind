"""
Revenue Copilot v2 — атрибуция upsell-предложений (UpsellOfferEvent).

TODO(intent_router): после успешного upsell в ``_save_recommendation_to_order_meta`` /
``apply_db_upsell_rules`` вызывать ``record_upsell_offer`` с order_id, offered_item_id,
source_rule_id и variant — см. ``app/services/intent_router.py`` (~L200, ~L920).
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Order, UpsellOfferEvent
from app.services.tenant_scope import _location_allowed_expr
from app.services.upsell_utils import (
    max_one_upsell_per_order,
    recently_offered_iiko_ids as _recently_offered_iiko_ids,
    recently_rejected_iiko_ids as _recently_rejected_iiko_ids,
    rejected_upsell_iiko_ids,
)

STATUS_SHOWN = "shown"
STATUS_ACCEPTED = "accepted"
STATUS_REJECTED = "rejected"
STATUS_IGNORED = "ignored"

logger = logging.getLogger(__name__)


def _dt_as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _sql_dt_for_filter(dt: datetime) -> datetime:
    u = _dt_as_utc(dt)
    return u


def _period_bounds(period: str) -> tuple[datetime, datetime, str]:
    now = datetime.now(timezone.utc)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    p = (period or "today").strip().lower()
    if p in {"yesterday", "вчера"}:
        start = today - timedelta(days=1)
        end = today
        label = "yesterday"
    elif p in {"week", "7d", "неделя"}:
        start = today - timedelta(days=7)
        end = now
        label = "week"
    else:
        start = today
        end = now
        label = "today"
    return start, end, label


def _upsell_location_filter(
    allowed_location_ids: set[int] | None,
    location_id: int | None = None,
):
    return _location_allowed_expr(UpsellOfferEvent, allowed_location_ids, location_id)


def _order_food_lines(items_json: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(items_json, dict):
        return []
    raw = items_json.get("items")
    if not isinstance(raw, list):
        return []
    return [x for x in raw if isinstance(x, dict)]


def _iiko_qty_map(lines: list[dict[str, Any]]) -> dict[str, float]:
    out: dict[str, float] = {}
    for line in lines:
        raw = str(line.get("iiko_id") or line.get("iiko_item_id") or "").strip().lower()
        if not raw:
            continue
        q = float(line.get("quantity") or 1)
        out[raw] = out.get(raw, 0.0) + q
    return out


def _revenue_for_iiko_qty(
    iiko_key: str,
    qty: float,
    lines: list[dict[str, Any]],
    *,
    fallback_price: float = 0.0,
) -> float:
    if qty <= 0:
        return 0.0
    for line in lines:
        rid = str(line.get("iiko_id") or line.get("iiko_item_id") or "").strip().lower()
        if rid != iiko_key:
            continue
        line_qty = float(line.get("quantity") or 1)
        total = float(line.get("item_total") or 0)
        if line_qty > 0 and total > 0:
            unit = total / line_qty
            return round(unit * qty, 2)
    if fallback_price > 0:
        return round(float(fallback_price) * qty, 2)
    return 0.0


async def record_upsell_offer(
    db: AsyncSession,
    *,
    organization_id: int,
    location_id: int | None = None,
    order_id: int | None = None,
    user_id: int | None = None,
    chat_log_id: int | None = None,
    source_rule_id: int | None = None,
    base_item_id: str | None = None,
    offered_item_id: str | None = None,
    base_item_name: str = "",
    offered_item_name: str = "",
    variant: str | None = None,
    offered_price: float = 0.0,
    meta_json: dict[str, Any] | None = None,
) -> UpsellOfferEvent:
    """Зафиксировать показ upsell-предложения (status=shown)."""
    row = UpsellOfferEvent(
        organization_id=int(organization_id),
        location_id=location_id,
        order_id=order_id,
        user_id=user_id,
        chat_log_id=chat_log_id,
        source_rule_id=source_rule_id,
        base_item_id=(base_item_id or "").strip() or None,
        offered_item_id=(offered_item_id or "").strip() or None,
        base_item_name=(base_item_name or "").strip(),
        offered_item_name=(offered_item_name or "").strip(),
        variant=(variant or "").strip() or None,
        status=STATUS_SHOWN,
        offered_price=float(offered_price or 0),
        added_revenue=0.0,
        meta_json=meta_json,
    )
    db.add(row)
    await db.flush()
    return row


async def assign_variant_at_offer(
    db: AsyncSession,
    *,
    organization_id: int,
    rule_id: int | None,
    fallback_template: str = "",
) -> tuple[str, str]:
    """
    Выбор phrase-variant в момент показа upsell (отдельно от infer acceptance).

    Возвращает (variant_key, template). При отсутствии экспериментов — fallback на rule template.
    """
    from app.services.upsell_experiments import (
        load_variants_for_rule,
        pick_weighted_variant,
        record_variant_outcome,
    )

    default_template = (fallback_template or "").strip() or (
        "К заказу отлично подойдёт {item_name} ({price} ₸). Добавить?"
    )
    if rule_id is None:
        return ("default", default_template)

    variants = await load_variants_for_rule(db, organization_id, int(rule_id))
    if not variants:
        return (f"rule_{int(rule_id)}", default_template)

    variant_key, template = pick_weighted_variant(variants)
    if not (template or "").strip():
        template = default_template

    await record_variant_outcome(
        db,
        organization_id,
        int(rule_id),
        variant_key,
        STATUS_SHOWN,
    )
    return variant_key, template


async def mark_upsell_accepted(
    db: AsyncSession,
    event_id: int,
    *,
    added_revenue: float | None = None,
) -> UpsellOfferEvent | None:
    row = await db.get(UpsellOfferEvent, int(event_id))
    if row is None:
        return None
    row.status = STATUS_ACCEPTED
    if added_revenue is not None:
        row.added_revenue = round(float(added_revenue), 2)
    elif float(row.added_revenue or 0) <= 0 and float(row.offered_price or 0) > 0:
        row.added_revenue = round(float(row.offered_price), 2)
    await db.flush()
    return row


async def mark_upsell_rejected(
    db: AsyncSession,
    event_id: int,
) -> UpsellOfferEvent | None:
    row = await db.get(UpsellOfferEvent, int(event_id))
    if row is None:
        return None
    row.status = STATUS_REJECTED
    row.added_revenue = 0.0
    await db.flush()
    return row


async def recently_rejected_iiko_ids(
    db: AsyncSession,
    org_id: int,
    user_id: int,
    *,
    days: float = 7,
) -> set[str]:
    """DB cooldown: отказы из UpsellOfferEvent (для scoring / anti-repeat)."""
    return await _recently_rejected_iiko_ids(db, org_id, user_id, days=days)


async def recently_offered_iiko_ids(
    db: AsyncSession,
    org_id: int,
    user_id: int,
    *,
    order_id: int | None = None,
    days: float = 7,
) -> set[str]:
    """DB cooldown: недавние офферы (для scoring / anti-repeat)."""
    return await _recently_offered_iiko_ids(
        db,
        org_id,
        user_id,
        order_id=order_id,
        days=days,
    )


async def order_has_upsell_offer(db: AsyncSession, order_id: int) -> bool:
    """True, если в заказе уже зафиксирован upsell-показ (max 1 offer per order)."""
    return await max_one_upsell_per_order(db, order_id)


async def infer_upsell_from_draft_update(
    db: AsyncSession,
    order: Order,
    prev_items_json: dict[str, Any] | None,
    new_items_json: dict[str, Any] | None,
) -> list[UpsellOfferEvent]:
    """
    При обновлении draft-состава помечает shown-события accepted / rejected / ignored.
    """
    order_id = int(order.id)
    res = await db.execute(
        select(UpsellOfferEvent).where(
            UpsellOfferEvent.order_id == order_id,
            UpsellOfferEvent.status == STATUS_SHOWN,
        ),
    )
    events = list(res.scalars().all())
    if not events:
        return []

    prev = prev_items_json if isinstance(prev_items_json, dict) else {}
    new = new_items_json if isinstance(new_items_json, dict) else {}
    prev_qty = _iiko_qty_map(_order_food_lines(prev))
    new_qty = _iiko_qty_map(_order_food_lines(new))

    prev_meta = prev.get("order_meta")
    prev_meta = prev_meta if isinstance(prev_meta, dict) else {}
    new_meta = new.get("order_meta")
    new_meta = new_meta if isinstance(new_meta, dict) else {}
    rejected = rejected_upsell_iiko_ids(new_meta)
    prev_rejected = rejected_upsell_iiko_ids(prev_meta)

    new_lines = _order_food_lines(new)
    updated: list[UpsellOfferEvent] = []
    for ev in events:
        oid = str(ev.offered_item_id or "").strip().lower()
        if oid and oid in rejected and oid not in prev_rejected:
            ev.status = STATUS_REJECTED
            ev.added_revenue = 0.0
            updated.append(ev)
            continue

        prev_q = prev_qty.get(oid, 0.0) if oid else 0.0
        new_q = new_qty.get(oid, 0.0) if oid else 0.0
        delta = new_q - prev_q
        if oid and delta > 0:
            ev.status = STATUS_ACCEPTED
            rev = _revenue_for_iiko_qty(
                oid,
                delta,
                new_lines,
                fallback_price=float(ev.offered_price or 0),
            )
            ev.added_revenue = rev if rev > 0 else round(float(ev.offered_price or 0) * delta, 2)
            updated.append(ev)
            continue

        if oid and oid in rejected:
            ev.status = STATUS_REJECTED
            ev.added_revenue = 0.0
            updated.append(ev)

    if updated:
        await db.flush()
    return updated


async def infer_upsell_acceptance_from_order(
    db: AsyncSession,
    order_id: int,
) -> list[UpsellOfferEvent]:
    """
    По финальному составу заказа помечает shown-события как accepted / rejected / ignored.
    """
    order = await db.get(Order, int(order_id))
    if order is None:
        return []

    res = await db.execute(
        select(UpsellOfferEvent).where(
            UpsellOfferEvent.order_id == int(order_id),
            UpsellOfferEvent.status == STATUS_SHOWN,
        ),
    )
    events = list(res.scalars().all())
    if not events:
        return []

    items_json = order.items_json if isinstance(order.items_json, dict) else {}
    meta = items_json.get("order_meta")
    meta = meta if isinstance(meta, dict) else {}
    rejected = rejected_upsell_iiko_ids(meta)
    lines = _order_food_lines(items_json)
    qty_map = _iiko_qty_map(lines)

    updated: list[UpsellOfferEvent] = []
    for ev in events:
        oid = str(ev.offered_item_id or "").strip().lower()
        if oid and oid in rejected:
            ev.status = STATUS_REJECTED
            ev.added_revenue = 0.0
            updated.append(ev)
            continue
        qty = qty_map.get(oid, 0.0) if oid else 0.0
        if oid and qty > 0:
            ev.status = STATUS_ACCEPTED
            rev = _revenue_for_iiko_qty(
                oid,
                qty,
                lines,
                fallback_price=float(ev.offered_price or 0),
            )
            ev.added_revenue = rev if rev > 0 else round(float(ev.offered_price or 0), 2)
            updated.append(ev)
            continue
        ev.status = STATUS_IGNORED
        ev.added_revenue = 0.0
        updated.append(ev)

    if updated:
        await db.flush()
    return updated


async def record_ai_upsell_from_brain_response(
    db: AsyncSession,
    *,
    organization_id: int,
    order_id: int,
    user_id: int | None = None,
    location_id: int | None = None,
    ai_eff: Any,
    menu_items: list[Any] | None = None,
) -> UpsellOfferEvent | None:
    """Записать upsell из ответа LLM (upsell_offered / upsell_offered_id), если ещё не зафиксирован."""
    from app.schemas.ai_schemas import AIBrainResponse

    if not isinstance(ai_eff, AIBrainResponse):
        return None
    off_id = (ai_eff.upsell_offered_id or "").strip()
    off_name = (ai_eff.upsell_offered or "").strip()
    if not off_id and not off_name:
        return None

    dedupe_q = select(UpsellOfferEvent).where(
        UpsellOfferEvent.organization_id == int(organization_id),
        UpsellOfferEvent.order_id == int(order_id),
        UpsellOfferEvent.status == STATUS_SHOWN,
    )
    if off_id:
        dedupe_q = dedupe_q.where(UpsellOfferEvent.offered_item_id == off_id)
    else:
        dedupe_q = dedupe_q.where(UpsellOfferEvent.offered_item_name == off_name)
    existing = await db.scalar(dedupe_q.limit(1))
    if existing is not None:
        return existing

    offered_price = 0.0
    if menu_items:
        key = off_id.lower() if off_id else off_name.lower()
        for mi in menu_items:
            iid = (getattr(mi, "iiko_id", None) or "").strip().lower()
            nm = (getattr(mi, "name", None) or "").strip().lower()
            if (off_id and iid == key) or (not off_id and nm == key):
                offered_price = float(getattr(mi, "price", 0) or 0)
                if not off_name:
                    off_name = str(getattr(mi, "name", "") or "")
                if not off_id:
                    off_id = str(getattr(mi, "iiko_id", "") or "").strip() or None
                break

    return await record_upsell_offer(
        db,
        organization_id=organization_id,
        location_id=location_id,
        order_id=order_id,
        user_id=user_id,
        offered_item_id=off_id or None,
        offered_item_name=off_name,
        variant="ai_brain",
        offered_price=offered_price,
        meta_json={
            "upsell_reasoning": (ai_eff.upsell_reasoning or "").strip() or None,
            "is_recommendation": bool(ai_eff.is_recommendation),
        },
    )


async def build_upsell_impact_summary(
    db: AsyncSession,
    org_id: int,
    period: str,
    *,
    location_id: int | None = None,
    allowed_location_ids: set[int] | None = None,
) -> dict[str, Any]:
    start, end, label = _period_bounds(period)
    start_sql = _sql_dt_for_filter(start)
    end_sql = _sql_dt_for_filter(end)
    location_scope = _upsell_location_filter(allowed_location_ids, location_id)

    rows = (
        await db.execute(
            select(UpsellOfferEvent).where(
                UpsellOfferEvent.organization_id == int(org_id),
                location_scope,
                UpsellOfferEvent.created_at >= start_sql,
                UpsellOfferEvent.created_at < end_sql,
            ),
        )
    ).scalars().all()

    shown = 0
    accepted = 0
    rejected = 0
    ignored = 0
    added_revenue = 0.0

    pair_stats: dict[tuple[str, str], dict[str, Any]] = {}
    variant_stats: dict[str, dict[str, Any]] = {}
    rejected_counter: Counter[str] = Counter()
    rejected_labels: dict[str, str] = {}
    item_stats: dict[str, dict[str, Any]] = {}

    for row in rows:
        st = (row.status or STATUS_SHOWN).strip().lower()
        if st == STATUS_SHOWN:
            shown += 1
        elif st == STATUS_ACCEPTED:
            shown += 1
            accepted += 1
            added_revenue += float(row.added_revenue or 0)
        elif st == STATUS_REJECTED:
            shown += 1
            rejected += 1
            key = (row.offered_item_id or row.offered_item_name or "?").strip()
            rejected_counter[key] += 1
            rejected_labels[key] = (row.offered_item_name or key).strip()
        elif st == STATUS_IGNORED:
            shown += 1
            ignored += 1

        off_key = (row.offered_item_id or row.offered_item_name or "?").strip()
        is_item = st in {STATUS_SHOWN, STATUS_ACCEPTED, STATUS_REJECTED, STATUS_IGNORED}
        if is_item:
            ist = item_stats.setdefault(
                off_key,
                {
                    "offered_item_id": row.offered_item_id,
                    "offered_item_name": (row.offered_item_name or off_key).strip(),
                    "shown": 0,
                    "accepted": 0,
                    "rejected": 0,
                    "ignored": 0,
                },
            )
            ist["shown"] += 1
            if st == STATUS_ACCEPTED:
                ist["accepted"] += 1
            elif st == STATUS_REJECTED:
                ist["rejected"] += 1
            elif st == STATUS_IGNORED:
                ist["ignored"] += 1

        base_name = (row.base_item_name or "").strip() or "?"
        off_name = (row.offered_item_name or "").strip() or "?"
        pair_key = (base_name, off_name)
        ps = pair_stats.setdefault(
            pair_key,
            {
                "base_item_name": base_name,
                "offered_item_name": off_name,
                "shown": 0,
                "accepted": 0,
            },
        )
        ps["shown"] += 1
        if st == STATUS_ACCEPTED:
            ps["accepted"] += 1

        variant_key = (row.variant or "default").strip() or "default"
        vs = variant_stats.setdefault(
            variant_key,
            {
                "variant": variant_key,
                "shown": 0,
                "accepted": 0,
                "added_revenue": 0.0,
            },
        )
        vs["shown"] += 1
        if st == STATUS_ACCEPTED:
            vs["accepted"] += 1
            vs["added_revenue"] += float(row.added_revenue or 0)

    conversion_rate = round(100.0 * accepted / shown, 1) if shown else 0.0

    top_pairs: list[dict[str, Any]] = []
    for ps in pair_stats.values():
        s = int(ps["shown"])
        a = int(ps["accepted"])
        top_pairs.append(
            {
                "base_item_name": ps["base_item_name"],
                "offered_item_name": ps["offered_item_name"],
                "shown": s,
                "accepted": a,
                "conversion_rate": round(100.0 * a / s, 1) if s else 0.0,
            },
        )
    top_pairs.sort(key=lambda x: (-x["accepted"], -x["shown"], x["offered_item_name"]))

    best_variants: list[dict[str, Any]] = []
    for vs in variant_stats.values():
        s = int(vs["shown"])
        a = int(vs["accepted"])
        best_variants.append(
            {
                "variant": vs["variant"],
                "shown": s,
                "accepted": a,
                "conversion_rate": round(100.0 * a / s, 1) if s else 0.0,
                "added_revenue": round(float(vs["added_revenue"]), 2),
            },
        )
    best_variants.sort(
        key=lambda x: (-x["conversion_rate"], -x["added_revenue"], -x["shown"]),
    )

    rejected_items = [
        {
            "offered_item_id": key if not key.startswith("?") else None,
            "offered_item_name": rejected_labels.get(key, key),
            "count": count,
        }
        for key, count in rejected_counter.most_common()
    ]

    worst_offers: list[dict[str, Any]] = []
    for ps in item_stats.values():
        s = int(ps["shown"])
        if s < 2:
            continue
        a = int(ps["accepted"])
        r = int(ps["rejected"])
        ig = int(ps["ignored"])
        cr = round(100.0 * a / s, 1) if s else 0.0
        negative_rate = round(100.0 * (r + ig) / s, 1) if s else 0.0
        worst_offers.append(
            {
                "offered_item_id": ps.get("offered_item_id"),
                "offered_item_name": ps.get("offered_item_name"),
                "shown": s,
                "accepted": a,
                "rejected": r,
                "ignored": ig,
                "conversion_rate": cr,
                "negative_rate": negative_rate,
            },
        )
    worst_offers.sort(
        key=lambda x: (-float(x["negative_rate"]), -int(x["rejected"]), -int(x["shown"])),
    )

    return {
        "period": label,
        "shown": shown,
        "offered": shown,
        "accepted": accepted,
        "rejected": rejected,
        "ignored": ignored,
        "conversion_rate": conversion_rate,
        "added_revenue": round(added_revenue, 2),
        "top_pairs": top_pairs[:10],
        "best_variants": best_variants[:10],
        "rejected_items": rejected_items[:10],
        "worst_offers": worst_offers[:10],
    }


async def backfill_upsell_attribution(
    db: AsyncSession,
    org_id: int,
    *,
    period: str = "today",
    location_id: int | None = None,
    allowed_location_ids: set[int] | None = None,
    limit: int = 200,
) -> dict[str, int]:
    """Пересчитать acceptance для shown upsell-событий по подтверждённым заказам (cron)."""
    from app.db.models import Order, OrderStatus
    from app.services.tenant_scope import orders_location_filter

    start, end, _ = _period_bounds(period)
    start_sql = _sql_dt_for_filter(start)
    end_sql = _sql_dt_for_filter(end)

    q = (
        select(Order.id)
        .where(
            Order.organization_id == int(org_id),
            orders_location_filter(allowed_location_ids, location_id),
            Order.status.in_(
                [
                    OrderStatus.CONFIRMED.value,
                    OrderStatus.SENT_TO_IIKO.value,
                    OrderStatus.IN_TRANSIT.value,
                    OrderStatus.WAITING_PICKUP.value,
                    OrderStatus.COMPLETED.value,
                ],
            ),
            Order.updated_at >= start_sql,
            Order.updated_at < end_sql,
        )
        .order_by(Order.updated_at.desc())
        .limit(max(1, min(int(limit), 500)))
    )
    order_ids = [int(x) for x in (await db.scalars(q)).all()]
    updated = 0
    for oid in order_ids:
        try:
            rows = await infer_upsell_acceptance_from_order(db, oid)
            if rows:
                updated += len(rows)
        except Exception as exc:
            logger.debug("backfill upsell skip order=%s: %s", oid, exc)
    return {
        "processed": len(order_ids),
        "events_updated": updated,
        "candidates": len(order_ids),
    }
