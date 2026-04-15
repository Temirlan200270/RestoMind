"""
Идемпотентное применение уведомлений об оплате от внешних провайдеров (Kaspi, эквайринг и т.д.).
"""

from __future__ import annotations

import re
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Order, PaymentEvent

PaymentWebhookStatus = Literal["paid", "failed"]


def _normalize_provider_slug(provider: str) -> str:
    s = (provider or "generic").strip().lower()
    s = re.sub(r"[^a-z0-9_-]+", "_", s).strip("_")
    return (s[:48] or "generic")


def _idempotency_note(provider_slug: str, payment_id: str) -> str:
    pid = (payment_id or "").strip()
    return f"{provider_slug}:{pid}"


async def apply_payment_webhook(
    db: AsyncSession,
    *,
    order_id: int,
    organization_id: int,
    payment_id: str,
    provider: str,
    status: PaymentWebhookStatus,
    amount: float | None,
) -> dict:
    """
    Обновляет prepayment_status при status=paid, пишет PaymentEvent.
    Дубликат по (order, event_type, note) возвращает duplicate=True без повторной записи.
    """
    order = await db.get(Order, order_id)
    if order is None:
        raise LookupError("order_not_found")
    oid = order.organization_id
    if oid is None or int(oid) != int(organization_id):
        raise PermissionError("organization_mismatch")

    if not (payment_id or "").strip():
        raise ValueError("invalid_payment_id")
    prov = _normalize_provider_slug(provider)
    note_key = _idempotency_note(prov, payment_id)

    if status == "paid":
        existing = await db.scalar(
            select(PaymentEvent.id).where(
                PaymentEvent.order_id == order.id,
                PaymentEvent.event_type == "webhook_paid",
                PaymentEvent.note == note_key,
            ).limit(1),
        )
        if existing is not None:
            return {
                "ok": True,
                "duplicate": True,
                "prepayment_status": order.prepayment_status,
            }
        order.prepayment_status = "paid"
        amt = float(amount) if amount is not None else float(order.total_price or 0)
        ext_id = (payment_id or "").strip()[:200]
        order.payment_provider = prov
        order.external_payment_id = ext_id
        order.payment_amount_captured = amt
        db.add(
            PaymentEvent(
                order_id=order.id,
                event_type="webhook_paid",
                actor="webhook",
                amount=amt,
                note=note_key,
            ),
        )
        return {"ok": True, "duplicate": False, "prepayment_status": order.prepayment_status}

    existing_fail = await db.scalar(
        select(PaymentEvent.id).where(
            PaymentEvent.order_id == order.id,
            PaymentEvent.event_type == "webhook_failed",
            PaymentEvent.note == note_key,
        ).limit(1),
    )
    if existing_fail is not None:
        return {"ok": True, "duplicate": True, "prepayment_status": order.prepayment_status}

    db.add(
        PaymentEvent(
            order_id=order.id,
            event_type="webhook_failed",
            actor="webhook",
            amount=float(amount) if amount is not None else None,
            note=note_key,
        ),
    )
    return {"ok": True, "duplicate": False, "prepayment_status": order.prepayment_status}
