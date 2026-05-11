"""
Сводка по клиенту для оператора (E0.1: вынесено из ``_monolith.py``).

Группа эндпоинтов работает в скоупе одного телефона активного филиала и
держит контракт стабильным: формат и пути не меняются (1:1 с монолитом),
вынос — чисто структурный.

Маршруты:

* ``GET  /api/admin/customers/{phone}/summary`` — агрегаты заказов, заметка,
  ai-snooze, последняя эскалация.
* ``POST /api/admin/customers/{phone}/note`` — текстовая заметка оператора.
* ``POST /api/admin/customers/{phone}/ai-pause`` — переключение ИИ для номера
  (HUMAN_MODE/CHATTING + Redis publish).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import EscalationEvent, Order, OrderStatus, User
from app.db.session import get_db, redis_client
from app.services.ai_snooze import clear_ai_snooze_if_expired
from app.services.dialog_mgr import UserState, set_user_state
from app.services.events import publish_event

from .deps import admin_org_from_session, require_admin_session_active

logger = logging.getLogger(__name__)

customers_router = APIRouter(dependencies=[Depends(require_admin_session_active)])


class CustomerNoteBody(BaseModel):
    """Тело запроса: заметка оператора о клиенте."""

    note: str = ""


class AiPauseBody(BaseModel):
    """Отключить или снова включить ИИ для клиента (персистентно + Redis)."""

    paused: bool = True


_REVENUE_STATUSES: tuple[str, ...] = (
    OrderStatus.CONFIRMED.value,
    OrderStatus.SENT_TO_IIKO.value,
    OrderStatus.IN_TRANSIT.value,
    OrderStatus.WAITING_PICKUP.value,
    OrderStatus.COMPLETED.value,
)


@customers_router.get("/customers/{phone}/summary")
async def customer_summary(
    request: Request,
    phone: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Сводка по клиенту для панели оператора: заказы, выручка, заметка, «чёрный список» (is_active).
    Если пользователя с таким телефоном ещё нет в БД — возвращаются нули (диалог только откроют вручную).
    """
    org_id = admin_org_from_session(request)
    user = await db.scalar(
        select(User).where(User.phone == phone, User.organization_id == org_id),
    )
    if user is None:
        return {
            "user_exists": False,
            "phone": phone,
            "name": None,
            "total_orders": 0,
            "revenue_orders": 0,
            "total_spent": 0.0,
            "avg_check": 0.0,
            "is_blocked": False,
            "ai_paused": False,
            "ai_snoozed_until": None,
            "operator_note": "",
            "last_escalation": None,
        }

    await clear_ai_snooze_if_expired(db, user)
    await db.commit()

    not_cancelled = Order.status != OrderStatus.CANCELLED.value
    cnt_all = await db.scalar(
        select(func.count(Order.id)).where(Order.user_id == user.id, not_cancelled),
    ) or 0

    rev_row = await db.execute(
        select(
            func.count(Order.id),
            func.coalesce(func.sum(Order.total_price), 0),
        ).where(Order.user_id == user.id, Order.status.in_(_REVENUE_STATUSES)),
    )
    rev = rev_row.one()
    rev_count = int(rev[0] or 0)
    total_spent = float(rev[1] or 0)
    avg_check = (total_spent / rev_count) if rev_count else 0.0

    esc_res = await db.execute(
        select(EscalationEvent)
        .where(
            EscalationEvent.phone == user.phone,
            or_(
                EscalationEvent.organization_id == org_id,
                EscalationEvent.organization_id.is_(None),
            ),
        )
        .order_by(EscalationEvent.created_at.desc())
        .limit(1),
    )
    esc_row = esc_res.scalars().first()
    last_escalation = None
    if esc_row is not None:
        last_escalation = {
            "created_at": esc_row.created_at.isoformat() if esc_row.created_at else None,
            "reason": (esc_row.reason or "")[:500],
            "user_message": (esc_row.user_message or "")[:500],
        }

    return {
        "user_exists": True,
        "phone": user.phone,
        "name": user.name,
        "total_orders": int(cnt_all),
        "revenue_orders": rev_count,
        "total_spent": total_spent,
        "avg_check": round(avg_check, 2),
        "is_blocked": not user.is_active,
        "ai_paused": bool(getattr(user, "ai_paused", False)),
        "ai_snoozed_until": user.ai_snoozed_until.isoformat() if user.ai_snoozed_until else None,
        "operator_note": user.operator_note or "",
        "last_escalation": last_escalation,
    }


@customers_router.post("/customers/{phone}/note")
async def save_customer_note(
    request: Request,
    phone: str,
    body: CustomerNoteBody,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Сохранить внутреннюю заметку оператора о клиенте."""
    org_id = admin_org_from_session(request)
    user = await db.scalar(
        select(User).where(User.phone == phone, User.organization_id == org_id),
    )
    if user is None:
        raise HTTPException(
            status_code=404,
            detail="Пользователь не найден — заметку можно сохранить после первого контакта клиента с ботом",
        )
    user.operator_note = body.note[:8000] if body.note else ""
    await db.flush()
    return {"ok": True}


@customers_router.post("/customers/{phone}/ai-pause")
async def set_customer_ai_pause(
    request: Request,
    phone: str,
    body: AiPauseBody,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Заблокировать ИИ для номера: бот не отвечает, пока не снять блокировку.
    Дублирует смысл «перехвата», но сохраняется в БД (переживает рестарт Redis).
    """
    org_id = admin_org_from_session(request)
    user = await db.scalar(
        select(User).where(User.phone == phone, User.organization_id == org_id),
    )
    if user is None:
        raise HTTPException(
            status_code=404,
            detail="Пользователь не найден — сначала должен быть диалог или заказ",
        )
    user.ai_paused = body.paused
    await db.flush()
    await db.commit()

    if body.paused:
        await set_user_state(redis_client, phone, UserState.HUMAN_MODE, organization_id=org_id)
        await publish_event(
            "state_changed",
            {"phone": phone, "state": UserState.HUMAN_MODE.value, "organization_id": org_id},
        )
    else:
        await set_user_state(redis_client, phone, UserState.CHATTING, organization_id=org_id)
        await publish_event(
            "state_changed",
            {"phone": phone, "state": UserState.CHATTING.value, "organization_id": org_id},
        )
    return {"ok": True, "ai_paused": user.ai_paused}
