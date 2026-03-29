"""
Бизнес-логика заказов.
Валидация позиций по таблице MenuItem в БД.
MOCK_MENU используется как fallback, если таблица пуста.
Нечёткий поиск (fuzzy matching) через difflib.
"""

import logging
import re
from dataclasses import dataclass
from difflib import get_close_matches
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.data.plovxana_menu import build_mock_menu_dict
from app.db.models import MenuItem
from app.schemas.ai_schemas import AIBrainResponse, OrderItem

logger = logging.getLogger(__name__)

PaymentMethodKey = Literal["cash", "card", "remote"]

# Фразы для эвристики (мультиязычно: ru/kk/en/uz + латиница). Порядок проверок: remote → card → cash.
_PAYMENT_REMOTE_HINTS: tuple[str, ...] = (
    "удалён",
    "удален",
    "удалённ",
    "удаленн",
    "перевод",
    "переведу",
    "онлайн",
    "онлаин",
    "ссылк",
    "ссылка",
    "kaspi",
    "каспи",
    "kаспи",
    " payme",
    "payme",
    "пэйми",
    "пейми",
    "halyk",
    "халык",
    "haluk",
    " qr",
    "qr ",
    "qr-код",
    "qrcode",
    " link",
    "link ",
    "payment link",
    "remote",
    "online pay",
    "apple pay",
    "google pay",
    "wallet",
    "paypal",
    "stripe",
    "click",  # часто переводы в СНГ
    "bee pay",
    "beepay",
)

_PAYMENT_CARD_HINTS: tuple[str, ...] = (
    # Не использовать голое «карт» — ловит «картошка» и т.п.
    "картамен",
    "картпен",
    "картой",
    "картою",
    "картасы",
    "на карту",
    "по карте",
    " картой",
    " картам",
    "kartamen",
    "kartpen",
    "kartoy",
    "card",
    "cards",
    "терминал",
    "terminal",
    "pos",
    "безнал",
    "visa",
    "master",
    "maestro",
    "мир ",
    " debit",
    "debit ",
    "credit card",
    "tap to pay",
    "contactless",
    "банк карт",
)

_PAYMENT_CASH_HINTS: tuple[str, ...] = (
    "налич",
    "cash",
    "налом",
    " кэш",
    "кэш ",
    "by cash",
    "with cash",
    "pay cash",
    "naqd",
    "нақт",
    "накты",
    "naqd pul",
    "qolma",
    "қолма",
    "kolma",
    "nakit",
    "naqdda",
    "пулмен",  # каз.: наличными (контекст)
    "ақшамен",
    "akshemen",
)


def _normalize_payment_input(text: str) -> str:
    """Нижний регистр, апострофы, типографика; буквы (unicode) + цифры + пробел/дефис."""
    s = (text or "").strip().lower()
    for ch in ("'", "'", "`", "´", "ʼ", "ʻ"):
        s = s.replace(ch, "")
    s = s.replace("ё", "е")
    # \w в Python 3 — unicode-буквы; оставляем дефис для составных слов
    s = re.sub(r"[^\w\s\-]", " ", s, flags=re.UNICODE)
    return re.sub(r"\s+", " ", s).strip()


def detect_payment_method_from_text(text: str) -> PaymentMethodKey | None:
    """
    Распознаёт ответ клиента о способе оплаты (ключ для order_meta.payment_method).
    Эвристики по ключевым фрагментам; порядок важен (удалённая оплата — до «карты»).
    """
    t = _normalize_payment_input(text)
    if not t:
        return None

    if any(h in t for h in _PAYMENT_REMOTE_HINTS):
        return "remote"
    if any(h in t for h in _PAYMENT_CARD_HINTS):
        return "card"
    if any(h in t for h in _PAYMENT_CASH_HINTS) or t in ("нал", "cash", "naqd"):
        return "cash"
    return None


