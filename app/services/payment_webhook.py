"""
Идемпотентное применение уведомлений об оплате от внешних провайдеров (Kaspi, эквайринг и т.д.).
"""

from __future__ import annotations

import re
from typing import Literal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Order, PaymentEvent
from app.core.config import settings

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
    if not (payment_id or "").strip():
        raise ValueError("invalid_payment_id")
    prov = _normalize_provider_slug(provider)
    note_key = _idempotency_note(prov, payment_id)

    order = await db.get(Order, order_id)
    if order is None:
        raise LookupError("order_not_found")
    oid = order.organization_id
    if oid is None or int(oid) != int(organization_id):
        insert_stmt_m = sqlite_insert(PaymentEvent) if settings.db_mode == "sqlite" else pg_insert(PaymentEvent)
        mismatch_note = f"{note_key}:org_mismatch:expected={oid}:got={organization_id}"
        await db.execute(
            insert_stmt_m.values(
                order_id=order.id,
                event_type="webhook_failed",
                actor="webhook",
                amount=float(amount) if amount is not None else None,
                note=mismatch_note[:500],
            ).on_conflict_do_nothing(
                index_elements=["order_id", "event_type", "note"],
            ),
        )
        raise PermissionError("organization_mismatch")

    if status == "paid":
        insert_stmt = sqlite_insert(PaymentEvent) if settings.db_mode == "sqlite" else pg_insert(PaymentEvent)
        stmt = (
            insert_stmt.values(
                order_id=order.id,
                event_type="webhook_paid",
                actor="webhook",
                amount=float(amount) if amount is not None else float(order.total_price or 0),
                note=note_key,
            ).on_conflict_do_nothing(
                index_elements=["order_id", "event_type", "note"],
            )
        )
        res = await db.execute(stmt)
        if (res.rowcount or 0) == 0:
            return {"ok": True, "duplicate": True, "prepayment_status": order.prepayment_status}
        order.prepayment_status = "paid"
        amt = float(amount) if amount is not None else float(order.total_price or 0)
        ext_id = (payment_id or "").strip()[:200]
        order.payment_provider = prov
        order.external_payment_id = ext_id
        order.payment_amount_captured = amt
        return {"ok": True, "duplicate": False, "prepayment_status": order.prepayment_status}

    insert_stmt_f = sqlite_insert(PaymentEvent) if settings.db_mode == "sqlite" else pg_insert(PaymentEvent)
    stmt_f = (
        insert_stmt_f.values(
            order_id=order.id,
            event_type="webhook_failed",
            actor="webhook",
            amount=float(amount) if amount is not None else None,
            note=note_key,
        ).on_conflict_do_nothing(
            index_elements=["order_id", "event_type", "note"],
        )
    )
    res_f = await db.execute(stmt_f)
    if (res_f.rowcount or 0) == 0:
        return {"ok": True, "duplicate": True, "prepayment_status": order.prepayment_status}
    return {"ok": True, "duplicate": False, "prepayment_status": order.prepayment_status}
