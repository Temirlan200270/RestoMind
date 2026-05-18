"""Analytics consumer for the Event-First OS layer (Phase 2).

Подключается к emit_event() как синхронный consumer:
получает BusinessEvent и обновляет агрегаты вместо прямых SQL-запросов в /stats.

Текущая реализация — заготовка (stub), которая логирует события.
Полноценные агрегаты добавятся по мере замены прямых SQL в analytics.py на event-driven.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.services.system_events import BusinessEvent

logger = logging.getLogger(__name__)

# Типы событий, которые обрабатывает этот consumer
HANDLED_EVENT_TYPES = frozenset({
    "order.created",
    "order.confirmed",
    "order.cancelled",
    "ai.escalated",
    "operator.took_over",
    "booking.confirmed",
})


async def on_business_event(event: "BusinessEvent", db: "AsyncSession") -> None:
    """Consumer для BusinessEvent: вызывается из emit_event() после записи в БД.

    Пока является stub-логгером. Следующий шаг (Phase 2.3):
    заменить прямые COUNT-запросы в /api/admin/stats на чтение из агрегатных таблиц,
    которые этот consumer обновляет инкрементально по событию.
    """
    if event.type not in HANDLED_EVENT_TYPES:
        return

    logger.debug(
        "analytics_consumer: org=%d type=%s actor=%s entity=%s/%s",
        event.org_id,
        event.type,
        event.actor,
        event.entity_type or "-",
        event.entity_id or "-",
    )

    # Заглушки для будущих агрегатов:
    if event.type == "order.confirmed":
        # TODO Phase 2.3: _increment_daily_revenue(db, event.org_id, event.payload.get("total", 0))
        pass
    elif event.type == "ai.escalated":
        # TODO Phase 2.3: _increment_escalation_count(db, event.org_id)
        pass
    elif event.type == "operator.took_over":
        # TODO Phase 2.3: _increment_takeover_count(db, event.org_id)
        pass