def build_summary_text_from_stored_items(items_json: dict[str, object]) -> str:
    """Текстовые строки позиций из сохранённого items_json (после смены оплаты и т.п.)."""
    items = items_json.get("items")
    if not isinstance(items, list) or not items:
        return ""
    lines: list[str] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        name = it.get("name", "—")
        q = it.get("quantity", 1)
        total = float(it.get("item_total", 0))
        lines.append(f"  • {name} × {q} — {total:.0f} ₸")
    return "\n".join(lines)


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
    """Запись справочника меню: цена + iiko UUID + категория (для тарифов упаковки)."""

    price: float
    iiko_id: str | None
    category: str = ""


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
            name: MenuEntry(price=price, iiko_id=None, category="")
            for name, price in MOCK_MENU.items()
        }

    lookup: dict[str, MenuEntry] = {}
    for mi in db_items:
        lookup[mi.name.lower().strip()] = MenuEntry(
            price=float(mi.price), iiko_id=mi.iiko_id, category=(mi.category or ""),
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
                "category": entry.category,
                "packaging_plov_1kg": (item.packaging_plov_1kg or "").strip(),
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


def _norm_txt(s: str) -> str:
    t = (s or "").lower().replace("ё", "е").strip()
    return " ".join(t.split())


PackagingKind = Literal["manty", "plov_half", "plov_1kg", "none"]


def classify_packaging_kind(name: str, category: str) -> PackagingKind:
    """
    Классификация строки заказа для тарифов упаковки (манты / плов 0.5 / плов 1кг).
    Узнаёт по названию и категории из меню.
    """
    n = _norm_txt(name).replace(" ", "")
    c_raw = _norm_txt(category)
    c = c_raw.replace(" ", "")
    if "мант" in name.lower() or "мант" in c_raw:
        return "manty"
    if "плов" in name.lower() or "плов" in c_raw:
        if "0,5" in n or "0.5" in name.lower() or "500г" in n or "500г" in c or "полкг" in n:
            return "plov_half"
        if (
            "1кг" in n
            or "1кг" in c
            or "1 кг" in name.lower()
            or "1000г" in n
            or "1000г" in c
        ):
            return "plov_1kg"
        if "0,5" in c or "0.5" in c_raw:
            return "plov_half"
        if "1кг" in c or "1 kg" in c_raw:
            return "plov_1kg"
    return "none"


def compute_fee_lines(
    food_lines: list[dict],
    foods_subtotal: float,
    order_type: str,
) -> tuple[list[dict], float]:
    """
    Упаковка по спец. правилам (манты / плов 0.5 / плов 1кг) + доставка при необходимости.
    Плов 1кг: в строке должно быть packaging_plov_1kg tabak|foil_kazan (валидация раньше в intent_router).
    """
    fee_lines: list[dict] = []
    extras_total = 0.0

    for line in food_lines:
        if not isinstance(line, dict):
            continue
        qty = int(line.get("quantity", 1))
        if qty < 1:
            qty = 1
        kind = classify_packaging_kind(str(line.get("name", "")), str(line.get("category", "")))
        if kind == "manty":
            unit = float(settings.packaging_manty_unit_price)
            total = unit * qty
            fee_lines.append({
                "kind": "packaging_manty",
                "name": "Контейнер для мант",
                "quantity": qty,
                "unit_price": unit,
                "item_total": total,
                "iiko_id": settings.iiko_product_id_packaging_manty.strip() or None,
            })
            extras_total += total
        elif kind == "plov_half":
            unit = float(settings.packaging_plov_half_unit_price)
            total = unit * qty
            fee_lines.append({
                "kind": "packaging_plov_half",
                "name": "Контейнер средний (плов 0.5)",
                "quantity": qty,
                "unit_price": unit,
                "item_total": total,
                "iiko_id": settings.iiko_product_id_packaging_plov_half.strip() or None,
            })
            extras_total += total
        elif kind == "plov_1kg":
            choice = (line.get("packaging_plov_1kg") or "").strip()
            if choice == "tabak":
                unit = float(settings.packaging_plov_1kg_tabak_unit_price)
                label = "Контейнер-табак (плов 1 кг)"
                iiko_pid = settings.iiko_product_id_packaging_plov_tabak.strip() or None
                fk: str = "packaging_plov_1kg_tabak"
            elif choice == "foil_kazan":
                unit = float(settings.packaging_plov_1kg_foil_unit_price)
                label = "Фольгированный казан (плов 1 кг)"
                iiko_pid = settings.iiko_product_id_packaging_plov_foil.strip() or None
                fk = "packaging_plov_1kg_foil"
            else:
                continue
            total = unit * qty
            fee_lines.append({
                "kind": fk,
                "name": label,
                "quantity": qty,
                "unit_price": unit,
                "item_total": total,
                "iiko_id": iiko_pid,
            })
            extras_total += total

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


def validate_mixed_payment_total(ai: AIBrainResponse, grand_total: float, *, tol: float = 1.0) -> str | None:
    """При payment_mode=mixed сумма cash+card+remote должна совпадать с итогом заказа."""
    if ai.payment_mode != "mixed":
        return None
    ps = ai.payment_split
    s = float(ps.cash) + float(ps.card) + float(ps.remote)
    if s <= 0:
        return "Укажите ненулевые суммы в payment_split (cash / card / remote)."
    if abs(s - grand_total) > tol:
        return (
            f"Сумма частей оплаты ({s:.0f} ₸) не совпадает с итогом заказа ({grand_total:.0f} ₸)."
        )
    return None


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

    requires_order_prepayment = grand_total >= float(settings.order_prepayment_threshold_kzt)

    if ai.payment_mode == "mixed":
        pay_details: dict[str, object] = {
            "type": "mixed",
            "split": {
                "cash": float(ai.payment_split.cash),
                "card": float(ai.payment_split.card),
                "remote": float(ai.payment_split.remote),
            },
        }
    else:
        pay_details = {"type": "single", "method": ai.payment_method}

    order_meta = {
        "order_type": ai.order_type,
        "payment_method": ai.payment_method,
        "payment_mode": ai.payment_mode,
        "is_preorder": ai.is_preorder,
        "booking_time": ai.booking_time,
        "delivery_address": (ai.delivery_address or "").strip(),
        "pickup_time_note": (ai.pickup_time_note or "").strip(),
        "payment_details": pay_details,
        "requires_order_prepayment": requires_order_prepayment,
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
        pd = meta.get("payment_details")
        if isinstance(pd, dict) and pd.get("type") == "mixed":
            sp = pd.get("split")
            if isinstance(sp, dict):
                lines.append("")
                lines.append("💳 Смешанная оплата:")
                if float(sp.get("remote") or 0) > 0:
                    lines.append(f"  • Удалённо: {float(sp['remote']):.0f} ₸")
                if float(sp.get("card") or 0) > 0:
                    lines.append(f"  • Карта при получении: {float(sp['card']):.0f} ₸")
                if float(sp.get("cash") or 0) > 0:
                    lines.append(f"  • Наличными: {float(sp['cash']):.0f} ₸")
        else:
            lines.append(f"💳 Оплата: {pay_ru}")
        if meta.get("requires_order_prepayment"):
            lines.append("")
            lines.append(
                f"⚠️ Заказ от **{int(settings.order_prepayment_threshold_kzt):,}** ₸ — нужна предоплата; "
                "подтверждение возможно после оплаты (оператор пришлёт реквизиты/ссылку)."
            )

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
