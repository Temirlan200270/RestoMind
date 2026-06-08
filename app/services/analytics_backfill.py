"""Backfill исторических данных в daily_org_stats (Phase 5 OS).

Читает Order/EscalationEvent/ChatLog и заполняет daily_org_stats за последние N дней.
Использует GREATEST(existing, backfill) — живые event-driven данные не перетираются.
Запускается однократно через POST /api/admin/intelligence/backfill-stats.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import ChatLog, EscalationEvent, Order, OrderStatus, SystemEvent
from app.services.tenant_scope import orders_tenant_clause

logger = logging.getLogger(__name__)


def _sql_dt(dt: datetime) -> datetime:
    u = dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return u


async def backfill_daily_org_stats(
    db: AsyncSession,
    org_id: int,
    *,
    days: int = 90,
) -> dict[str, Any]:
    """Заполняет daily_org_stats историческими данными из Order/EscalationEvent/ChatLog.

    Использует GREATEST(existing, backfill) — живые счётчики не перетираются.
    Возвращает сводку: сколько дней обновлено и базовые счётчики.
    """
    now_utc = datetime.now(tz=timezone.utc)
    floor = now_utc.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days - 1)
    floor_sql = _sql_dt(floor)
    now_sql = _sql_dt(now_utc)

    org_orders = orders_tenant_clause(org_id)
    not_cancelled = Order.status != OrderStatus.CANCELLED.value

    # ── Заказы: created / confirmed / cancelled / revenue_kzt ─────────────
    order_rows = (await db.execute(
        select(
            Order.created_at,
            Order.status,
            Order.total_price,
        ).where(
            org_orders,
            Order.created_at.isnot(None),
            Order.created_at >= floor_sql,
            Order.created_at <= now_sql,
        )
    )).all()

    day_buckets: dict[str, dict] = {}

    for created_at, status, total_price in order_rows:
        dt = created_at
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dk = dt.astimezone(timezone.utc).strftime("%Y-%m-%d")
        if dk not in day_buckets:
            day_buckets[dk] = {
                "orders_created": 0,
                "orders_confirmed": 0,
                "orders_cancelled": 0,
                "revenue_kzt": 0.0,
                "escalations": 0,
                "ai_messages_count": 0,
                "dialogs_count": 0,
                "pricing_adjustments": 0,
                "sla_violations": 0,
                "healing_wa_sent": 0,
                "draft_recovery_sent": 0,
                "whatsapp_delivery_failed": 0,
            }
        day_buckets[dk]["orders_created"] += 1
        s = str(status or "").lower()
        if s in ("confirmed", "sent_to_iiko", "in_transit", "waiting_pickup", "completed"):
            day_buckets[dk]["orders_confirmed"] += 1
            day_buckets[dk]["revenue_kzt"] += float(total_price or 0)
        elif s == "cancelled":
            day_buckets[dk]["orders_cancelled"] += 1

    # ── Эскалации ────────────────────────────────────────────────────────
    esc_rows = (await db.execute(
        select(EscalationEvent.created_at).where(
            EscalationEvent.organization_id == org_id,
            EscalationEvent.created_at.isnot(None),
            EscalationEvent.created_at >= floor_sql,
            EscalationEvent.created_at <= now_sql,
        )
    )).scalars().all()

    for created_at in esc_rows:
        dt = created_at
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dk = dt.astimezone(timezone.utc).strftime("%Y-%m-%d")
        if dk not in day_buckets:
            day_buckets[dk] = {
                "orders_created": 0, "orders_confirmed": 0, "orders_cancelled": 0,
                "revenue_kzt": 0.0, "escalations": 0, "ai_messages_count": 0,
                "dialogs_count": 0, "pricing_adjustments": 0, "sla_violations": 0,
                "healing_wa_sent": 0, "draft_recovery_sent": 0, "whatsapp_delivery_failed": 0,
            }
        day_buckets[dk]["escalations"] += 1

    # ── AI-сообщения (ассистент) ─────────────────────────────────────────
    chat_rows = (await db.execute(
        select(ChatLog.created_at).where(
            ChatLog.organization_id == org_id,
            ChatLog.role == "assistant",
            ChatLog.created_at.isnot(None),
            ChatLog.created_at >= floor_sql,
            ChatLog.created_at <= now_sql,
        )
    )).scalars().all()

    for created_at in chat_rows:
        dt = created_at
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dk = dt.astimezone(timezone.utc).strftime("%Y-%m-%d")
        if dk not in day_buckets:
            day_buckets[dk] = {
                "orders_created": 0, "orders_confirmed": 0, "orders_cancelled": 0,
                "revenue_kzt": 0.0, "escalations": 0, "ai_messages_count": 0,
                "dialogs_count": 0, "pricing_adjustments": 0, "sla_violations": 0,
                "healing_wa_sent": 0, "draft_recovery_sent": 0, "whatsapp_delivery_failed": 0,
            }
        day_buckets[dk]["ai_messages_count"] += 1

    # ── Уникальные диалоги (user messages per day) ─────────────────────────
    dialog_rows = (await db.execute(
        select(
            func.date(ChatLog.created_at).label("day"),
            func.count(func.distinct(ChatLog.user_id)).label("cnt"),
        ).where(
            ChatLog.organization_id == org_id,
            ChatLog.role == "user",
            ChatLog.created_at.isnot(None),
            ChatLog.created_at >= floor_sql,
            ChatLog.created_at <= now_sql,
        ).group_by(func.date(ChatLog.created_at))
    )).all()

    for day_val, cnt in dialog_rows:
        dk = str(day_val) if day_val is not None else None
        if not dk:
            continue
        if dk not in day_buckets:
            day_buckets[dk] = {
                "orders_created": 0, "orders_confirmed": 0, "orders_cancelled": 0,
                "revenue_kzt": 0.0, "escalations": 0, "ai_messages_count": 0,
                "dialogs_count": 0, "pricing_adjustments": 0, "sla_violations": 0,
                "healing_wa_sent": 0, "draft_recovery_sent": 0, "whatsapp_delivery_failed": 0,
            }
        day_buckets[dk]["dialogs_count"] = int(cnt or 0)

    _OPS_EVENT_COLUMNS = {
        "system.pricing_adjusted": "pricing_adjustments",
        "system.sla_violated": "sla_violations",
        "system.healing_wa_sent": "healing_wa_sent",
        "order.draft_recovery_sent": "draft_recovery_sent",
        "integration.whatsapp.failed": "whatsapp_delivery_failed",
    }
    ops_rows = (await db.execute(
        select(SystemEvent.event_type, SystemEvent.created_at).where(
            SystemEvent.organization_id == org_id,
            SystemEvent.event_type.in_(tuple(_OPS_EVENT_COLUMNS.keys())),
            SystemEvent.created_at.isnot(None),
            SystemEvent.created_at >= floor_sql,
            SystemEvent.created_at <= now_sql,
        )
    )).all()
    for event_type, created_at in ops_rows:
        dt = created_at
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dk = dt.astimezone(timezone.utc).strftime("%Y-%m-%d")
        col = _OPS_EVENT_COLUMNS.get(str(event_type))
        if not col:
            continue
        if dk not in day_buckets:
            day_buckets[dk] = {
                "orders_created": 0, "orders_confirmed": 0, "orders_cancelled": 0,
                "revenue_kzt": 0.0, "escalations": 0, "ai_messages_count": 0,
                "dialogs_count": 0, "pricing_adjustments": 0, "sla_violations": 0,
                "healing_wa_sent": 0, "draft_recovery_sent": 0, "whatsapp_delivery_failed": 0,
            }
        day_buckets[dk][col] = int(day_buckets[dk].get(col, 0)) + 1

    if not day_buckets:
        return {"ok": True, "org_id": org_id, "days_updated": 0, "days_skipped": 0}

    # ── Upsert в daily_org_stats ──────────────────────────────────────────
    # GREATEST(existing, backfill) — живые event-driven данные не перетираются
    updated = 0
    for dk, bucket in day_buckets.items():
        sql = text("""
            INSERT INTO daily_org_stats
                (organization_id, day,
                 orders_created, orders_confirmed, orders_cancelled, revenue_kzt,
                 escalations, ai_messages_count, dialogs_count,
                 pricing_adjustments, sla_violations, healing_wa_sent,
                 draft_recovery_sent, whatsapp_delivery_failed)
            VALUES
                (:org_id, :day,
                 :orders_created, :orders_confirmed, :orders_cancelled, :revenue_kzt,
                 :escalations, :ai_messages_count, :dialogs_count,
                 :pricing_adjustments, :sla_violations, :healing_wa_sent,
                 :draft_recovery_sent, :whatsapp_delivery_failed)
            ON CONFLICT (organization_id, day)
            DO UPDATE SET
                orders_created    = GREATEST(daily_org_stats.orders_created,    EXCLUDED.orders_created),
                orders_confirmed  = GREATEST(daily_org_stats.orders_confirmed,  EXCLUDED.orders_confirmed),
                orders_cancelled  = GREATEST(daily_org_stats.orders_cancelled,  EXCLUDED.orders_cancelled),
                revenue_kzt       = GREATEST(daily_org_stats.revenue_kzt,       EXCLUDED.revenue_kzt),
                escalations       = GREATEST(daily_org_stats.escalations,       EXCLUDED.escalations),
                ai_messages_count = GREATEST(daily_org_stats.ai_messages_count, EXCLUDED.ai_messages_count),
                dialogs_count     = GREATEST(daily_org_stats.dialogs_count,     EXCLUDED.dialogs_count),
                pricing_adjustments = GREATEST(daily_org_stats.pricing_adjustments, EXCLUDED.pricing_adjustments),
                sla_violations    = GREATEST(daily_org_stats.sla_violations,    EXCLUDED.sla_violations),
                healing_wa_sent   = GREATEST(daily_org_stats.healing_wa_sent,   EXCLUDED.healing_wa_sent),
                draft_recovery_sent = GREATEST(daily_org_stats.draft_recovery_sent, EXCLUDED.draft_recovery_sent),
                whatsapp_delivery_failed = GREATEST(daily_org_stats.whatsapp_delivery_failed, EXCLUDED.whatsapp_delivery_failed),
                updated_at        = CURRENT_TIMESTAMP
        """)
        await db.execute(sql, {
            "org_id": org_id,
            "day": dk,
            **{k: bucket.get(k, 0) for k in (
                "orders_created", "orders_confirmed", "orders_cancelled",
                "revenue_kzt", "escalations", "ai_messages_count", "dialogs_count",
                "pricing_adjustments", "sla_violations", "healing_wa_sent",
                "draft_recovery_sent", "whatsapp_delivery_failed",
            )},
        })
        updated += 1

    logger.info(
        "analytics_backfill: org=%d updated %d days (last %d days)",
        org_id, updated, days,
    )
    return {
        "ok": True,
        "org_id": org_id,
        "days_updated": updated,
        "days_skipped": days - updated,
        "total_orders_confirmed": sum(b["orders_confirmed"] for b in day_buckets.values()),
        "total_revenue_kzt": round(sum(b["revenue_kzt"] for b in day_buckets.values()), 2),
    }
