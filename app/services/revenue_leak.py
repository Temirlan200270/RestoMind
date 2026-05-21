"""Revenue Leak Detector — считаем упущенные деньги сегодня.

Rule 0: любой код здесь показывает владельцу потерю денег или помогает её вернуть.

Три источника потерь:
  abandoned_drafts_kzt  — DRAFT-заказы > 1 часа, которые так и не стали confirmed
  slow_response_kzt     — диалоги где гость ждёт ответа > 5 минут (не вернётся)
  cancelled_today_kzt   — реальная сумма отменённых заказов за сегодня
  menu_confusion_kzt    — гость спросил блюдо, но бот ответил "не нашёл"/"в стопе"
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import ChatLog, Order, OrderStatus
from app.services.tenant_scope import chat_logs_location_filter, orders_location_filter, orders_tenant_clause

logger = logging.getLogger(__name__)

_COMPLETED = (
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


async def _calc_aov(
    db: AsyncSession,
    org_id: int,
    *,
    days: int = 30,
    location_id: int | None = None,
    allowed_location_ids: set[int] | None = None,
) -> float:
    """Средний чек за последние N дней по подтверждённым заказам."""
    since = _sql_dt(datetime.now(tz=timezone.utc) - timedelta(days=days))
    row = (await db.execute(
        select(
            func.coalesce(func.sum(Order.total_price), 0),
            func.count(Order.id),
        ).where(
            orders_tenant_clause(org_id),
            Order.status.in_(list(_COMPLETED)),
            Order.created_at >= since,
            Order.total_price > 0,
            orders_location_filter(allowed_location_ids, location_id),
        )
    )).one()
    total, count = float(row[0]), int(row[1])
    return round(total / count, 2) if count > 0 else 0.0


async def _abandoned_drafts_kzt(
    db: AsyncSession,
    org_id: int,
    aov: float,
    location_id: int | None = None,
    allowed_location_ids: set[int] | None = None,
) -> float:
    """DRAFT-заказы старше 1 часа — гость начал, но не дошёл до оплаты.

    Оценка потери: кол-во × AOV (могли бы подтвердиться).
    """
    cutoff = _sql_dt(datetime.now(tz=timezone.utc) - timedelta(hours=1))
    count = int(
        await db.scalar(
            select(func.count(Order.id)).where(
                orders_tenant_clause(org_id),
                Order.status == OrderStatus.DRAFT.value,
                Order.created_at <= cutoff,
                orders_location_filter(allowed_location_ids, location_id),
            )
        ) or 0
    )
    return round(count * aov, 2)


async def _slow_response_kzt(
    db: AsyncSession,
    org_id: int,
    aov: float,
    location_id: int | None = None,
    allowed_location_ids: set[int] | None = None,
) -> float:
    """Диалоги где последнее сообщение от гостя не получило ответа > 5 минут.

    Оценка потери: кол-во × (AOV × 0.5) — половина среднего чека, т.к. не факт что заказ был.
    """
    now = datetime.now(tz=timezone.utc)
    window_start = _sql_dt(now - timedelta(minutes=30))  # активные за последние 30 мин
    threshold = _sql_dt(now - timedelta(minutes=5))       # ждут > 5 мин

    # user_id-ы кто написал в последние 30 мин, но чьё последнее сообщение > 5 мин назад
    # и после него нет ответа ассистента
    user_rows = (await db.execute(
        select(ChatLog.user_id, func.max(ChatLog.created_at).label("last_user_msg"))
        .where(
            ChatLog.organization_id == org_id,
            ChatLog.role == "user",
            ChatLog.user_id.isnot(None),
            ChatLog.created_at >= window_start,
            ChatLog.created_at <= threshold,
            chat_logs_location_filter(allowed_location_ids, location_id),
        )
        .group_by(ChatLog.user_id)
    )).all()

    if not user_rows:
        return 0.0

    stuck_count = 0
    for user_id, last_user_msg in user_rows:
        last_user_dt = _sql_dt(last_user_msg) if last_user_msg else None
        if last_user_dt is None:
            continue
        # Есть ли ответ ассистента после последнего сообщения гостя?
        has_reply = bool(
            await db.scalar(
                select(func.count(ChatLog.id)).where(
                    ChatLog.organization_id == org_id,
                    ChatLog.user_id == user_id,
                    ChatLog.role == "assistant",
                    ChatLog.created_at > last_user_dt,
                    chat_logs_location_filter(allowed_location_ids, location_id),
                )
            )
        )
        if not has_reply:
            stuck_count += 1

    return round(stuck_count * aov * 0.5, 2)


async def _cancelled_today_kzt(
    db: AsyncSession,
    org_id: int,
    location_id: int | None = None,
    allowed_location_ids: set[int] | None = None,
) -> float:
    """Реальная сумма отменённых заказов за сегодня (UTC)."""
    today_start = _sql_dt(datetime.now(tz=timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0,
    ))
    total = float(
        await db.scalar(
            select(func.coalesce(func.sum(Order.total_price), 0)).where(
                orders_tenant_clause(org_id),
                Order.status == OrderStatus.CANCELLED.value,
                Order.created_at >= today_start,
                Order.total_price > 0,
                orders_location_filter(allowed_location_ids, location_id),
            )
        ) or 0
    )
    return round(total, 2)


async def _menu_confusion_kzt(
    db: AsyncSession,
    org_id: int,
    aov: float,
    location_id: int | None = None,
    allowed_location_ids: set[int] | None = None,
) -> float:
    """Меню-путаница за сегодня: бот не смог продать блюдо из-за unknown/stoplist ответа.

    Оценка такая же осторожная, как slow_response: диалог мог стать заказом, но
    не каждый вопрос о блюде равен покупке.
    """
    today_start = _sql_dt(datetime.now(tz=timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0,
    ))
    rows = (await db.execute(
        select(ChatLog.user_id, ChatLog.content)
        .where(
            ChatLog.organization_id == org_id,
            ChatLog.role == "assistant",
            ChatLog.created_at >= today_start,
            chat_logs_location_filter(allowed_location_ids, location_id),
            ChatLog.user_id.isnot(None),
        )
    )).all()
    markers = (
        "не нашёл в меню",
        "не найдено в меню",
        "временно недоступно",
        "в стопе",
        "not found",
    )
    confused_users = {
        int(user_id)
        for user_id, content in rows
        if user_id is not None and any(marker in str(content or "").lower() for marker in markers)
    }
    return round(len(confused_users) * aov * 0.5, 2)


def _action_surface(
    *,
    surface_id: str,
    severity: str,
    title: str,
    count: int,
    risk_kzt: float,
    actions: list[dict],
) -> dict | None:
    if count <= 0:
        return None
    return {
        "id": surface_id,
        "severity": severity,
        "title": title,
        "count": int(count),
        "risk_kzt": round(float(risk_kzt), 2),
        "actions": actions,
    }


async def build_leak_action_surfaces(
    db: AsyncSession,
    org_id: int,
    *,
    aov: float,
    location_id: int | None = None,
    allowed_location_ids: set[int] | None = None,
) -> list[dict]:
    """G8: actionable surfaces — агрегирует G5/G6/G7 без новой бизнес-логики."""
    from app.services.money_queue import summarize_queue_counts

    counts = await summarize_queue_counts(
        db,
        org_id,
        location_id=location_id,
        allowed_location_ids=allowed_location_ids,
    )
    slow_risk = round(float(counts["slow_chats"]) * aov * 0.5, 2) if aov > 0 else 0.0

    surfaces: list[dict] = []
    for surface in (
        _action_surface(
            surface_id="lost_drafts",
            severity="critical",
            title="Брошенные заказы",
            count=int(counts["abandoned_drafts"]),
            risk_kzt=float(counts["abandoned_drafts_kzt"]),
            actions=[
                {
                    "id": "recover_all",
                    "label": "Вернуть все",
                    "type": "api",
                    "method": "POST",
                    "path": "/api/admin/intelligence/revenue-leak/recover-drafts",
                },
                {
                    "id": "open_list",
                    "label": "Открыть список",
                    "type": "navigate",
                    "tab": "inbox",
                    "inboxTab": "clients",
                },
            ],
        ),
        _action_surface(
            surface_id="slow_chats",
            severity="warning",
            title="Медленные ответы",
            count=int(counts["slow_chats"]),
            risk_kzt=slow_risk,
            actions=[
                {
                    "id": "open_red_chats",
                    "label": "Открыть красные",
                    "type": "navigate",
                    "tab": "chats",
                    "chatPulseFilter": "red",
                },
                {
                    "id": "open_slow_chats",
                    "label": "Все ждущие",
                    "type": "navigate",
                    "tab": "chats",
                    "chatPulseFilter": "slow",
                },
            ],
        ),
        _action_surface(
            surface_id="pending_prepay",
            severity="info",
            title="Ожидают оплату",
            count=int(counts["pending_prepay"]),
            risk_kzt=float(counts["pending_prepay_kzt"]),
            actions=[
                {
                    "id": "open_money_queue",
                    "label": "Открыть очередь",
                    "type": "navigate",
                    "tab": "inbox",
                    "inboxTab": "clients",
                },
                {
                    "id": "open_orders",
                    "label": "Открыть заказы",
                    "type": "navigate",
                    "tab": "orders",
                    "orderSumMin": 1,
                },
            ],
        ),
        _action_surface(
            surface_id="high_value_stuck",
            severity="critical",
            title="Крупные зависшие",
            count=int(counts["high_value_stuck"]),
            risk_kzt=float(counts["high_value_stuck_kzt"]),
            actions=[
                {
                    "id": "prioritize",
                    "label": "В приоритет",
                    "type": "navigate",
                    "tab": "inbox",
                    "inboxTab": "clients",
                },
                {
                    "id": "open_high_orders",
                    "label": "Открыть заказы",
                    "type": "navigate",
                    "tab": "orders",
                    "orderSumMin": 5000,
                },
            ],
        ),
    ):
        if surface is not None:
            surfaces.append(surface)
    return surfaces


async def build_revenue_leak(
    db: AsyncSession,
    org_id: int,
    *,
    location_id: int | None = None,
    allowed_location_ids: set[int] | None = None,
) -> dict:
    """Считает три источника потерь и возвращает итог для Hero Block дашборда."""
    aov = await _calc_aov(
        db, org_id, location_id=location_id, allowed_location_ids=allowed_location_ids,
    )
    abandoned = await _abandoned_drafts_kzt(
        db, org_id, aov, location_id=location_id, allowed_location_ids=allowed_location_ids,
    )
    slow = await _slow_response_kzt(
        db, org_id, aov, location_id=location_id, allowed_location_ids=allowed_location_ids,
    )
    cancelled = await _cancelled_today_kzt(
        db, org_id, location_id=location_id, allowed_location_ids=allowed_location_ids,
    )
    menu_confusion = await _menu_confusion_kzt(
        db, org_id, aov, location_id=location_id, allowed_location_ids=allowed_location_ids,
    )
    total = round(abandoned + slow + cancelled + menu_confusion, 2)
    surfaces = await build_leak_action_surfaces(
        db,
        org_id,
        aov=aov,
        location_id=location_id,
        allowed_location_ids=allowed_location_ids,
    )
    action_risk_kzt = round(
        sum(float(s.get("risk_kzt") or 0) for s in surfaces),
        2,
    )

    logger.debug(
        "revenue_leak org=%d: total=%.0f abandoned=%.0f slow=%.0f cancelled=%.0f menu_confusion=%.0f aov=%.0f surfaces=%d",
        org_id, total, abandoned, slow, cancelled, menu_confusion, aov, len(surfaces),
    )

    return {
        "total_leak_kzt": total,
        "action_risk_kzt": action_risk_kzt,
        "aov": aov,
        "location_id": location_id,
        "surfaces": surfaces,
        "breakdown": {
            "abandoned_drafts_kzt": abandoned,
            "slow_response_kzt": slow,
            "cancelled_today_kzt": cancelled,
            "menu_confusion_kzt": menu_confusion,
        },
        "labels": {
            "abandoned_drafts": "Брошенные корзины",
            "slow_response": "Медленные ответы",
            "cancelled_today": "Отмены сегодня",
            "menu_confusion": "Путаница в меню",
        },
    }
