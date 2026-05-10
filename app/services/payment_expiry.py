"""
Фоновая задача: помечает просроченные PaymentTransaction как expired.

Запускать периодически (раз в 5–15 минут):
    await expire_stale_payment_transactions(db)

После expire — emit SystemEvent("payment_expired") + вернуть список для reminder-а.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PaymentTransaction, PaymentTxStatus
from app.services.system_events import emit_system_event

logger = logging.getLogger(__name__)


async def expire_stale_payment_transactions(db: AsyncSession) -> list[PaymentTransaction]:
    """
    Переводит pending-транзакции с просроченным expires_at в статус expired.

    Возвращает список обновлённых транзакций (для последующего reminder-а).
    """
    now = datetime.now(timezone.utc)

    expired_rows = await db.scalars(
        select(PaymentTransaction).where(
            PaymentTransaction.status == PaymentTxStatus.PENDING.value,
            PaymentTransaction.expires_at.is_not(None),
            PaymentTransaction.expires_at < now,
        )
    )
    expired_list = list(expired_rows)

    if not expired_list:
        return []

    tx_ids = [tx.id for tx in expired_list]

    await db.execute(
        update(PaymentTransaction)
        .where(PaymentTransaction.id.in_(tx_ids))
        .values(status=PaymentTxStatus.EXPIRED.value)
    )

    for tx in expired_list:
        try:
            await emit_system_event(
                db,
                organization_id=tx.organization_id,
                event_type="payment_expired",
                source="payment_expiry_job",
                entity_type="payment_transaction",
                entity_id=tx.id,
                idempotency_key=f"payment_expired:{tx.id}",
                payload={
                    "payment_transaction_id": tx.id,
                    "order_id": tx.order_id,
                    "provider": tx.provider,
                    "amount": float(tx.amount),
                },
            )
        except Exception:
            logger.warning("emit_system_event failed for tx=%s", tx.id, exc_info=True)

    await db.flush()
    logger.info("Expired %d payment transaction(s)", len(tx_ids))
    return expired_list

