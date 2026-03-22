"""
Бизнес-логика заказов.
Валидация позиций по таблице MenuItem в БД.
MOCK_MENU используется как fallback, если таблица пуста.
Нечёткий поиск (fuzzy matching) через difflib.
"""

import logging
from dataclasses import dataclass
from difflib import get_close_matches

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.data.plovxana_menu import build_mock_menu_dict
from app.db.models import MenuItem
from app.schemas.ai_schemas import AIBrainResponse, OrderItem

logger = logging.getLogger(__name__)

# Fallback, если таблица menu_items пуста (меню ПловXана из app/data/plovxana_menu.py)
MOCK_MENU: dict[str, float] = build_mock_menu_dict()
# Короткие синонимы для распознавания речи / тестов
MOCK_MENU.setdefault("плов", MOCK_MENU.get("плов праздничный баранина", 2790.0))
MOCK_MENU.setdefault("лагман", MOCK_MENU.get("лагман от шеф-повара", 2790.0))


@dataclass
class ValidatedOrder:
    """Результат валидации заказа."""

    valid_items: list[dict]
    unknown_items: list[str]
    total_price: float
    summary_text: str


@dataclass
class MenuEntry:
    """Запись справочника меню: цена + iiko UUID."""

    price: float
    iiko_id: str | None


async def load_available_menu(db: AsyncSession) -> list[MenuItem]:
    """Один запрос на весь цикл обработки — загрузка доступных позиций."""
    result = await db.execute(
        select(MenuItem)
        .where(MenuItem.is_available.is_(True))
        .order_by(MenuItem.category, MenuItem.name)
    )
    return list(result.scalars().all())


def _build_menu_lookup(
    db_items: list[MenuItem],
) -> dict[str, MenuEntry]:
    """Строит lookup из уже загруженных MenuItem. Fallback на MOCK_MENU."""
    if not db_items:
        logger.warning("Таблица menu_items пуста — используется MOCK_MENU")
        return {
            name: MenuEntry(price=price, iiko_id=None)
            for name, price in MOCK_MENU.items()
        }

    lookup: dict[str, MenuEntry] = {}
    for mi in db_items:
        lookup[mi.name.lower().strip()] = MenuEntry(
            price=float(mi.price), iiko_id=mi.iiko_id,
        )
    return lookup


async def validate_order(
    items: list[OrderItem],
    menu_items: list[MenuItem] | None = None,
    db: AsyncSession | None = None,
) -> ValidatedOrder:
    """
    Проверяет каждую позицию заказа по таблице MenuItem в БД.
    Принимает готовый список menu_items (чтобы не дублировать запрос)
    или db-сессию для ленивой загрузки. Fallback на MOCK_MENU.
    """
    if menu_items is None and db is not None:
        menu_items = await load_available_menu(db)
    menu_lookup = _build_menu_lookup(menu_items or [])
    menu_names = list(menu_lookup.keys())

    valid_items: list[dict] = []
    unknown_items: list[str] = []
    fuzzy_matched: list[str] = []
    total_price = 0.0

    for item in items:
        name_lower = item.name.lower().strip()
        entry = menu_lookup.get(name_lower)

        # Нечёткий поиск, если точного совпадения нет
        if entry is None and menu_names:
            matches = get_close_matches(name_lower, menu_names, n=1, cutoff=0.6)
            if matches:
                matched_name = matches[0]
                entry = menu_lookup[matched_name]
                fuzzy_matched.append(f"{item.name} → {matched_name}")
                logger.info("Fuzzy match: '%s' → '%s'", name_lower, matched_name)

        if entry is not None:
            item_total = entry.price * item.quantity
            total_price += item_total
            valid_items.append({
                "name": item.name,
                "quantity": item.quantity,
                "price_per_unit": entry.price,
                "item_total": item_total,
                "iiko_id": entry.iiko_id,
                "exclude_ingredients": item.exclude_ingredients,
            })
        else:
            unknown_items.append(item.name)

    lines = []
    for vi in valid_items:
        exclude_str = ""
        if vi["exclude_ingredients"]:
            exclude_str = f" (без {', '.join(vi['exclude_ingredients'])})"
        lines.append(
            f"  • {vi['name']} × {vi['quantity']} — {vi['item_total']:.0f} ₸{exclude_str}"
        )

    summary = "\n".join(lines)
    if fuzzy_matched:
        summary += "\n\n🔍 Уточнено автоматически: " + "; ".join(fuzzy_matched)
    if unknown_items:
        summary += f"\n\n⚠️ Не нашёл в меню: {', '.join(unknown_items)}"
    summary += f"\n\n💰 Итого: {total_price:.0f} ₸"

    logger.info(
        "Валидация заказа: %d найдено, %d не найдено, итого %.2f ₸",
        len(valid_items), len(unknown_items), total_price,
    )

    return ValidatedOrder(
        valid_items=valid_items,
        unknown_items=unknown_items,
        total_price=total_price,
        summary_text=summary,
    )


