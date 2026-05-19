"""
Авто-отправка заказа в iiko после подтверждения оплаты (вебхук или ручная отметка в админке).

Порядок важен для Telegram: при переводе draft → confirmed публикуем order_updated (CONFIRMED),
чтобы сработало уведомление «НОВЫЙ ЗАКАЗ» в notification_router — как при ответе клиента «Да».
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session as db_session
from app.db.models import Order, OrderStatus, Organization, User
from app.services.dialog_mgr import clear_pending_order
from app.services.events import publish_event
from app.services.intent_router import confirm_order
from app.services.tenant_scope import orders_tenant_clause

logger = logging.getLogger(__name__)


async def _resolve_order_restaurant_org(
    db: AsyncSession,
    order: Order,
) -> tuple[Organization | None, int | None]:
    """
    Организация ресторана для заказа: прямой organization_id или legacy (NULL) через user.
    """
    if order.organization_id is not None:
        oid = int(order.organization_id)
        org = await db.get(Organization, oid)
        return org, oid
    if order.user_id is None:
        return None, None
    u_oid = await db.scalar(select(User.organization_id).where(User.id == order.user_id))
    if u_oid is None:
        return None, None
    oid = int(u_oid)
    org = await db.get(Organization, oid)
    return org, oid


async def run_auto_send_to_iiko_after_payment(order_id: int) -> None:
    """Если у организации включён флаг — подтвердить заказ (при необходимости) и отправить в iiko."""
    from app.api.admin import _check_mixed_payment_split
    from app.api.webhooks import _send_order_to_iiko

    async with db_session.async_session_factory() as db:
        order = await db.get(Order, order_id)
        if order is None:
            return
        org, org_id = await _resolve_order_restaurant_org(db, order)
        if (
            org is None
            or org_id is None
            or not bool(getattr(org, "auto_send_to_iiko_after_payment", False))
        ):
            return
        if (order.prepayment_status or "").strip().lower() != "paid":
            return

        cur = (order.status or "").strip().lower()
        if cur in (OrderStatus.SENT_TO_IIKO.value, OrderStatus.SENDING_TO_IIKO.value):
            return

        phone_s = (
            await db.scalar(select(User.phone).where(User.id == order.user_id))
        ) or ""
        phone_s = phone_s.strip()
        split_warn = _check_mixed_payment_split(
            order.items_json if isinstance(order.items_json, dict) else None,
            float(order.total_price or 0),
        )
        if split_warn:
            logger.warning("auto_iiko skipped order=%s: %s", order_id, split_warn)
            return

        if cur == OrderStatus.DRAFT.value:
            o2 = await confirm_order(db, order_id)
            if o2 is None:
                logger.warning("auto_iiko: confirm_order returned None order=%s", order_id)
                return
            await db.commit()
            await db.refresh(o2)
            created_at_iso = o2.created_at.isoformat() if getattr(o2, "created_at", None) else None
            evt_data: dict = {
                "order_id": o2.id,
                "status": OrderStatus.CONFIRMED,
                "phone": phone_s,
                "total_price": float(o2.total_price),
                "iiko_last_error": None,
                "organization_id": org_id,
            }
            if created_at_iso:
                evt_data["created_at"] = created_at_iso
            await publish_event("order_updated", evt_data)
            if phone_s:
                await clear_pending_order(db_session.redis_client, phone_s, organization_id=org_id)
            order = o2
            cur = (order.status or "").strip().lower()

        if cur != OrderStatus.CONFIRMED.value:
            logger.info("auto_iiko: unexpected status order=%s status=%s", order_id, cur)
            return

        expected = int(order.row_version or 0)
        claimed = (
            await db.execute(
                update(Order)
                .where(
                    Order.id == order.id,
                    orders_tenant_clause(org_id),
                    Order.status == OrderStatus.CONFIRMED.value,
                    Order.row_version == expected,
                )
                .values(
                    status=OrderStatus.SENDING_TO_IIKO.value,
                    row_version=Order.row_version + 1,
                    iiko_last_error=None,
                )
            )
        ).rowcount
        if claimed != 1:
            logger.info("auto_iiko: claim sending_to_iiko failed order=%s (race?)", order_id)
            return
        await db.commit()

        snap = await db.get(Order, order_id)
        if snap is None:
            return
        oid = int(snap.id)
        items_snapshot = snap.items_json

    sent_to_iiko, iiko_err, iiko_raw = await _send_order_to_iiko(
        order_id=oid,
        phone=phone_s,
        items_json=items_snapshot if isinstance(items_snapshot, dict) else None,
        restaurant_organization_id=org_id,
    )

    async with db_session.async_session_factory() as db2:
        locked = await db2.get(Order, order_id)
        if locked is None:
            return
        if sent_to_iiko:
            locked.status = OrderStatus.SENT_TO_IIKO.value
            locked.iiko_last_error = None
            if iiko_raw and isinstance(locked.items_json, dict):
                ij = dict(locked.items_json)
                raw_om = ij.get("order_meta")
                om: dict = dict(raw_om) if isinstance(raw_om, dict) else {}
                oi = iiko_raw.get("orderInfo") if isinstance(iiko_raw.get("orderInfo"), dict) else {}
                om["iiko_last_send"] = {
                    "correlation_id": iiko_raw.get("correlationId"),
                    "iiko_order_id": oi.get("id"),
                    "external_number": str(locked.id),
                    "sent_at": datetime.now(timezone.utc).isoformat(),
                }
                ij["order_meta"] = om
                locked.items_json = ij
        else:
            locked.status = OrderStatus.CONFIRMED.value
            locked.iiko_last_error = iiko_err or "iiko: неизвестная ошибка"
            try:
                from app.services.integration_events import emit_integration_iiko_failed

                await emit_integration_iiko_failed(
                    db2,
                    org_id=int(locked.organization_id or org_id),
                    order_id=int(locked.id),
                    error=locked.iiko_last_error or "",
                    phone=phone_s or None,
                    location_id=getattr(locked, "location_id", None),
                )
            except Exception:
                logger.exception("emit iiko failed event order=%s", locked.id)
        locked.row_version = int(locked.row_version or 0) + 1
        await db2.commit()
        phone_out = (
            await db2.scalar(select(User.phone).where(User.id == locked.user_id))
        ) or ""
        phone_out = phone_out.strip()
        pub = {
            "order_id": int(locked.id),
            "status": str(locked.status or ""),
            "phone": phone_out,
            "total_price": float(locked.total_price or 0),
            "iiko_last_error": locked.iiko_last_error,
            "organization_id": org_id,
        }

    await publish_event("order_updated", pub)
