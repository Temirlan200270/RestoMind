"""
Fan-out критичных событий в Telegram персонала по настройкам организации.
Не дублирует разбор бизнес-логики — только реагирует на type + data из publish_event.
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.config import settings
from app.db.models import OrderStatus
from app.integrations.telegram import _escape_html, send_ops_notification_html

logger = logging.getLogger(__name__)


def _org_id_from_payload(data: dict[str, Any]) -> int | None:
    raw = data.get("organization_id")
    if raw is None:
        return int(settings.default_organization_id)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return int(settings.default_organization_id)


async def notify_staff_from_event(event_type: str, data: dict[str, Any]) -> None:
    """Точечные алерты: новый подтверждённый заказ, сбой доставки сообщения."""
    oid = _org_id_from_payload(data)
    try:
        if event_type == "order_updated":
            st = data.get("status")
            st_s = st.value if hasattr(st, "value") else st
            st_s = (str(st_s) if st_s is not None else "").lower()
            if st_s == OrderStatus.CONFIRMED.value:
                phone = _escape_html(str(data.get("phone") or ""))
                order_id = data.get("order_id")
                total = data.get("total_price")
                msg = (
                    "<b>Новый подтверждённый заказ</b>\n"
                    f"№ <code>{_escape_html(str(order_id))}</code>\n"
                    f"Телефон: <code>{phone}</code>\n"
                    f"Сумма: <code>{_escape_html(str(total))}</code>"
                )
                await send_ops_notification_html(msg, organization_id=oid)
        elif event_type == "message_status_updated":
            if (data.get("delivery_status") or "").strip().lower() != "failed":
                return
            phone = _escape_html(str(data.get("phone") or ""))
            lid = data.get("chat_log_id")
            msg = (
                "<b>Сбой доставки WhatsApp</b>\n"
                f"Запись чата: <code>{_escape_html(str(lid))}</code>\n"
                f"Телефон: <code>{phone}</code>"
            )
            await send_ops_notification_html(msg, organization_id=oid)
    except Exception:
        logger.exception("notify_staff_from_event: %s", event_type)