def build_menu_context(db_items: list[MenuItem]) -> str:
    """
    Формирует текстовое описание меню для System Prompt.
    AI использует его, чтобы знать реальные блюда и цены.
    Принимает уже загруженный список MenuItem.
    """
    if not db_items:
        lines = [f"- {name}: {price:.0f} ₸" for name, price in MOCK_MENU.items()]
        return "\n".join(lines)

    current_category = ""
    lines: list[str] = []
    for item in db_items:
        if item.category != current_category:
            current_category = item.category
            lines.append(f"\n## {current_category}")
        iiko_tag = f" [id: {item.iiko_id}]" if item.iiko_id else ""
        lines.append(f"- {item.name}: {float(item.price):.0f} ₸{iiko_tag}")

    return "\n".join(lines)


def _container_unit_price(order_type: str) -> float:
    """Цена одного контейнера в зависимости от типа получения заказа."""
    if order_type == "hall":
        return float(settings.pricing_container_hall)
    return float(settings.pricing_container_delivery_pickup)


def compute_fee_lines(
    food_lines: list[dict],
    foods_subtotal: float,
    order_type: str,
) -> tuple[list[dict], float]:
    """
    Контейнеры (на каждую порцию блюд) и доставка (если сумма блюд ниже порога).
    Возвращает (список fee_lines, сумма наценок).
    """
    fee_lines: list[dict] = []
    extras_total = 0.0

    qty_portions = sum(int(x.get("quantity", 1)) for x in food_lines)
    container_count = int(qty_portions * float(settings.pricing_containers_per_main_unit))
    unit = _container_unit_price(order_type)

    if container_count > 0:
        c_total = container_count * unit
        if order_type == "hall":
            container_iiko = (
                settings.iiko_product_id_container_hall.strip()
                or settings.iiko_product_id_container.strip()
                or None
            )
            container_label = "Контейнер (зал)"
        else:
            container_iiko = (
                settings.iiko_product_id_container_delivery_pickup.strip()
                or settings.iiko_product_id_container.strip()
                or None
            )
            container_label = "Контейнер (доставка/самовывоз)"
        fee_lines.append({
            "kind": "container",
            "name": container_label,
            "quantity": container_count,
            "unit_price": unit,
            "item_total": c_total,
            "iiko_id": container_iiko,
        })
        extras_total += c_total

    if order_type == "delivery" and foods_subtotal < float(settings.pricing_delivery_free_threshold):
        d_fee = float(settings.pricing_delivery_fee)
        fee_lines.append({
            "kind": "delivery",
            "name": "Доставка",
            "quantity": 1,
            "unit_price": d_fee,
            "item_total": d_fee,
            "iiko_id": settings.iiko_product_id_delivery.strip() or None,
        })
        extras_total += d_fee

    return fee_lines, extras_total


def build_order_items_json(
    validated: ValidatedOrder,
    ai: AIBrainResponse,
) -> tuple[dict[str, object], float]:
    """
    Собирает items_json для БД: блюда, наценки, метаданные заказа, итоговая сумма.
    """
    foods = validated.valid_items
    foods_subtotal = float(validated.total_price)
    fee_lines, extras = compute_fee_lines(foods, foods_subtotal, ai.order_type)
    grand_total = foods_subtotal + extras

    order_meta = {
        "order_type": ai.order_type,
        "payment_method": ai.payment_method,
        "is_preorder": ai.is_preorder,
        "booking_time": ai.booking_time,
        "delivery_address": (ai.delivery_address or "").strip(),
        "pickup_time_note": (ai.pickup_time_note or "").strip(),
    }
    if ai.booking_details:
        order_meta["booking_snapshot"] = {
            "date": ai.booking_details.date,
            "time": ai.booking_details.time,
            "guests": ai.booking_details.guests,
            "hall": ai.booking_details.hall,
        }

    payload: dict[str, object] = {
        "items": foods,
        "fee_lines": fee_lines,
        "foods_subtotal": foods_subtotal,
        "order_meta": order_meta,
    }
    return payload, grand_total


