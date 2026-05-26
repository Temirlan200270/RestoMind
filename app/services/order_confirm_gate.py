"""Жёсткая проверка перед confirm_order — целостность корзины (не промпт)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MenuItem, Order, OrderStatus
from app.services.order_logic import load_available_menu, validate_order
from app.schemas.ai_schemas import OrderItem


@dataclass(frozen=True, slots=True)
class ConfirmGateResult:
    ok: bool
    reason: str = ""


def _food_lines(items_json: dict[str, Any]) -> list[dict[str, Any]]:
    raw = items_json.get("items") or []
    return [x for x in raw if isinstance(x, dict) and str(x.get("name") or "").strip()]


async def validate_order_ready_to_confirm(
    db: AsyncSession,
    order: Order,
    *,
    menu_items: list[MenuItem] | None = None,
    check_fulfillment: bool = False,
    order_meta: dict[str, Any] | None = None,
) -> ConfirmGateResult:
    """
    Блокирует confirm, если корзина неконсистентна.

    check_fulfillment=True — дополнительно адрес/время/оплата (WhatsApp «Да»).
    """
    if order.status != OrderStatus.DRAFT:
        return ConfirmGateResult(ok=False, reason="Заказ уже обработан или не является черновиком.")

    raw = order.items_json
    items_json: dict[str, Any] = dict(raw) if isinstance(raw, dict) else {}
    lines = _food_lines(items_json)
    if not lines:
        return ConfirmGateResult(ok=False, reason="В заказе нет позиций — подтвердить нечего.")

    meta_src = order_meta
    if meta_src is None:
        om = items_json.get("order_meta")
        meta_src = dict(om) if isinstance(om, dict) else {}

    if check_fulfillment:
        block = _fulfillment_block_reason(meta_src)
        if block:
            return ConfirmGateResult(ok=False, reason=block)

    org_id = int(order.organization_id) if order.organization_id else None
    if menu_items is None and org_id is not None:
        menu_items = await load_available_menu(db, organization_id=org_id, include_unavailable=True)

    order_items = [
        OrderItem(
            name=str(x.get("name") or ""),
            quantity=int(x.get("quantity") or 1),
            iiko_item_id=str(x.get("iiko_id") or x.get("iiko_item_id") or "") or None,
        )
        for x in lines
    ]
    validated = await validate_order(
        order_items,
        menu_items=menu_items or [],
        organization_id=org_id,
    )

    if validated.unknown_items:
        return ConfirmGateResult(
            ok=False,
            reason=f"Не все позиции найдены в меню: {', '.join(validated.unknown_items)}.",
        )
    if validated.stoplist_items:
        return ConfirmGateResult(
            ok=False,
            reason=f"В заказе есть позиции на стоп-листе: {', '.join(validated.stoplist_items)}.",
        )
    if not validated.valid_items:
        return ConfirmGateResult(ok=False, reason="Нет доступных позиций для подтверждения.")

    for line in lines:
        iid = str(line.get("iiko_id") or line.get("iiko_item_id") or "").strip()
        if not iid:
            return ConfirmGateResult(
                ok=False,
                reason=f"Позиция «{line.get('name')}» не привязана к меню — нужно уточнение.",
            )

    return ConfirmGateResult(ok=True)


def _fulfillment_block_reason(order_meta: dict[str, Any]) -> str:
    ot = str(order_meta.get("order_type") or "").strip().lower()
    if ot == "delivery" and not str(order_meta.get("delivery_address") or "").strip():
        return "Перед подтверждением нужен адрес доставки."
    if ot == "pickup" and not str(order_meta.get("pickup_time_note") or "").strip():
        return "Перед подтверждением уточните время самовывоза."
    if not ot:
        return "Перед подтверждением уточните способ получения: доставка, самовывоз или в зале."
    pm = str(order_meta.get("payment_method") or "").strip().lower()
    if not pm and ot != "hall":
        return "Перед подтверждением укажите способ оплаты."
    return ""
