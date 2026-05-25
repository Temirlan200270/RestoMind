"""Analytics consumer for the Event-First OS layer (Phase 2.3).

Подключается к emit_event() как синхронный consumer: получает BusinessEvent и обновляет
агрегаты в DailyOrgStats через upsert (ON CONFLICT DO UPDATE).

Запускается ВНУТРИ транзакции emit_event — если commit откатится, агрегат тоже не сохранится.
Ошибки consumer логируются, но не пробрасываются — event записывается в любом случае.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.services.system_events import BusinessEvent

logger = logging.getLogger(__name__)


def _zero_event_summary() -> dict:
    return {
        "orders_created": 0,
        "orders_confirmed": 0,
        "orders_cancelled": 0,
        "bookings_created": 0,
        "bookings_confirmed": 0,
        "payments_completed": 0,
        "payments_failed": 0,
        "revenue_kzt": 0.0,
        "escalations": 0,
        "operator_takeovers": 0,
        "ai_messages_count": 0,
        "dialogs_count": 0,
        "recovered_kzt": 0.0,
        "focus_completed_count": 0,
        "pricing_adjustments": 0,
        "sla_violations": 0,
        "healing_wa_sent": 0,
        "draft_recovery_sent": 0,
        "whatsapp_delivery_failed": 0,
        "source": "event_driven",
    }


async def _safe_daily_stats_mappings(db: "AsyncSession", sql, params: dict):
    """Read DailyOrgStats; return None on schema/DB errors (e.g. migration lag on prod)."""
    try:
        result = await db.execute(sql, params)
        return result.mappings()
    except SQLAlchemyError as exc:
        logger.warning("daily_org_stats read failed org=%s: %s", params.get("org_id"), exc)
        # Postgres: failed statement aborts the whole transaction — must rollback before fallback SQL.
        try:
            await db.rollback()
        except SQLAlchemyError:
            logger.exception("rollback after daily_org_stats read failure failed")
        return None


HANDLED_EVENT_TYPES = frozenset({
    "order.created",
    "order.confirmed",
    "order.cancelled",
    "booking.created",
    "booking.confirmed",
    "booking.cancelled",
    "payment.completed",
    "payment.failed",
    "payment.expired",
    "ai.escalated",
    "ai.response.generated",
    "ai.dialog.started",
    "operator.took_over",
    "shift.focus_completed",
    "order.draft_recovered",
    "system.pricing_adjusted",
    "system.sla_violated",
    "system.healing_wa_sent",
    "order.draft_recovery_sent",
    "integration.whatsapp.failed",
})

_EVENT_COLUMN: dict[str, str] = {
    "order.created": "orders_created",
    "order.confirmed": "orders_confirmed",
    "order.cancelled": "orders_cancelled",
    "booking.created": "bookings_created",
    "booking.confirmed": "bookings_confirmed",
    "booking.cancelled": "bookings_cancelled",
    "payment.completed": "payments_completed",  # handled by _upsert_payment_completed (also updates revenue_kzt)
    "payment.failed": "payments_failed",
    "payment.expired": "payments_failed",  # expired → same counter as failed for analytics
    "ai.escalated": "escalations",
    "ai.response.generated": "ai_messages_count",
    "ai.dialog.started": "dialogs_count",
    "operator.took_over": "operator_takeovers",
    "system.pricing_adjusted": "pricing_adjustments",
    "system.sla_violated": "sla_violations",
    "system.healing_wa_sent": "healing_wa_sent",
    "order.draft_recovery_sent": "draft_recovery_sent",
    "integration.whatsapp.failed": "whatsapp_delivery_failed",
}


async def on_business_event(event: "BusinessEvent", db: "AsyncSession") -> None:
    """Consumer для BusinessEvent: инкрементирует дневной агрегат в DailyOrgStats.

    Вызывается синхронно внутри транзакции emit_event(). Использует raw SQL upsert
    (INSERT ... ON CONFLICT DO UPDATE) — работает на PostgreSQL и SQLite 3.24+.
    """
    if event.type not in HANDLED_EVENT_TYPES:
        return

    today = datetime.now(tz=timezone.utc).date()

    logger.debug(
        "analytics_consumer: org=%d type=%s day=%s",
        event.org_id, event.type, today,
    )

    if event.type == "shift.focus_completed":
        amount = float(event.payload.get("amount_kzt") or 0)
        await _upsert_recovered(db, event.org_id, today, amount, increment_focus_count=True)
        return

    if event.type == "order.draft_recovered":
        amount = float(event.payload.get("amount_kzt") or event.payload.get("total_price") or 0)
        await _upsert_recovered(db, event.org_id, today, amount, increment_focus_count=False)
        return

    column = _EVENT_COLUMN.get(event.type)
    if column is not None:
        if event.type == "payment.completed":
            amount = float(event.payload.get("amount") or 0)
            await _upsert_payment_completed(db, event.org_id, today, amount)
        else:
            await _upsert_daily_stat(db, event.org_id, today, column)


async def _upsert_payment_completed(
    db: "AsyncSession",
    org_id: int,
    day: object,
    amount: float,
) -> None:
    """Единый upsert: payments_completed +1 и revenue_kzt += amount атомарно."""
    sql = text("""
        INSERT INTO daily_org_stats
            (organization_id, day, payments_completed, revenue_kzt)
        VALUES
            (:org_id, :day, 1, :amount)
        ON CONFLICT (organization_id, day)
        DO UPDATE SET
            payments_completed = daily_org_stats.payments_completed + 1,
            revenue_kzt = daily_org_stats.revenue_kzt + :amount,
            updated_at = CURRENT_TIMESTAMP
    """)
    await db.execute(sql, {"org_id": org_id, "day": day, "amount": amount})


async def _upsert_recovered(
    db: "AsyncSession",
    org_id: int,
    day: object,
    amount_kzt: float,
    *,
    increment_focus_count: bool,
) -> None:
    """Money Layer: increment recovered_kzt (+ optional focus_completed_count)."""
    amount = round(max(0.0, float(amount_kzt or 0)), 2)
    fc_inc = 1 if increment_focus_count else 0
    if amount <= 0 and fc_inc <= 0:
        return
    sql = text("""
        INSERT INTO daily_org_stats
            (organization_id, day, recovered_kzt, focus_completed_count)
        VALUES
            (:org_id, :day, :amount, :fc_inc)
        ON CONFLICT (organization_id, day)
        DO UPDATE SET
            recovered_kzt = daily_org_stats.recovered_kzt + :amount,
            focus_completed_count = daily_org_stats.focus_completed_count + :fc_inc,
            updated_at = CURRENT_TIMESTAMP
    """)
    try:
        await db.execute(sql, {"org_id": org_id, "day": day, "amount": amount, "fc_inc": fc_inc})
    except SQLAlchemyError as exc:
        logger.warning("daily_org_stats recovered upsert failed org=%s: %s", org_id, exc)


async def _upsert_daily_stat(
    db: "AsyncSession",
    org_id: int,
    day: object,
    column: str,
) -> None:
    """Атомарный инкремент одной метрики в daily_org_stats через SQL upsert."""
    # column — строка из _EVENT_COLUMN (не из пользовательского ввода), безопасно
    sql = text(f"""
        INSERT INTO daily_org_stats
            (organization_id, day, {column})
        VALUES
            (:org_id, :day, 1)
        ON CONFLICT (organization_id, day)
        DO UPDATE SET
            {column} = daily_org_stats.{column} + 1,
            updated_at = CURRENT_TIMESTAMP
    """)
    await db.execute(sql, {"org_id": org_id, "day": day})


async def get_event_stats(
    db: "AsyncSession",
    org_id: int,
    days: int = 7,
) -> list[dict]:
    """Читает агрегаты из DailyOrgStats за последние N дней для org_id."""
    sql = text("""
        SELECT
            day, orders_created, orders_confirmed, orders_cancelled,
            bookings_created, bookings_confirmed, bookings_cancelled,
            payments_completed, payments_failed, revenue_kzt,
            escalations, operator_takeovers,
            ai_messages_count, dialogs_count,
            recovered_kzt, focus_completed_count,
            pricing_adjustments, sla_violations, healing_wa_sent,
            draft_recovery_sent, whatsapp_delivery_failed,
            updated_at
        FROM daily_org_stats
        WHERE organization_id = :org_id
          AND day >= (CURRENT_DATE - CAST(:days AS INTEGER))
        ORDER BY day DESC
    """)
    mappings = await _safe_daily_stats_mappings(db, sql, {"org_id": org_id, "days": days - 1})
    if mappings is None:
        return []
    rows = mappings.all()
    return [
        {
            "date": str(r["day"]),
            "orders_created": int(r["orders_created"] or 0),
            "orders_confirmed": int(r["orders_confirmed"] or 0),
            "orders_cancelled": int(r["orders_cancelled"] or 0),
            "bookings_created": int(r["bookings_created"] or 0),
            "bookings_confirmed": int(r["bookings_confirmed"] or 0),
            "bookings_cancelled": int(r["bookings_cancelled"] or 0),
            "payments_completed": int(r["payments_completed"] or 0),
            "payments_failed": int(r["payments_failed"] or 0),
            "revenue_kzt": float(r["revenue_kzt"] or 0),
            "escalations": int(r["escalations"] or 0),
            "operator_takeovers": int(r["operator_takeovers"] or 0),
            "ai_messages_count": int(r["ai_messages_count"] or 0),
            "dialogs_count": int(r["dialogs_count"] or 0),
            "pricing_adjustments": int(r.get("pricing_adjustments") or 0),
            "sla_violations": int(r.get("sla_violations") or 0),
            "healing_wa_sent": int(r.get("healing_wa_sent") or 0),
            "draft_recovery_sent": int(r.get("draft_recovery_sent") or 0),
            "whatsapp_delivery_failed": int(r.get("whatsapp_delivery_failed") or 0),
        }
        for r in rows
    ]


def _sum_event_rows(rows: list[dict]) -> dict:
    """Sum daily event rows into period totals."""
    totals = _zero_event_summary()
    for r in rows:
        for key in (
            "orders_created", "orders_confirmed", "orders_cancelled",
            "bookings_created", "bookings_confirmed", "bookings_cancelled",
            "payments_completed", "payments_failed", "escalations",
            "operator_takeovers", "ai_messages_count", "dialogs_count",
            "focus_completed_count", "pricing_adjustments", "sla_violations",
            "healing_wa_sent", "draft_recovery_sent", "whatsapp_delivery_failed",
        ):
            totals[key] = int(totals.get(key, 0)) + int(r.get(key) or 0)
        totals["revenue_kzt"] = float(totals.get("revenue_kzt", 0)) + float(r.get("revenue_kzt") or 0)
        totals["recovered_kzt"] = float(totals.get("recovered_kzt", 0)) + float(r.get("recovered_kzt") or 0)
    totals["source"] = "event_driven"
    return totals


async def get_cumulative_event_totals(db: "AsyncSession", org_id: int) -> dict | None:
    """All-time totals from DailyOrgStats SUM; None if table unreadable or empty."""
    sql = text("""
        SELECT
            COALESCE(SUM(orders_created), 0) AS orders_created,
            COALESCE(SUM(orders_cancelled), 0) AS orders_cancelled,
            COALESCE(SUM(orders_confirmed), 0) AS orders_confirmed,
            COALESCE(SUM(revenue_kzt), 0) AS revenue_kzt,
            COUNT(*) AS row_count
        FROM daily_org_stats
        WHERE organization_id = :org_id
    """)
    mappings = await _safe_daily_stats_mappings(db, sql, {"org_id": org_id})
    if mappings is None:
        return None
    row = mappings.first()
    if row is None or int(row["row_count"] or 0) <= 0:
        return None
    created = int(row["orders_created"] or 0)
    cancelled = int(row["orders_cancelled"] or 0)
    confirmed = int(row["orders_confirmed"] or 0)
    revenue = float(row["revenue_kzt"] or 0)
    if created <= 0 and confirmed <= 0 and revenue <= 0:
        return None
    return {
        "orders_created": created,
        "orders_cancelled": cancelled,
        "orders_confirmed": confirmed,
        "revenue_kzt": revenue,
        "total_orders": max(0, created - cancelled),
        "has_data": True,
    }


async def get_event_stats_for_range(
    db: "AsyncSession",
    org_id: int,
    *,
    start_date: "date",
    end_date: "date",
) -> list[dict]:
    """Читает агрегаты из DailyOrgStats за произвольный диапазон дат (start..end включительно)."""
    from datetime import date as _date
    sql = text("""
        SELECT
            day, orders_created, orders_confirmed, orders_cancelled,
            payments_completed, payments_failed, revenue_kzt,
            escalations, operator_takeovers, ai_messages_count, dialogs_count
        FROM daily_org_stats
        WHERE organization_id = :org_id
          AND day >= :start_date
          AND day <= :end_date
        ORDER BY day ASC
    """)
    mappings = await _safe_daily_stats_mappings(
        db,
        sql,
        {
            "org_id": org_id,
            "start_date": start_date,
            "end_date": end_date,
        },
    )
    if mappings is None:
        return []
    rows = mappings.all()
    return [
        {
            "date": str(r["day"]),
            "orders_created": int(r["orders_created"] or 0),
            "orders_confirmed": int(r["orders_confirmed"] or 0),
            "orders_cancelled": int(r["orders_cancelled"] or 0),
            "revenue_kzt": float(r["revenue_kzt"] or 0),
            "escalations": int(r["escalations"] or 0),
            "ai_messages_count": int(r["ai_messages_count"] or 0),
            "dialogs_count": int(r["dialogs_count"] or 0),
        }
        for r in rows
    ]


async def get_today_event_summary(
    db: "AsyncSession",
    org_id: int,
) -> dict:
    """Сводка событий за сегодня (UTC) для обогащения /api/admin/stats."""
    sql = text("""
        SELECT
            orders_created, orders_confirmed, orders_cancelled,
            bookings_created, bookings_confirmed,
            payments_completed, payments_failed, revenue_kzt,
            escalations, operator_takeovers,
            ai_messages_count, dialogs_count
        FROM daily_org_stats
        WHERE organization_id = :org_id
          AND day = CURRENT_DATE
    """)
    mappings = await _safe_daily_stats_mappings(db, sql, {"org_id": org_id})
    if mappings is None:
        return _zero_event_summary()
    row = mappings.first()
    zero = _zero_event_summary()
    if row is None:
        return zero
    return {
        "orders_created": int(row["orders_created"] or 0),
        "orders_confirmed": int(row["orders_confirmed"] or 0),
        "orders_cancelled": int(row["orders_cancelled"] or 0),
        "bookings_created": int(row["bookings_created"] or 0),
        "bookings_confirmed": int(row["bookings_confirmed"] or 0),
        "payments_completed": int(row["payments_completed"] or 0),
        "payments_failed": int(row["payments_failed"] or 0),
        "revenue_kzt": float(row["revenue_kzt"] or 0),
        "escalations": int(row["escalations"] or 0),
        "operator_takeovers": int(row["operator_takeovers"] or 0),
        "ai_messages_count": int(row["ai_messages_count"] or 0),
        "dialogs_count": int(row["dialogs_count"] or 0),
        "source": "event_driven",
    }
