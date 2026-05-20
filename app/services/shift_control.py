"""Shift Control Screen — единый операционный контур G5–G8 для смены."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import Order, OrderStatus
from app.services.integration_config import iiko_effective_configured, whatsapp_effective_configured
from app.services.integration_health import build_status_payload
from app.services.money_queue import build_money_queue
from app.services.revenue_leak import build_revenue_leak
from app.services.tenant_scope import orders_location_filter, orders_tenant_clause

SHIFT_QUEUE_LIMIT = 8
FOCUS_LIMIT = 3
LIVE_CHAT_LIMIT = 6

_COMPLETED_TODAY = (
    OrderStatus.CONFIRMED.value,
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


def _normalize_action(action: dict[str, Any]) -> dict[str, Any]:
    out = dict(action)
    if "tab" in out and "type" not in out:
        out["type"] = "navigate"
    return out


def _focus_action(item: dict[str, Any]) -> dict[str, Any] | None:
    actions = item.get("actions") or []
    if not actions:
        return None
    primary = _normalize_action(dict(actions[0]))
    if item.get("kind") == "abandoned_draft" and len(actions) > 1:
        for act in actions:
            a = _normalize_action(dict(act))
            if a.get("tab") == "orders":
                return a
    return primary


async def _saved_today_kzt(
    db: AsyncSession,
    org_id: int,
    *,
    location_id: int | None,
    allowed_location_ids: set[int] | None,
) -> float:
    today_start = _sql_dt(datetime.now(tz=timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0))
    total = float(
        await db.scalar(
            select(func.coalesce(func.sum(Order.total_price), 0)).where(
                orders_tenant_clause(org_id),
                Order.status.in_(list(_COMPLETED_TODAY)),
                Order.created_at >= today_start,
                Order.total_price > 0,
                orders_location_filter(allowed_location_ids, location_id),
            )
        )
        or 0
    )
    return round(total, 2)


def _avg_wait_sec(items: list[dict[str, Any]]) -> int | None:
    waits = [int(it.get("wait_minutes") or 0) for it in items if it.get("kind") == "slow_chat"]
    if not waits:
        return None
    return int(sum(waits) / len(waits) * 60)


async def _system_status(db: AsyncSession, org_id: int) -> list[dict[str, str]]:
    iiko_ok = await iiko_effective_configured(db, org_id)
    wa_ok = await whatsapp_effective_configured(db, org_id)
    payload = await build_status_payload(
        db,
        organization_id=int(org_id),
        iiko_configured=iiko_ok,
        whatsapp_configured=wa_ok,
    )
    rows: list[dict[str, str]] = [
        {
            "id": "whatsapp",
            "label": "WhatsApp",
            "status": "ok" if wa_ok else "down",
        },
        {
            "id": "iiko",
            "label": "iiko",
            "status": "ok" if iiko_ok else "down",
        },
    ]
    menu_sync = payload.get("last_menu_sync") or {}
    if iiko_ok:
        if menu_sync.get("ok") is False:
            rows.append({"id": "iiko_sync", "label": "Синхронизация iiko", "status": "warning"})
        elif menu_sync.get("at"):
            rows.append({"id": "iiko_sync", "label": "Синхронизация iiko", "status": "ok"})
    rows.append({"id": "payments", "label": "Оплаты", "status": "ok"})
    return rows


async def build_shift_control(
    db: AsyncSession,
    org_id: int,
    *,
    location_id: int | None = None,
    allowed_location_ids: set[int] | None = None,
) -> dict[str, Any]:
    money = await build_money_queue(
        db,
        org_id,
        location_id=location_id,
        allowed_location_ids=allowed_location_ids,
    )
    leak = await build_revenue_leak(
        db,
        org_id,
        location_id=location_id,
        allowed_location_ids=allowed_location_ids,
    )

    queue = list(money.get("items") or [])[:SHIFT_QUEUE_LIMIT]
    focus: list[dict[str, Any]] = []
    for item in queue[:FOCUS_LIMIT]:
        act = _focus_action(item)
        focus.append(
            {
                **item,
                "do_now_action": act,
                "do_now_label": "Сделать сейчас",
            }
        )

    live_chats = [it for it in queue if it.get("kind") == "slow_chat"][:LIVE_CHAT_LIMIT]
    drafts = [it for it in queue if it.get("kind") == "abandoned_draft"]
    prepay = [it for it in queue if it.get("kind") == "pending_prepay"]
    high_value = [it for it in queue if it.get("kind") == "high_value_stuck"]

    saved_today = await _saved_today_kzt(
        db,
        org_id,
        location_id=location_id,
        allowed_location_ids=allowed_location_ids,
    )
    avg_wait = _avg_wait_sec(queue)

    quick_actions = [
        {
            "id": "recover_all",
            "label": "Вернуть все черновики",
            "type": "api",
            "method": "POST",
            "path": "/api/admin/intelligence/revenue-leak/recover-drafts",
        },
        {
            "id": "open_red_chats",
            "label": "Ответить красным",
            "type": "navigate",
            "tab": "chats",
            "chatPulseFilter": "red",
        },
        {
            "id": "open_money_queue",
            "label": "Очередь денег",
            "type": "navigate",
            "tab": "inbox",
            "inboxTab": "clients",
        },
    ]

    return {
        "location_id": location_id,
        "metrics": {
            "at_risk_kzt": float(money.get("summary", {}).get("money_at_risk_kzt") or 0),
            "saved_today_kzt": saved_today,
            "avg_wait_sec": avg_wait,
            "total_leak_kzt": float(leak.get("total_leak_kzt") or 0),
            "queue_total": int(money.get("summary", {}).get("total") or 0),
            "critical": int(money.get("summary", {}).get("critical") or 0),
        },
        "queue": queue,
        "focus": focus,
        "live_chats": live_chats,
        "orders": {
            "drafts": drafts,
            "pending_prepay": prepay,
            "high_value": high_value,
        },
        "leak": {
            "breakdown": leak.get("breakdown") or {},
            "surfaces": leak.get("surfaces") or [],
            "labels": leak.get("labels") or {},
        },
        "quick_actions": quick_actions,
        "system": await _system_status(db, org_id),
    }
