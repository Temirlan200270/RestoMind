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

    # Среднее время первого ответа после эскалации — один SQL-запрос вместо N+1 цикла
    avg_first_response_min: float | None = None
    if escalated_phones:
        from sqlalchemy import text
        rows = (await db.execute(
            text("""
                WITH last_esc AS (
                    SELECT phone, MAX(created_at) AS last_esc_at
                    FROM escalation_events
                    WHERE organization_id = :org_id AND created_at >= :since
                    GROUP BY phone
                )
                SELECT
                    le.last_esc_at,
                    MIN(cl.created_at) AS first_reply_at
                FROM last_esc le
                JOIN users u ON u.phone = le.phone AND u.organization_id = :org_id
                JOIN chat_logs cl
                    ON cl.user_id = u.id
                    AND cl.organization_id = :org_id
                    AND cl.role = 'assistant'
                    AND cl.created_at > le.last_esc_at
                GROUP BY le.phone, le.last_esc_at
            """),
            {"org_id": org_id, "since": since},
        )).all()

        response_deltas: list[float] = []
        for esc_at, reply_at in rows:
            if esc_at is None or reply_at is None:
                continue
            if not hasattr(esc_at, "tzinfo"):
                from datetime import timezone as _tz
                esc_at = esc_at.replace(tzinfo=_tz.utc)
                reply_at = reply_at.replace(tzinfo=_tz.utc)
            delta = (reply_at - esc_at).total_seconds() / 60.0
            if 0 < delta < 1440:
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
