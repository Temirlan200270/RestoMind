"""
Персонализация допродаж на основе истории заказов клиента.

Анализирует последние 20 подтверждённых заказов и возвращает:
- never_categories: категории, которые клиент никогда/почти никогда не берёт (< 5% заказов)
- avg_total: средний чек
- drinks_frequency: доля заказов, где были напитки

Используется в build_sales_strategy() для фильтрации неподходящих кандидатов upsell.
"""

from __future__ import annotations

import logging
from collections import Counter

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Order, OrderStatus

logger = logging.getLogger(__name__)

_DRINK_CAT_HINTS = ("напит", "кофе", "чай", "бар", "сок")
_COMPLETED_STATUSES = (
    OrderStatus.CONFIRMED.value,
    OrderStatus.SENT_TO_IIKO.value,
    OrderStatus.COMPLETED.value,
)


async def get_user_preferences(
    db: AsyncSession,
    user_id: int,
    org_id: int,
    limit: int = 20,
) -> dict:
    """
    Возвращает предпочтения клиента на основе истории заказов.

    Результат:
    {
        "never_categories": set[str],   # категории с частотой < 5%
        "avg_total": float,             # средний чек
        "drinks_frequency": float,      # доля заказов с напитками (0.0–1.0)
    }
    Если истории нет — возвращает пустой dict.
    """
    try:
        rows = (await db.execute(
            select(Order.items_json)
            .where(
                Order.user_id == user_id,
                Order.organization_id == org_id,
                Order.status.in_(list(_COMPLETED_STATUSES)),
            )
            .order_by(Order.created_at.desc())
            .limit(limit)
        )).scalars().all()
    except Exception:
        logger.exception("personalization: ошибка загрузки истории user_id=%s", user_id)
        return {}

    if not rows:
        return {}

    cat_order_counts: Counter[str] = Counter()
    total_orders = 0
    drinks_orders = 0
    totals: list[float] = []

    for items_json in rows:
        if not isinstance(items_json, dict):
            continue
        items = items_json.get("items") or []
        total_orders += 1

        order_total = float(items_json.get("total_price") or 0)
        if order_total > 0:
            totals.append(order_total)

        cats_in_order: set[str] = set()
        has_drink = False
        for item in items:
            if not isinstance(item, dict):
                continue
            cat = (item.get("category") or "").strip().lower()
            if cat:
                cats_in_order.add(cat)
                if any(h in cat for h in _DRINK_CAT_HINTS):
                    has_drink = True

        for cat in cats_in_order:
            cat_order_counts[cat] += 1
        if has_drink:
            drinks_orders += 1

    if total_orders == 0:
        return {}

    never_categories: set[str] = {
        cat for cat, count in cat_order_counts.items()
        if cat and (count / total_orders) < 0.05
    }
    avg_total = sum(totals) / len(totals) if totals else 0.0
    drinks_frequency = drinks_orders / total_orders

    logger.debug(
        "personalization user_id=%s: never=%s drinks_freq=%.2f avg_total=%.0f",
        user_id, never_categories, drinks_frequency, avg_total,
    )

    return {
        "never_categories": never_categories,
        "avg_total": avg_total,
        "drinks_frequency": drinks_frequency,
    }
