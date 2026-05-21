"""Unified money-at-risk queue for operator inbox (Money Core)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import ChatLog, Order, OrderStatus, User
from app.services.bot_sla_status import chat_live_pulse
from app.services.db_schema_fallback import with_location_scope_fallback
from app.services.tenant_scope import chat_logs_location_filter, orders_location_filter, orders_tenant_clause

DRAFT_STALE_MINUTES = 30
PULSE_QUEUE_MIN_SEC = 120
HIGH_VALUE_STUCK_KZT = 5000


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _sql_dt(dt: datetime) -> datetime:
    u = _utc(dt)
    return u.replace(tzinfo=None) if settings.db_mode == "sqlite" else u


def _severity_rank(severity: str) -> int:
    return {"critical": 0, "warning": 1, "info": 2}.get(str(severity or "").lower(), 3)


def _sort_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        items,
        key=lambda it: (
            _severity_rank(str(it.get("severity") or "")),
            -(float(it.get("amount_kzt") or 0)),
            -(int(it.get("wait_minutes") or 0)),
        ),
    )


async def _fetch_abandoned_drafts(
    db: AsyncSession,
    org_id: int,
    *,
    location_id: int | None,
    allowed_location_ids: set[int] | None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    cutoff = _sql_dt(datetime.now(tz=timezone.utc) - timedelta(minutes=DRAFT_STALE_MINUTES))
    rows = (
        await db.execute(
            select(Order, User.phone, User.name)
            .join(User, User.id == Order.user_id)
            .where(
                orders_tenant_clause(org_id),
                Order.status == OrderStatus.DRAFT.value,
                Order.updated_at <= cutoff,
                Order.total_price > 0,
                orders_location_filter(allowed_location_ids, location_id),
            )
            .order_by(Order.total_price.desc(), Order.updated_at.asc())
            .limit(limit)
        )
    ).all()

    items: list[dict[str, Any]] = []
    for order, phone, user_name in rows:
        items_json = order.items_json if isinstance(order.items_json, dict) else {}
        if not items_json.get("items"):
            continue
        updated = _utc(order.updated_at or order.created_at or datetime.now(tz=timezone.utc))
        wait_min = max(0, int((datetime.now(tz=timezone.utc) - updated).total_seconds() // 60))
        amount = float(order.total_price or 0)
        items.append(
            {
                "id": f"draft:{order.id}",
                "kind": "abandoned_draft",
                "severity": "critical" if wait_min >= 45 else "warning",
                "title": f"Брошенный черновик #{order.id}",
                "subtitle": (user_name or phone or "Гость") + f" · ждёт {wait_min} мин",
                "amount_kzt": round(amount, 2),
                "phone": phone,
                "order_id": int(order.id),
                "wait_minutes": wait_min,
                "created_at": updated.isoformat(),
                "actions": [
                    {"label": "Открыть заказ", "tab": "orders", "order_id": int(order.id)},
                    {"label": "Написать гостю", "tab": "chats", "phone": phone},
                ],
            }
        )
    return items


async def _fetch_pending_prepay(
    db: AsyncSession,
    org_id: int,
    *,
    location_id: int | None,
    allowed_location_ids: set[int] | None,
    limit: int = 8,
) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            select(Order, User.phone, User.name)
            .join(User, User.id == Order.user_id)
            .where(
                orders_tenant_clause(org_id),
                Order.prepayment_status == "pending",
                Order.status.in_([OrderStatus.DRAFT.value, OrderStatus.CONFIRMED.value]),
                orders_location_filter(allowed_location_ids, location_id),
            )
            .order_by(Order.updated_at.asc())
            .limit(limit)
        )
    ).all()

    items: list[dict[str, Any]] = []
    for order, phone, user_name in rows:
        updated = _utc(order.updated_at or order.created_at or datetime.now(tz=timezone.utc))
        wait_min = max(0, int((datetime.now(tz=timezone.utc) - updated).total_seconds() // 60))
        amount = float(order.total_price or 0)
        items.append(
            {
                "id": f"prepay:{order.id}",
                "kind": "pending_prepay",
                "severity": "critical" if wait_min >= 60 else "warning",
                "title": f"Ожидает оплату #{order.id}",
                "subtitle": (user_name or phone or "Гость") + f" · {wait_min} мин без оплаты",
                "amount_kzt": round(amount, 2),
                "phone": phone,
                "order_id": int(order.id),
                "wait_minutes": wait_min,
                "created_at": updated.isoformat(),
                "actions": [
                    {"label": "Открыть заказ", "tab": "orders", "order_id": int(order.id)},
                    {"label": "Написать гостю", "tab": "chats", "phone": phone},
                ],
            }
        )
    return items


async def _fetch_slow_chats(
    db: AsyncSession,
    org_id: int,
    *,
    location_id: int | None,
    allowed_location_ids: set[int] | None,
    limit: int = 8,
) -> list[dict[str, Any]]:
    now = datetime.now(tz=timezone.utc)
    base_sq = (
        select(
            ChatLog.id.label("log_id"),
            ChatLog.created_at.label("last_at"),
            ChatLog.content.label("content"),
            ChatLog.role.label("last_role"),
            User.phone.label("phone"),
            User.name.label("user_name"),
            func.row_number()
            .over(
                partition_by=ChatLog.user_id,
                order_by=(ChatLog.created_at.desc(), ChatLog.id.desc()),
            )
            .label("rn"),
        )
        .join(User, User.id == ChatLog.user_id)
        .where(
            User.organization_id == org_id,
            chat_logs_location_filter(allowed_location_ids, location_id),
        )
        .subquery()
    )
    rows = (
        await db.execute(
            select(base_sq).where(base_sq.c.rn == 1).order_by(base_sq.c.last_at.asc()).limit(limit * 3)
        )
    ).all()

    items: list[dict[str, Any]] = []
    for r in rows:
        phone = r.phone
        if not phone:
            continue
        live = chat_live_pulse(r.last_role, r.last_at, now=now)
        wait_sec = live.get("wait_seconds")
        if wait_sec is None or int(wait_sec) < PULSE_QUEUE_MIN_SEC:
            continue
        pulse = str(live.get("pulse") or "green")
        if pulse not in {"amber", "red"}:
            continue
        wait_min = max(1, int(wait_sec) // 60)
        items.append(
            {
                "id": f"chat:{phone}",
                "kind": "slow_chat",
                "severity": "critical" if pulse == "red" else "warning",
                "title": f"Гость ждёт ответ · {r.user_name or phone}",
                "subtitle": ((r.content or "")[:72] + "…") if len(r.content or "") > 72 else (r.content or "Без текста"),
                "amount_kzt": 0.0,
                "phone": phone,
                "order_id": None,
                "wait_minutes": wait_min,
                "pulse": pulse,
                "created_at": _utc(r.last_at).isoformat() if r.last_at else None,
                "actions": [
                    {"label": "Открыть диалог", "tab": "chats", "phone": phone},
                ],
            }
        )
        if len(items) >= limit:
            break
    return items


async def _fetch_high_value_stuck(
    db: AsyncSession,
    org_id: int,
    *,
    location_id: int | None,
    allowed_location_ids: set[int] | None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    cutoff = _sql_dt(datetime.now(tz=timezone.utc) - timedelta(minutes=DRAFT_STALE_MINUTES))
    rows = (
        await db.execute(
            select(Order, User.phone, User.name)
            .join(User, User.id == Order.user_id)
            .where(
                orders_tenant_clause(org_id),
                Order.total_price >= HIGH_VALUE_STUCK_KZT,
                Order.status.in_([OrderStatus.DRAFT.value, OrderStatus.CONFIRMED.value]),
                Order.updated_at <= cutoff,
                orders_location_filter(allowed_location_ids, location_id),
            )
            .order_by(Order.total_price.desc(), Order.updated_at.asc())
            .limit(limit)
        )
    ).all()

    items: list[dict[str, Any]] = []
    seen_orders: set[int] = set()
    for order, phone, user_name in rows:
        oid = int(order.id)
        if oid in seen_orders:
            continue
        seen_orders.add(oid)
        updated = _utc(order.updated_at or order.created_at or datetime.now(tz=timezone.utc))
        wait_min = max(0, int((datetime.now(tz=timezone.utc) - updated).total_seconds() // 60))
        amount = float(order.total_price or 0)
        items.append(
            {
                "id": f"high:{order.id}",
                "kind": "high_value_stuck",
                "severity": "critical",
                "title": f"Крупный заказ #{order.id}",
                "subtitle": (user_name or phone or "Гость") + f" · ₸{amount:,.0f} · {wait_min} мин".replace(",", " "),
                "amount_kzt": round(amount, 2),
                "phone": phone,
                "order_id": oid,
                "wait_minutes": wait_min,
                "created_at": updated.isoformat(),
                "actions": [
                    {"label": "В приоритет", "tab": "inbox", "inboxTab": "clients"},
                    {"label": "Открыть заказ", "tab": "orders", "order_id": oid},
                ],
            }
        )
    return items


async def summarize_queue_counts(
    db: AsyncSession,
    org_id: int,
    *,
    location_id: int | None = None,
    allowed_location_ids: set[int] | None = None,
) -> dict[str, int | float]:
    """Агрегаты для G8 action layer (без лимита списка G7)."""
    draft_cutoff = _sql_dt(datetime.now(tz=timezone.utc) - timedelta(minutes=DRAFT_STALE_MINUTES))
    stuck_cutoff = draft_cutoff

    draft_row = (
        await db.execute(
            select(
                func.count(Order.id),
                func.coalesce(func.sum(Order.total_price), 0),
            ).where(
                orders_tenant_clause(org_id),
                Order.status == OrderStatus.DRAFT.value,
                Order.updated_at <= draft_cutoff,
                Order.total_price > 0,
                orders_location_filter(allowed_location_ids, location_id),
            )
        )
    ).one()
    draft_count, draft_sum = int(draft_row[0] or 0), float(draft_row[1] or 0)

    prepay_row = (
        await db.execute(
            select(
                func.count(Order.id),
                func.coalesce(func.sum(Order.total_price), 0),
            ).where(
                orders_tenant_clause(org_id),
                Order.prepayment_status == "pending",
                Order.status.in_([OrderStatus.DRAFT.value, OrderStatus.CONFIRMED.value]),
                orders_location_filter(allowed_location_ids, location_id),
            )
        )
    ).one()
    prepay_count, prepay_sum = int(prepay_row[0] or 0), float(prepay_row[1] or 0)

    high_row = (
        await db.execute(
            select(
                func.count(Order.id),
                func.coalesce(func.sum(Order.total_price), 0),
            ).where(
                orders_tenant_clause(org_id),
                Order.total_price >= HIGH_VALUE_STUCK_KZT,
                Order.status.in_([OrderStatus.DRAFT.value, OrderStatus.CONFIRMED.value]),
                Order.updated_at <= stuck_cutoff,
                orders_location_filter(allowed_location_ids, location_id),
            )
        )
    ).one()
    high_count, high_sum = int(high_row[0] or 0), float(high_row[1] or 0)

    slow_count = len(
        await _fetch_slow_chats(
            db,
            org_id,
            location_id=location_id,
            allowed_location_ids=allowed_location_ids,
            limit=100,
        )
    )

    return {
        "abandoned_drafts": draft_count,
        "abandoned_drafts_kzt": round(draft_sum, 2),
        "pending_prepay": prepay_count,
        "pending_prepay_kzt": round(prepay_sum, 2),
        "slow_chats": slow_count,
        "high_value_stuck": high_count,
        "high_value_stuck_kzt": round(high_sum, 2),
    }


async def _build_money_queue_impl(
    db: AsyncSession,
    org_id: int,
    *,
    location_id: int | None = None,
    allowed_location_ids: set[int] | None = None,
) -> dict[str, Any]:
    drafts, prepay, chats, high_value = await _fetch_abandoned_drafts(
        db,
        org_id,
        location_id=location_id,
        allowed_location_ids=allowed_location_ids,
    ), await _fetch_pending_prepay(
        db,
        org_id,
        location_id=location_id,
        allowed_location_ids=allowed_location_ids,
    ), await _fetch_slow_chats(
        db,
        org_id,
        location_id=location_id,
        allowed_location_ids=allowed_location_ids,
    ), await _fetch_high_value_stuck(
        db,
        org_id,
        location_id=location_id,
        allowed_location_ids=allowed_location_ids,
    )

    draft_ids = {int(it.get("order_id") or 0) for it in drafts}
    prepay_ids = {int(it.get("order_id") or 0) for it in prepay}
    high_value = [
        it for it in high_value
        if int(it.get("order_id") or 0) not in draft_ids and int(it.get("order_id") or 0) not in prepay_ids
    ]

    items = _sort_items([*drafts, *prepay, *chats, *high_value])
    money_kzt = round(
        sum(float(it.get("amount_kzt") or 0) for it in items if float(it.get("amount_kzt") or 0) > 0),
        2,
    )
    return {
        "items": items,
        "summary": {
            "total": len(items),
            "abandoned_drafts": len(drafts),
            "pending_prepay": len(prepay),
            "slow_chats": len(chats),
            "high_value_stuck": len(high_value),
            "money_at_risk_kzt": money_kzt,
            "critical": sum(1 for it in items if it.get("severity") == "critical"),
        },
        "location_id": location_id,
    }


async def build_money_queue(
    db: AsyncSession,
    org_id: int,
    *,
    location_id: int | None = None,
    allowed_location_ids: set[int] | None = None,
) -> dict[str, Any]:
    return await with_location_scope_fallback(
        location_id=location_id,
        allowed_location_ids=allowed_location_ids,
        run=lambda loc_id, allowed: _build_money_queue_impl(
            db,
            org_id,
            location_id=loc_id,
            allowed_location_ids=allowed,
        ),
    )
