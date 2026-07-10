"""
Payment reminder: после истечения транзакции пробует re-initiation и шлёт клиенту новую ссылку.

Вызывается из payment_expiry_loop после expire_stale_payment_transactions().
Паттерн: db закрыт → LLM/HTTP → новый db (см. CLAUDE.md запрет 3).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Order, Organization, PaymentTransaction, User
from app.services.payment_initiation import NoPaymentConfigError, re_initiate_payment

logger = logging.getLogger(__name__)


async def send_payment_reminder(
    db: AsyncSession,
    *,
    expired_tx: PaymentTransaction,
) -> bool:
    """
    Для одной истёкшей транзакции:
    1. Загружает Order + User + Organization.
    2. Инициирует новую транзакцию через провайдера.
    3. Шлёт WhatsApp-сообщение с новой ссылкой.

    Возвращает True если ссылка успешно отправлена.
    """
    from app.services.customer_reply import send_customer_text

    order = await db.get(Order, expired_tx.order_id)
    if order is None:
        logger.warning("payment_reminder: order=%s не найден", expired_tx.order_id)
        return False

    # Не повторяем, если уже оплачен/отменён
    if order.prepayment_status in ("paid", "waived", "not_required"):
        return False
    if order.status in ("cancelled", "completed"):
        return False

    user = await db.get(User, order.user_id)
    org = await db.get(Organization, expired_tx.organization_id)
    if user is None or org is None:
        return False

    try:
        new_tx = await re_initiate_payment(
            db,
            order=order,
            org=org,
            description=f"Повторная оплата заказа #{order.id}",
        )
        await db.commit()
    except NoPaymentConfigError:
        return False
    except Exception as exc:
        logger.warning("payment_reminder re_initiate failed order=%s: %s", order.id, exc)
        return False

    if not new_tx.payment_url:
        return False

    # Отправляем WhatsApp-сообщение с новой ссылкой
    amount_str = f"{int(float(new_tx.amount)):,}" if new_tx.amount else ""
    expires_min = 60
    if new_tx.expires_at:
        delta = new_tx.expires_at - datetime.now(timezone.utc)
        expires_min = max(5, int(delta.total_seconds() / 60))

    text = (
        f"⏰ Время оплаты заказа #{order.id} истекло.\n\n"
        f"Создана новая ссылка"
        + (f" на сумму {amount_str} {org.currency or 'KZT'}" if amount_str else "")
        + f" (действует {expires_min} мин):\n\n"
        f"👉 {new_tx.payment_url}\n\n"
        "После оплаты вы сможете подтвердить заказ ответом «Да»."
    )

    try:
        await send_customer_text(user.phone, text, organization_id=int(expired_tx.organization_id))
        logger.info(
            "payment_reminder: order=%s новая ссылка отправлена phone=%s tx=%s",
            order.id, user.phone, new_tx.id,
        )
        return True
    except Exception as exc:
        logger.warning(
            "payment_reminder: WhatsApp send failed order=%s error=%s",
            order.id, exc,
        )
    return False


async def send_payment_reminders_for_expired(
    expired_transactions: list[PaymentTransaction],
    session_factory,
) -> int:
    """
    Обрабатывает список истёкших транзакций: для каждой открывает отдельную DB-сессию.
    Возвращает число успешно отправленных напоминаний.
    """
    sent = 0
    for tx in expired_transactions:
        try:
            async with session_factory() as db:
                ok = await send_payment_reminder(db, expired_tx=tx)
            if ok:
                sent += 1
        except Exception as exc:
            logger.error(
                "payment_reminder failed tx=%s order=%s: %s",
                tx.id, tx.order_id, exc, exc_info=True,
            )
    return sent
