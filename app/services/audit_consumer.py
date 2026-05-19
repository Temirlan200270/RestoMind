"""Audit consumer — иммутабельный лог всех бизнес-событий (Phase 5 OS).

Вызывается из emit_event() после analytics_consumer.
Пишет запись в audit_log: кто (actor), что (action/type), над чем (entity), когда (created_at).
Таблица append-only — без UPDATE/DELETE.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.services.system_events import BusinessEvent

logger = logging.getLogger(__name__)

# Высокочастотные технические события — логируем только summary, не каждую запись
_HIGH_FREQ_TYPES = frozenset({
    "ai.response.generated",
    "conversation.state_changed",
})


async def on_business_event(event: "BusinessEvent", db: "AsyncSession") -> None:
    """Записывает ВСЕ бизнес-события в audit_log (append-only).

    Высокочастотные технические события (_HIGH_FREQ_TYPES) пропускаются
    чтобы не раздувать таблицу без бизнес-пользы.
    Вызывается синхронно внутри транзакции emit_event().
    """
    if event.type in _HIGH_FREQ_TYPES:
        return  # слишком часто — не логируем в audit

    try:
        from app.db.models import AuditLog
        entry = AuditLog(
            organization_id=int(event.org_id),
            actor=str(event.actor),
            action=str(event.type),
            entity_type=event.entity_type,
            entity_id=str(event.entity_id) if event.entity_id is not None else None,
            details={
                k: v for k, v in event.payload.items()
                if not k.startswith("_")  # убираем внутренние поля _actor, _version, _location_id
            } or None,
        )
        db.add(entry)
        await db.flush()
        logger.debug("audit_consumer: logged %s for org=%d", event.type, event.org_id)
        _schedule_os_audit_ws(event, entry)
    except Exception:
        logger.exception("audit_consumer failed for event type=%s org=%d", event.type, event.org_id)


def _schedule_os_audit_ws(event: "BusinessEvent", entry: object) -> None:
    """Real-time OS Decision Feed: push в admin WebSocket без polling."""
    try:
        import asyncio
        from app.services.events import publish_event

        created = getattr(entry, "created_at", None)
        asyncio.create_task(
            publish_event(
                "os.audit",
                {
                    "organization_id": int(event.org_id),
                    "org_id": int(event.org_id),
                    "actor": str(event.actor),
                    "action": str(event.type),
                    "entity_type": event.entity_type,
                    "entity_id": str(event.entity_id) if event.entity_id is not None else None,
                    "created_at": created.isoformat() if created is not None else None,
                    "title": _audit_feed_title(event),
                },
            )
        )
    except Exception:
        logger.debug("os.audit ws push skipped for %s", event.type)


def _audit_feed_title(event: "BusinessEvent") -> str:
    mapping = {
        "order.created": "ОС: создан черновик заказа",
        "order.confirmed": "ОС: заказ подтверждён",
        "order.cancelled": "ОС: заказ отменён",
        "booking.created": "ОС: бронь создана",
        "booking.confirmed": "ОС: бронь подтверждена",
        "booking.cancelled": "ОС: бронь отменена",
        "payment.completed": "ОС: оплата получена",
        "payment.failed": "ОС: ошибка оплаты",
        "payment.expired": "ОС: оплата истекла",
        "ai.escalated": "ОС: эскалация к оператору",
        "ai.dialog.started": "ОС: новый диалог с гостем",
        "operator.took_over": "ОС: оператор подключился",
        "system.pricing_adjusted": "ОС: цены скорректированы",
        "system.healing_wa_sent": "ОС: напоминание об оплате в WhatsApp",
        "system.sla_violated": "ОС: нарушен SLA",
        "integration.whatsapp.failed": "ОС: сбой доставки WhatsApp",
        "integration.iiko.failed": "ОС: ошибка iiko",
    }
    if event.type in mapping:
        return mapping[event.type]
    human = (event.type or "").replace(".", " · ").replace("_", " ")
    return f"ОС: {human}"


async def get_audit_log(
    db: "AsyncSession",
    org_id: int,
    *,
    limit: int = 50,
    action_filter: str | None = None,
    actor_filter: str | None = None,
) -> list[dict]:
    """Читает последние N записей audit_log для org_id.

    Объединяет AuditLog (бизнес-события через emit_event) и
    SystemEvent (системные события, включая legacy emit_system_event).
    """
    from sqlalchemy import select, desc, union_all, literal, text as _text
    from app.db.models import AuditLog, SystemEvent

    # AuditLog — события через emit_event (основной источник)
    audit_stmt = (
        select(
            AuditLog.id.label("id"),
            AuditLog.actor.label("actor"),
            AuditLog.action.label("action"),
            AuditLog.entity_type.label("entity_type"),
            AuditLog.entity_id.label("entity_id"),
            AuditLog.created_at.label("created_at"),
            literal("audit_log").label("source"),
        )
        .where(AuditLog.organization_id == org_id)
        .order_by(desc(AuditLog.created_at))
        .limit(limit)
    )
    if action_filter:
        audit_stmt = audit_stmt.where(AuditLog.action == action_filter)
    if actor_filter:
        audit_stmt = audit_stmt.where(AuditLog.actor == actor_filter)

    rows = (await db.execute(audit_stmt)).all()

    # Дополняем SystemEvent для полного покрытия (системные события без emit_event)
    if not action_filter and not actor_filter:
        sys_stmt = (
            select(
                SystemEvent.id.label("id"),
                SystemEvent.source.label("actor"),
                SystemEvent.event_type.label("action"),
                SystemEvent.entity_type.label("entity_type"),
                SystemEvent.entity_id.label("entity_id"),
                SystemEvent.created_at.label("created_at"),
                literal("system_events").label("source"),
            )
            .where(
                SystemEvent.organization_id == org_id,
                SystemEvent.event_type.not_in(
                    # Уже покрыто AuditLog — не дублировать
                    [
                        "order.created", "order.confirmed", "order.cancelled",
                        "booking.created", "booking.confirmed", "booking.cancelled",
                        "payment.completed", "payment.failed", "payment.expired",
                        "ai.escalated", "operator.took_over",
                        "system.sla_violated", "conversation.state_changed",
                    ]
                ),
            )
            .order_by(desc(SystemEvent.created_at))
            .limit(limit // 2)
        )
        sys_rows = (await db.execute(sys_stmt)).all()
        rows = list(rows) + list(sys_rows)

    rows_sorted = sorted(rows, key=lambda r: (r.created_at or ""), reverse=True)[:limit]

    return [
        {
            "id": r.id,
            "actor": r.actor or "system",
            "action": r.action,
            "entity_type": r.entity_type,
            "entity_id": r.entity_id,
            "source": r.source,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows_sorted
    ]
