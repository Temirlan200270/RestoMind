"""
Operator efficiency analytics (P4 sprint).

Вычисляет метрики эффективности операторов из существующих таблиц:
- EscalationEvent — количество и частота эскалаций
- ChatLog — время первого ответа оператора после эскалации
- Order — сколько заказов было подтверждено после работы оператора
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ChatLog, EscalationEvent, Order, OrderStatus, User

logger = logging.getLogger(__name__)


async def get_operator_efficiency(
    db: AsyncSession,
    org_id: int,
    hours: int = 24,
) -> dict[str, Any]:
    """
    Возвращает агрегированные метрики оператора за последние N часов.

    Метрики:
    - escalation_count: сколько раз клиенты вызвали оператора
    - total_dialogs: всего уникальных клиентов-диалогов за период
    - escalation_rate_pct: (escalation_count / total_dialogs) * 100
    - avg_first_response_min: среднее время от эскалации до первого ответа
      оператора (ChatLog role=assistant) — оценка отзывчивости
    - human_mode_sessions: уникальных телефонов в HUMAN_MODE
    - orders_confirmed_after_escalation: заказов CONFIRMED среди phone, у которых была эскалация
    - operator_recovery_rate_pct: (confirmed / escalation_count) * 100
    """
    since = datetime.now(timezone.utc) - timedelta(hours=hours)

    # Количество эскалаций
    escalation_count = await db.scalar(
        select(func.count(EscalationEvent.id)).where(
            EscalationEvent.organization_id == org_id,
            EscalationEvent.created_at >= since,
        )
    ) or 0

    # Уникальные телефоны с эскалацией
    escalated_phones_rows = await db.execute(
        select(EscalationEvent.phone)
        .where(
            EscalationEvent.organization_id == org_id,
            EscalationEvent.created_at >= since,
        )
        .distinct()
    )
    escalated_phones = {r[0] for r in escalated_phones_rows.all() if r[0]}

    # Всего уникальных клиентов (через ChatLog), для расчёта rate
    total_dialogs = await db.scalar(
        select(func.count(func.distinct(ChatLog.user_id))).where(
            ChatLog.organization_id == org_id,
            ChatLog.created_at >= since,
        )
    ) or 0

    escalation_rate_pct = (
        round(escalation_count / total_dialogs * 100, 1) if total_dialogs > 0 else 0.0
    )

    # Среднее время первого ответа после эскалации
    avg_first_response_min: float | None = None
    if escalated_phones:
        response_deltas: list[float] = []
        for phone in list(escalated_phones)[:50]:  # ограничение для производительности
            # Найти последнюю эскалацию этого телефона за период
            esc = await db.scalar(
                select(EscalationEvent).where(
                    EscalationEvent.organization_id == org_id,
                    EscalationEvent.phone == phone,
                    EscalationEvent.created_at >= since,
                ).order_by(EscalationEvent.created_at.desc()).limit(1)
            )
            if esc is None:
                continue
            # Найти user_id по phone + org
            user_id = await db.scalar(
                select(User.id).where(
                    User.phone == phone,
                    User.organization_id == org_id,
                ).limit(1)
            )
            if user_id is None:
                continue
            # Первый ответ бота/оператора ПОСЛЕ эскалации
            first_reply = await db.scalar(
                select(ChatLog.created_at).where(
                    ChatLog.user_id == user_id,
                    ChatLog.organization_id == org_id,
                    ChatLog.role == "assistant",
                    ChatLog.created_at > esc.created_at,
                ).order_by(ChatLog.created_at.asc()).limit(1)
            )
            if first_reply:
                delta = (first_reply - esc.created_at).total_seconds() / 60.0
                if 0 < delta < 1440:  # не считаем > 24h
                    response_deltas.append(delta)

        if response_deltas:
            avg_first_response_min = round(sum(response_deltas) / len(response_deltas), 1)

    # Заказов CONFIRMED среди эскалированных клиентов
    orders_confirmed = 0
    if escalated_phones:
        user_ids_rows = await db.execute(
            select(User.id).where(
                User.phone.in_(list(escalated_phones)),
                User.organization_id == org_id,
            )
        )
        user_ids = [r[0] for r in user_ids_rows.all()]
        if user_ids:
            orders_confirmed = await db.scalar(
                select(func.count(Order.id)).where(
                    Order.organization_id == org_id,
                    Order.user_id.in_(user_ids),
                    Order.status.in_([OrderStatus.CONFIRMED.value, OrderStatus.SENT_TO_IIKO.value]),
                    Order.created_at >= since,
                )
            ) or 0

    operator_recovery_rate_pct = (
        round(orders_confirmed / escalation_count * 100, 1) if escalation_count > 0 else 0.0
    )

    return {
        "period_hours": hours,
        "escalation_count": int(escalation_count),
        "total_dialogs": int(total_dialogs),
        "escalation_rate_pct": escalation_rate_pct,
        "avg_first_response_min": avg_first_response_min,
        "human_mode_sessions": len(escalated_phones),
        "orders_confirmed_after_escalation": int(orders_confirmed),
        "operator_recovery_rate_pct": operator_recovery_rate_pct,
    }
