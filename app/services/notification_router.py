"""
Fan-out критичных событий в Telegram персонала по настройкам организации.
Не дублирует разбор бизнес-логики — только реагирует на type + data из publish_event.
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.config import settings
from app.db.models import OrderStatus
from app.integrations.telegram import (
    _absolute_admin_dialog_url,
    _absolute_admin_orders_url,
    _escape_html,
    format_phone_for_alert,
    send_ops_notification_html,
)

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
                phone_raw = str(data.get("phone") or "").strip()
                phone_disp = _escape_html(format_phone_for_alert(phone_raw))
                order_id = data.get("order_id")
                total = data.get("total_price")
                total_disp = _escape_html(str(total)) if total is not None else "—"
                msg = (
                    "🎉 <b>НОВЫЙ ЗАКАЗ</b>\n"
                    f"№ <code>{_escape_html(str(order_id))}</code>\n"
                    "\n"
                    f"👤 <b>Клиент:</b> <code>{phone_disp}</code>\n"
                    f"💰 <b>Сумма:</b> <code>{total_disp}</code>\n"
                    "\n"
                    "📝 <b>Статус:</b> клиент подтвердил в WhatsApp — ожидает работы кухни/оператора."
                )
                orders_url = _absolute_admin_orders_url()
                markup = (
                    {
                        "inline_keyboard": [
                            [{"text": "ПОСМОТРЕТЬ В АДМИНКЕ ↗️", "url": orders_url}],
                        ],
                    }
                    if orders_url
                    else None
                )
                await send_ops_notification_html(msg, organization_id=oid, reply_markup=markup)
        elif event_type == "message_status_updated":
            if (data.get("delivery_status") or "").strip().lower() != "failed":
                return
            phone_raw = str(data.get("phone") or "").strip()
            phone_disp = _escape_html(format_phone_for_alert(phone_raw))
            msg = (
                "⚠️ <b>Сбой доставки WhatsApp</b>\n"
                "\n"
                f"👤 <b>Клиент:</b> <code>{phone_disp}</code>\n"
                "\n"
                "<i>Сообщение не дошло до гостя. Проверьте номер / лимиты Meta / режим песочницы.</i>"
            )
            dialog_url = _absolute_admin_dialog_url(phone_raw)
            markup = (
                {"inline_keyboard": [[{"text": "ОТКРЫТЬ ЧАТ ↗️", "url": dialog_url}]]}
                if dialog_url
                else None
            )
            await send_ops_notification_html(msg, organization_id=oid, reply_markup=markup)
    except Exception:
        logger.exception("notify_staff_from_event: %s", event_type)
