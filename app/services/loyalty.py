"""
Бонусная программа лояльности.

LoyaltyBalance — текущий баланс клиента (upsert).
LoyaltyTransaction — история начислений/списаний.
get_loyalty_context_line — вставляется в system prompt если LOYALTY_ENABLED.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def get_balance(db: AsyncSession, org_id: int, user_id: int) -> int:
    """Возвращает текущий баланс баллов клиента (0 если нет записи)."""
    from app.db.models import LoyaltyBalance
    row = await db.scalar(
        select(LoyaltyBalance).where(
            LoyaltyBalance.organization_id == org_id,
            LoyaltyBalance.user_id == user_id,
        )
    )
    return int(row.balance_points) if row else 0


async def add_points(
    db: AsyncSession,
    org_id: int,
    user_id: int,
    points: int,
    *,
    kind: str,
    order_id: int | None = None,
    note: str | None = None,
) -> int:
    """
    Начисляет или списывает баллы (points > 0 = earn, points < 0 = redeem).
    Возвращает новый баланс.
    """
    from app.db.models import LoyaltyBalance, LoyaltyTransaction

    row = await db.scalar(
        select(LoyaltyBalance).where(
            LoyaltyBalance.organization_id == org_id,
            LoyaltyBalance.user_id == user_id,
        )
    )
    if row is None:
        row = LoyaltyBalance(
            organization_id=org_id,
            user_id=user_id,
            balance_points=0,
        )
        db.add(row)
        await db.flush()

    row.balance_points = int(row.balance_points or 0) + points
    if row.balance_points < 0:
        row.balance_points = 0

    tx = LoyaltyTransaction(
        organization_id=org_id,
        user_id=user_id,
        order_id=order_id,
        points=points,
        kind=kind,
        note=note,
    )
    db.add(tx)
    await db.flush()
    return int(row.balance_points)


async def earn_points_for_order(
    db: AsyncSession,
    org_id: int,
    user_id: int,
    order_total: float,
    order_id: int | None = None,
) -> int:
    """
    Начисляет баллы за завершённый заказ по курсу loyalty_points_per_kzt.
    Например, при курсе 100: за 2500 KZT → 25 баллов.
    Возвращает количество начисленных баллов (0 если loyalty выключен).
    """
    from app.core.config import settings
    if not settings.loyalty_enabled:
        return 0
    if settings.loyalty_points_per_kzt <= 0:
        return 0
    earned = int(order_total // settings.loyalty_points_per_kzt)
    if earned <= 0:
        return 0
    await add_points(db, org_id, user_id, earned,
                     kind="earn", order_id=order_id,
                     note=f"Заказ #{order_id}: {int(order_total):,} ₸")
    return earned


async def get_loyalty_context_line(
    db: AsyncSession, org_id: int, user_id: int,
) -> str:
    """
    Возвращает строку для system prompt если лояльность включена.
    Пример: 'Бонусный баланс клиента: 250 баллов.'
    Пусто если LOYALTY_ENABLED=false или баланс 0.
    """
    from app.core.config import settings
    if not settings.loyalty_enabled:
        return ""
    try:
        balance = await get_balance(db, org_id, user_id)
        if balance > 0:
            return f"Бонусный баланс клиента: {balance} баллов."
    except Exception as exc:
        logger.debug("get_loyalty_context_line failed: %s", exc)
    return ""
