"""
Уведомление клиента в WhatsApp после подтверждения оплаты + запись в chat_logs (роль system).
Вызывается из FastAPI BackgroundTasks после commit транзакции оплаты.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ChatLog, Order, User
from app.services.customer_reply import send_customer_text

logger = logging.getLogger(__name__)

_NOTIFY_KIND = "payment_confirmed"


async def _already_notified(db: AsyncSession, user_id: int, order_id: int) -> bool:
    q = (
        select(ChatLog.meta_json)
        .where(ChatLog.user_id == user_id, ChatLog.role == "system")
        .order_by(ChatLog.id.desc())
        .limit(48)
    )
    rows = (await db.execute(q)).all()
    oid = int(order_id)
    for (mj,) in rows:
        if isinstance(mj, dict) and mj.get("kind") == _NOTIFY_KIND and int(mj.get("order_id") or 0) == oid:
            return True
    return False


async def run_payment_received_customer_notify(order_id: int) -> None:
    """Идемпотентно: одно system-сообщение на заказ; доставка через тот же контур, что и ответы бота."""
    from app.db.session import async_session_factory

    async with async_session_factory() as db:
        order = await db.get(Order, order_id)
        if order is None:
            return
        if (order.prepayment_status or "").strip().lower() != "paid":
            return
        user = await db.get(User, order.user_id)
        if user is None:
            return
        org_id = order.organization_id or user.organization_id
        if await _already_notified(db, user.id, order.id):
            return
        text = f"✅ Оплата получена! Ваш заказ #{order.id} передан на обработку."
        now = datetime.now(timezone.utc)
        log = ChatLog(
            organization_id=int(org_id),
            user_id=user.id,
            role="system",
            content=text,
            meta_json={"kind": _NOTIFY_KIND, "order_id": order.id},
            delivery_status="sending",
            status_updated_at=now,
        )
        db.add(log)
        await db.commit()
        await db.refresh(log)
        log_id = int(log.id)
        phone = (user.phone or "").strip()

    if not phone:
        logger.warning("payment_notify: нет телефона user_id=%s order=%s", user.id, order_id)
        return
    try:
        await send_customer_text(phone, text, outbound_chat_log_id=log_id)
    except Exception:
        logger.exception("payment_notify: не удалось отправить WhatsApp order=%s", order_id)