def summary_without_food_total_line(summary_text: str) -> str:
    """Убирает строку «Итого» только по блюдам — полный итог будет с контейнером/доставкой."""
    if "\n\n💰 Итого:" in summary_text:
        return summary_text.rsplit("\n\n💰 Итого:", 1)[0]
    return summary_text


def format_order_confirmation_summary(
    items_json: dict[str, object],
    validated_summary: str,
) -> str:
    """Текст для клиента: блюда + строки наценок + мета (тип, оплата)."""
    meta = items_json.get("order_meta") if isinstance(items_json, dict) else None
    fee_lines = items_json.get("fee_lines") if isinstance(items_json, dict) else []
    total = items_json.get("total_price")

    lines: list[str] = [summary_without_food_total_line(validated_summary).rstrip()]

    if isinstance(fee_lines, list) and fee_lines:
        lines.append("")
        lines.append("📦 Дополнительно:")
        for fl in fee_lines:
            if not isinstance(fl, dict):
                continue
            name = fl.get("name", "—")
            ft = float(fl.get("item_total", 0))
            q = fl.get("quantity", 1)
            lines.append(f"  • {name} × {q} — {ft:.0f} ₸")

    if total is not None:
        lines.append("")
        lines.append(f"💰 Итого к оплате: {float(total):.0f} ₸")
    else:
        lines.append("")
        lines.append("💰 Итого см. выше.")

    if isinstance(meta, dict):
        ot = meta.get("order_type", "delivery")
        pm = meta.get("payment_method", "cash")
        type_ru = {"delivery": "Доставка", "pickup": "Самовывоз", "hall": "В зале"}.get(ot, ot)
        pay_ru = {"cash": "Наличные", "card": "Карта при получении", "remote": "Удалённая оплата"}.get(pm, pm)
        lines.append("")
        lines.append(f"🚚 Получение: {type_ru}")
        if ot == "delivery" and meta.get("delivery_address"):
            lines.append(f"📍 Адрес: {meta['delivery_address']}")
        if ot == "pickup" and meta.get("pickup_time_note"):
            lines.append(f"🕐 Время: {meta['pickup_time_note']}")
        if meta.get("booking_time"):
            lines.append(f"🕐 Время визита/получения: {meta['booking_time']}")
        snap = meta.get("booking_snapshot")
        if isinstance(snap, dict) and snap:
            d_ = snap.get("date", "")
            t_ = snap.get("time", "")
            g_ = snap.get("guests", "")
            h_ = snap.get("hall", "")
            lines.append(f"📅 Бронь: {d_} в {t_}, гостей: {g_}, зал: {h_}")
        lines.append(f"💳 Оплата: {pay_ru}")

    return "\n".join(lines)


def merge_total_into_items_json(items_json: dict[str, object], total_price: float) -> dict[str, object]:
    """Дублирует итог в JSON для отображения в админке."""
    out = dict(items_json)
    out["total_price"] = total_price
    return out


def finalize_order_draft(
    validated: ValidatedOrder,
    ai: AIBrainResponse,
) -> tuple[dict[str, object], float]:
    """
    Финальный ``items_json`` и сумма по ТЗ: блюда + контейнеры + доставка (см. ``compute_fee_lines``).
    Обёртка над ``build_order_items_json`` и ``merge_total_into_items_json``.
    """
    payload, grand_total = build_order_items_json(validated, ai)
    merged = merge_total_into_items_json(payload, grand_total)
    return merged, grand_total


def build_demo_order_payload(
    food_lines: list[dict],
    order_type: str = "delivery",
    payment_method: str = "cash",
    *,
    delivery_address: str = "",
    pickup_time_note: str = "",
    is_preorder: bool = False,
    booking_time: str | None = None,
) -> tuple[dict[str, object], float]:
    """
    Сборка items_json для демо и seed.py: те же поля, что у боевого заказа после тарифов (v2).
    """
    foods_subtotal = round(sum(float(x.get("item_total", 0)) for x in food_lines), 2)
    fee_lines, extras = compute_fee_lines(food_lines, foods_subtotal, order_type)
    grand_total = round(foods_subtotal + extras, 2)
    order_meta = {
        "order_type": order_type,
        "payment_method": payment_method,
        "is_preorder": is_preorder,
        "booking_time": booking_time,
        "delivery_address": delivery_address.strip(),
        "pickup_time_note": pickup_time_note.strip(),
    }
    payload: dict[str, object] = {
        "items": food_lines,
        "fee_lines": fee_lines,
        "foods_subtotal": foods_subtotal,
        "order_meta": order_meta,
    }
    return merge_total_into_items_json(payload, grand_total), grand_total
