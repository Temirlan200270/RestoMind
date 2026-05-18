"""
Бизнес-логика заказов.
Валидация позиций по таблице MenuItem в БД.
MOCK_MENU используется как fallback, если таблица пуста.
Нечёткий поиск (fuzzy matching) через difflib.
"""

import hashlib
import logging
import re
import time
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from difflib import SequenceMatcher, get_close_matches
from typing import Any, Literal

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.services.restaurant_context_cache import redis_cached_menu_context_string
from app.data.plovxana_menu import build_mock_menu_dict
from app.db.models import MenuItem, Order, OrderStatus, Organization, PackagingRule
from app.services.menu_embeddings import embed_query_vector, top_k_menu_items_by_similarity
from app.schemas.ai_schemas import AIBrainResponse, OrderAction, OrderItem

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
    # Нечёткое сопоставление с меню: similarity = SequenceMatcher по нижнему регистру (0..1).
    fuzzy_match_details: list[dict[str, Any]] = field(default_factory=list)
    # Позиции, которые есть в меню, но сейчас на стопе (is_available=False).
    stoplist_items: list[str] = field(default_factory=list)


@dataclass
class MenuEntry:
    """Запись справочника меню: цена + iiko UUID + категория (для тарифов упаковки)."""

    price: float
    iiko_id: str | None
    category: str = ""
    is_available: bool = True


async def load_available_menu(
    db: AsyncSession,
    *,
    organization_id: int,
    include_unavailable: bool = False,
) -> list[MenuItem]:
    """
    Один запрос на весь цикл обработки.
    include_unavailable=True — загружает также позиции на стопе (is_available=False),
    чтобы ИИ знал об их существовании и мог корректно отвечать гостям.
    organization_id обязателен: без скоупа возможна утечка меню между филиалами.
    """
    org_id = int(organization_id)
    where_clauses = [MenuItem.organization_id == org_id]
    if not include_unavailable:
        where_clauses.append(MenuItem.is_available.is_(True))
    stmt = (
        select(MenuItem)
        .where(*where_clauses)
        .order_by(MenuItem.category, MenuItem.name)
    )
    result = await db.execute(stmt)
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
            price=float(mi.price),
            iiko_id=mi.iiko_id,
            category=(mi.category or ""),
            is_available=bool(getattr(mi, "is_available", True)),
        )
    return lookup


def merge_confidence_into_order_meta(
    order_meta: dict[str, object],
    validated: ValidatedOrder,
) -> None:
    """
    P1.5: флаги доверия к автозаполнению заказа (админка, канбан).

    - fuzzy_menu_match: нечёткое совпадение с меню с similarity < 0.8 (difflib.SequenceMatcher).
    - unverified_delivery_address: доставка с непустым адресом, пока нет order_meta.delivery_address_verified.
    """
    reasons: list[str] = []
    details: dict[str, object] = {}

    low_fuzzy = [
        x for x in validated.fuzzy_match_details
        if float(x.get("similarity") or 0) < 0.8
    ]
    if low_fuzzy:
        reasons.append("fuzzy_menu_match")
        details["fuzzy_matches"] = low_fuzzy

    ot = str(order_meta.get("order_type") or "")
    addr = str(order_meta.get("delivery_address") or "").strip()
    if ot == "delivery" and addr and order_meta.get("delivery_address_verified") is not True:
        reasons.append("unverified_delivery_address")

    order_meta["confidence"] = {
        "low_confidence": bool(reasons),
        "reasons": reasons,
        "details": details,
    }


async def validate_order(
    items: list[OrderItem],
    menu_items: list[MenuItem] | None = None,
    db: AsyncSession | None = None,
    *,
    organization_id: int | None = None,
) -> ValidatedOrder:
    """
    Проверяет каждую позицию заказа по таблице MenuItem в БД.
    Принимает готовый список menu_items (чтобы не дублировать запрос)
    или db-сессию для ленивой загрузки. Fallback на MOCK_MENU.
    """
    if menu_items is None and db is not None:
        if organization_id is None:
            raise ValueError("organization_id обязателен при загрузке меню из БД")
        menu_items = await load_available_menu(db, organization_id=int(organization_id))
    menu_lookup = _build_menu_lookup(menu_items or [])
    menu_names = list(menu_lookup.keys())

    valid_items: list[dict] = []
    unknown_items: list[str] = []
    stoplist_items: list[str] = []
    fuzzy_matched: list[str] = []
    fuzzy_match_details: list[dict[str, Any]] = []
    total_price = 0.0

    for item in items:
        name_lower = item.name.lower().strip()
        entry = menu_lookup.get(name_lower)

        # Нечёткий поиск, если точного совпадения нет
        if entry is None and menu_names:
            matches = get_close_matches(name_lower, menu_names, n=1, cutoff=0.6)
            if matches:
                matched_name = matches[0]
                similarity = SequenceMatcher(None, name_lower, matched_name).ratio()
                entry = menu_lookup[matched_name]
                fuzzy_matched.append(f"{item.name} → {matched_name}")
                fuzzy_match_details.append({
                    "source_name": item.name,
                    "matched_menu_name": matched_name,
                    "similarity": round(float(similarity), 4),
                })
                logger.info("Fuzzy match: '%s' → '%s'", name_lower, matched_name)

        if entry is not None:
            if not entry.is_available:
                # Позиция есть в меню, но временно на стопе — нельзя добавить в заказ
                stoplist_items.append(item.name)
                logger.info("Стоп-лист: '%s' — не добавлена в заказ", item.name)
            else:
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
                    "modifiers_ids": list(item.modifiers_ids or []),
                    "modifiers": list(item.modifiers or []),
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
        logger.info("Fuzzy matched items (hidden from customer): %s", "; ".join(fuzzy_matched))
    if stoplist_items:
        summary += f"\n\n🚫 Временно недоступно (стоп): {', '.join(stoplist_items)}"
    if unknown_items:
        summary += f"\n\n⚠️ Не нашёл в меню: {', '.join(unknown_items)}"
    summary += f"\n\n💰 Итого: {total_price:.0f} ₸"

    logger.info(
        "Валидация заказа: %d найдено, %d стоп, %d не найдено, итого %.2f ₸",
        len(valid_items), len(stoplist_items), len(unknown_items), total_price,
    )

    return ValidatedOrder(
        valid_items=valid_items,
        unknown_items=unknown_items,
        stoplist_items=stoplist_items,
        total_price=total_price,
        summary_text=summary,
        fuzzy_match_details=fuzzy_match_details,
    )


def looks_like_iiko_uuid(value: str) -> bool:
    """True, если строка похожа на UUID из контекста меню [id: …]."""
    s = (value or "").strip()
    if len(s) < 32:
        return False
    try:
        uuid.UUID(s)
    except ValueError:
        return False
    return True


def _internal_merge_key_from_food_line(line: dict) -> str:
    """Стабильный ключ строки еды в черновике (iiko_id приоритетнее имени)."""
    iid = str(line.get("iiko_id") or "").strip()
    if iid:
        return f"i:{iid.lower()}"
    return f"n:{_norm_txt(str(line.get('name', '')))}"


def _new_internal_key_from_item_id(item_id: str) -> str:
    tid = item_id.strip()
    if looks_like_iiko_uuid(tid):
        return f"i:{tid.lower()}"
    return f"n:{_norm_txt(tid)}"


def _resolve_merge_key(cart: dict[str, dict], item_id: str) -> str | None:
    """Сопоставляет item_id из ответа ИИ с ключом слияния."""
    tid = item_id.strip()
    if not tid:
        return None
    cand = _new_internal_key_from_item_id(tid)
    if cand in cart:
        return cand
    if looks_like_iiko_uuid(tid):
        u = tid.lower()
        for k, line in cart.items():
            if str(line.get("iiko_id") or "").strip().lower() == u:
                return k
    # Любой iiko_id из БД/меню (в т.ч. не-UUID в тестах и старых данных)
    tl = tid.lower()
    for k, line in cart.items():
        if str(line.get("iiko_id") or "").strip().lower() == tl:
            return k
    nu = _norm_txt(tid)
    for k, line in cart.items():
        if _norm_txt(str(line.get("name", ""))) == nu:
            return k
    return None


def _stub_food_line(item_id: str) -> dict:
    """
    Новая строка до валидации по меню: цены обновит validate_order.
    Для UUID имя пустое — заполнит enrich_merged_items_from_menu.
    """
    tid = item_id.strip()
    if looks_like_iiko_uuid(tid):
        return {
            "name": "",
            "quantity": 0,
            "price_per_unit": 0.0,
            "item_total": 0.0,
            "iiko_id": tid,
            "category": "",
            "packaging_plov_1kg": "",
            "exclude_ingredients": [],
        }
    return {
        "name": tid,
        "quantity": 0,
        "price_per_unit": 0.0,
        "item_total": 0.0,
        "iiko_id": None,
        "category": "",
        "packaging_plov_1kg": "",
        "exclude_ingredients": [],
    }


def merge_cart_actions(
    current_items: list[dict],
    actions: list[OrderAction],
) -> list[dict]:
    """
    Сливает сохранённые позиции еды (items_json['items']) с дельтами от ИИ.

    После вызова обязательно: enrich_merged_items_from_menu (если есть UUID без имени),
    draft_food_lines_to_order_items → validate_order → finalize_order_draft / build_order_items_json.
    """
    order_keys: list[str] = []
    cart: dict[str, dict] = {}

    for raw in current_items:
        if not isinstance(raw, dict):
            continue
        line = deepcopy(raw)
        k = _internal_merge_key_from_food_line(line)
        if k not in cart:
            order_keys.append(k)
            cart[k] = line
        else:
            cart[k]["quantity"] = int(cart[k].get("quantity", 1)) + int(line.get("quantity", 1))

    for act in actions:
        tid = act.item_id.strip()
        if not tid:
            continue
        target = _resolve_merge_key(cart, tid)

        if act.action == "add":
            if target is None:
                nk = _new_internal_key_from_item_id(tid)
                if nk not in cart:
                    order_keys.append(nk)
                    cart[nk] = _stub_food_line(tid)
                target = nk
            cart[target]["quantity"] = int(cart[target].get("quantity", 0)) + int(act.quantity)

        elif act.action == "remove":
            if target is None:
                continue
            prev_q = int(cart[target].get("quantity", 0))
            new_q = prev_q - int(act.quantity)
            if new_q <= 0:
                del cart[target]
                order_keys = [x for x in order_keys if x != target]
            else:
                cart[target]["quantity"] = new_q

        elif act.action == "set_quantity":
            qset = int(act.quantity)
            if target is None:
                if qset > 0:
                    nk = _new_internal_key_from_item_id(tid)
                    if nk not in cart:
                        order_keys.append(nk)
                        cart[nk] = _stub_food_line(tid)
                    cart[nk]["quantity"] = qset
                continue
            if qset <= 0:
                del cart[target]
                order_keys = [x for x in order_keys if x != target]
            else:
                cart[target]["quantity"] = qset

    return [deepcopy(cart[k]) for k in order_keys if k in cart]


APPLIED_ORDER_ACTION_IDS_KEY = "applied_order_action_ids"
_APPLIED_ORDER_ACTION_IDS_CAP = 200


def applied_order_action_ids_from_items_json(items_json: dict | None) -> set[str]:
    """Множество уже применённых к черновику id дельт (идемпотентность merge)."""
    if not items_json or not isinstance(items_json, dict):
        return set()
    meta = items_json.get("order_meta")
    if not isinstance(meta, dict):
        return set()
    raw = meta.get(APPLIED_ORDER_ACTION_IDS_KEY)
    if not isinstance(raw, list):
        return set()
    return {str(x).strip() for x in raw if str(x).strip()}


def compute_order_action_stable_id(
    act: OrderAction,
    idx: int,
    inbound_message_id: str,
) -> str:
    """Явный action_id от модели или детерминированный fingerprint (в т.ч. для одного WA message_id)."""
    explicit = (act.action_id or "").strip()
    if explicit:
        return explicit[:128]
    logger.debug(
        "order_action без action_id: fingerprint по message_id+дельта (iiko_id=%s action=%s)",
        (act.item_id or "")[:24],
        act.action,
    )
    mid = (inbound_message_id or "").strip()
    basis = f"{mid}|{idx}|{act.item_id}|{act.action}|{int(act.quantity)}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:32]


def filter_order_actions_idempotent(
    actions: list[OrderAction],
    *,
    applied: set[str],
    inbound_message_id: str,
) -> tuple[list[OrderAction], list[str]]:
    """
    Отбрасывает дельты, уже применённые к черновику, и дубликаты внутри одного ответа модели.
    Возвращает (действия для merge, новые id для записи в order_meta).
    """
    out: list[OrderAction] = []
    new_ids: list[str] = []
    seen_batch: set[str] = set()
    for idx, act in enumerate(actions):
        aid = compute_order_action_stable_id(act, idx, inbound_message_id)
        if aid in applied or aid in seen_batch:
            continue
        seen_batch.add(aid)
        out.append(act)
        new_ids.append(aid)
    return out, new_ids


def merge_applied_order_action_ids_into_items_json(
    items_json: dict[str, object],
    *,
    new_ids: list[str],
    reset: bool,
) -> dict[str, object]:
    """Дописывает applied_order_action_ids в order_meta или сбрасывает при полной пересборке items."""
    meta_raw = items_json.get("order_meta")
    meta = dict(meta_raw) if isinstance(meta_raw, dict) else {}
    if reset:
        merged: list[str] = []
    else:
        prev = meta.get(APPLIED_ORDER_ACTION_IDS_KEY)
        merged = [str(x).strip() for x in prev if str(x).strip()] if isinstance(prev, list) else []
    for aid in new_ids:
        s = str(aid).strip()
        if s and s not in merged:
            merged.append(s)
    if len(merged) > _APPLIED_ORDER_ACTION_IDS_CAP:
        merged = merged[-_APPLIED_ORDER_ACTION_IDS_CAP:]
    meta[APPLIED_ORDER_ACTION_IDS_KEY] = merged
    out = dict(items_json)
    out["order_meta"] = meta
    return out


# Синоним для документации / ТЗ (транзакционный merge — одна реализация)
apply_order_merge = merge_cart_actions


async def persist_draft_order_optimistic_update(
    db: AsyncSession,
    *,
    order_id: int,
    expected_row_version: int,
    items_json: dict[str, Any],
    total_price: float,
    prepayment_status: str,
    booking_id: int | None,
    organization_id: int,
) -> bool:
    """
    Один UPDATE черновика с оптимистичной блокировкой (version).
    Возвращает True, если ровно одна строка обновлена. Без commit — в той же транзакции, что и merge/fee.
    """
    res = await db.execute(
        update(Order)
        .where(
            Order.id == order_id,
            Order.status == OrderStatus.DRAFT.value,
            Order.row_version == expected_row_version,
        )
        .values(
            items_json=items_json,
            total_price=total_price,
            prepayment_status=prepayment_status,
            booking_id=booking_id,
            organization_id=organization_id,
            row_version=Order.row_version + 1,
        )
    )
    return int(res.rowcount or 0) == 1


def enrich_merged_items_from_menu(
    items: list[dict],
    menu_items: list[MenuItem] | None,
) -> list[dict]:
    """
    Подставляет name/category по iiko_id, если строка пришла только с UUID (после merge).
    """
    if not menu_items:
        return [deepcopy(x) if isinstance(x, dict) else x for x in items]
    by_iiko: dict[str, MenuItem] = {}
    for m in menu_items:
        iid = (m.iiko_id or "").strip()
        if iid:
            by_iiko[iid.lower()] = m
    out: list[dict] = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        d = deepcopy(raw)
        iid = str(d.get("iiko_id") or "").strip()
        nm = str(d.get("name") or "").strip()
        confused_name = bool(nm and looks_like_iiko_uuid(nm))
        if iid and (not nm or confused_name):
            mi = by_iiko.get(iid.lower())
            if mi:
                d["name"] = mi.name
                d["category"] = mi.category or ""
        out.append(d)
    return out


def draft_food_lines_to_order_items(items: list[dict]) -> list[OrderItem]:
    """Преобразует словари еды из items_json в OrderItem для validate_order."""
    result: list[OrderItem] = []
    for d in items:
        if not isinstance(d, dict):
            continue
        name = str(d.get("name") or "").strip()
        iiko = str(d.get("iiko_id") or "").strip()
        qty = int(d.get("quantity", 0))
        if qty < 1:
            continue
        if not name and not iiko:
            continue
        result.append(
            OrderItem(
                name=name or iiko,
                iiko_item_id=iiko,
                quantity=qty,
                packaging_plov_1kg=str(d.get("packaging_plov_1kg") or "").strip(),
                exclude_ingredients=list(d.get("exclude_ingredients") or []),
            )
        )
    return result


def format_draft_order_context_for_prompt(items_json: dict | None) -> str:
    """
    Текст для system instruction: актуальный DRAFT из БД (Phase 18 — state injection для LLM).
    Совпадает с конвенцией меню `[id: …]` — чтобы модель заполняла `order_actions.item_id` тем же UUID.
    Пустая строка, если черновика нет или позиций нет.
    """
    if not items_json or not isinstance(items_json, dict):
        return ""
    items = items_json.get("items")
    if not isinstance(items, list) or not items:
        return ""
    lines: list[str] = [
        "## Текущий черновик заказа (уже в системе)",
        "Ниже — фактический состав из базы. Не выдумывай позиции: опирайся на этот список при ответах и при `order_actions`.",
        "Для изменений используй `order_actions` (add / remove / set_quantity); в `item_id` указывай **тот же** `[id: …]`, что в строке, или узнаваемое имя из меню.",
        "Если клиент просит убрать то, чего нет в списке — вежливо уточни состав.",
        "",
    ]
    for it in items:
        if not isinstance(it, dict):
            continue
        name = it.get("name") or "—"
        q = it.get("quantity", 1)
        pk = (it.get("packaging_plov_1kg") or "").strip()
        extra = f", упаковка 1кг: {pk}" if pk else ""
        cat = str(it.get("category") or "").strip()
        cat_bit = f" ({cat})" if cat else ""
        iiko = str(it.get("iiko_id") or "").strip()
        id_bit = f" [id: {iiko}]" if iiko else ""
        excl = it.get("exclude_ingredients")
        if isinstance(excl, list) and excl:
            extra += f", без: {', '.join(str(x) for x in excl)}"
        lines.append(f"- {name}{cat_bit} × {q}{id_bit}{extra}")
    meta = items_json.get("order_meta") if isinstance(items_json.get("order_meta"), dict) else {}
    bits: list[str] = []
    ot = meta.get("order_type")
    if ot:
        bits.append(f"получение: {ot}")
    if meta.get("delivery_address"):
        bits.append(f"адрес: {meta['delivery_address']}")
    if meta.get("pickup_time_note"):
        bits.append(f"самовывоз: {meta['pickup_time_note']}")
    if bits:
        lines.extend(["", "; ".join(bits)])
    fs = items_json.get("foods_subtotal")
    if fs is not None:
        try:
            lines.append(f"\nСумма по блюдам (последний расчёт сервера): **{float(fs):.0f} ₸**.")
        except (TypeError, ValueError):
            pass
    fee_lines = items_json.get("fee_lines")
    if isinstance(fee_lines, list) and fee_lines:
        lines.append("")
        lines.append(
            "Строки доп. сборов (контейнеры, доставка — **факт** последнего пересчёта; "
            "при правках через `order_actions` бэкенд пересчитает заново, не копируй эти суммы как «истину» в JSON модели).",
        )
        for fl in fee_lines:
            if not isinstance(fl, dict):
                continue
            nm = str(fl.get("name") or fl.get("kind") or "—")
            q = fl.get("quantity", 1)
            try:
                it = float(fl.get("item_total") or 0)
            except (TypeError, ValueError):
                it = 0.0
            lines.append(f"- {nm} × {q} — {it:.0f} ₸")
    total = items_json.get("total_price")
    if total is not None:
        try:
            lines.append(f"\n**Итого к оплате (последний расчёт): {float(total):.0f} ₸** — после изменений состава пересчитает сервер (`compute_fee_lines`).")
        except (TypeError, ValueError):
            pass
    return "\n".join(lines)


async def calculate_total_and_fees(
    food_items: list[dict],
    ai: AIBrainResponse,
    *,
    menu_items: list[MenuItem] | None = None,
    db: AsyncSession | None = None,
    packaging_rules: list[PackagingRule] | None = None,
    organization_id: int | None = None,
    prepayment_enforced: bool | None = None,
) -> tuple[ValidatedOrder, dict[str, object], float]:
    """
    Единая точка расчёта: цены и номенклатура из БД, упаковка и доставка через compute_fee_lines.
    """
    if packaging_rules is None and db is not None:
        oid = int(organization_id if organization_id is not None else settings.default_organization_id)
        packaging_rules = await load_packaging_rules(db, oid)
    enriched = enrich_merged_items_from_menu(food_items, menu_items)
    order_items = draft_food_lines_to_order_items(enriched)
    if menu_items is None and db is not None and organization_id is None:
        raise ValueError("organization_id is required when menu_items is not provided")
    validated = await validate_order(
        order_items, menu_items=menu_items, db=db, organization_id=organization_id,
    )
    pe = True
    if prepayment_enforced is not None:
        pe = bool(prepayment_enforced)
    elif db is not None and organization_id is not None:
        org = await db.get(Organization, int(organization_id))
        pe = bool(org.prepayment_enforced) if org else True
    payload, grand_total = finalize_order_draft(
        validated, ai, packaging_rules=packaging_rules, prepayment_enforced=pe,
    )
    return validated, payload, grand_total


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
        stop_tag = " [СТОП — временно недоступно, нельзя добавить в заказ]" if not item.is_available else ""
        tags_s = (getattr(item, "tags", None) or "").strip()
        tags_part = f" — теги: {tags_s}" if tags_s else ""
        pk = (getattr(item, "portion_kind", None) or "single").strip().lower()
        portion_lbl = "на компанию" if pk == "shareable" else "порция"
        smin = int(getattr(item, "serves_min", None) or 1)
        smax = int(getattr(item, "serves_max", None) or smin)
        serve_part = ""
        if smax > 1 or smin > 1:
            serve_part = f" — гостей: {smin}–{smax}" if smin != smax else f" — гостей: ~{smin}"
        al = (getattr(item, "allergens", None) or "").strip()
        al_part = f" — аллергены: {al}" if al else ""
        ing = (getattr(item, "ingredients_summary", None) or "").strip()
        ing_part = f" — состав: {ing[:120]}" if ing else ""
        dt = (getattr(item, "dietary_tags", None) or "").strip()
        dt_part = f" — диета: {dt}" if dt else ""
        lines.append(
            f"- {item.name}: {float(item.price):.0f} ₸{iiko_tag}{stop_tag}{tags_part} "
            f"({portion_lbl}{serve_part}{al_part}{ing_part}{dt_part})"
        )

    return "\n".join(lines)


# org_id или "org_id:category_hint" → (context_str, timestamp)
_menu_ctx_cache: dict[str, tuple[str, float]] = {}
_MENU_CTX_TTL = 90.0  # seconds

# Категории, которые всегда включаются в полный контекст при smart filter (upsell-кандидаты)
_SMART_UPSELL_CAT_HINTS = ("напит", "кофе", "чай", "бар", "сок", "десерт", "выпечк", "соус")


def menu_stoplist_fingerprint(menu_items: list[MenuItem]) -> str:
    """
    Короткий отпечаток стоп-листа: ключ кэша промпта меню инвалидируется при смене is_available.
    """
    stopped = sorted(
        (m.iiko_id or str(m.id) or m.name)
        for m in menu_items
        if not m.is_available
    )
    avail = sum(1 for m in menu_items if m.is_available)
    blob = f"a{avail}:s{'|'.join(stopped[:250])}"
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _menu_cache_key(
    org_id: int,
    category_hint: str | None,
    stoplist_fp: str | None = None,
) -> str:
    base = str(org_id) if not category_hint else f"{org_id}:{category_hint.lower()}"
    if stoplist_fp:
        return f"{base}:sl{stoplist_fp}"
    return base


def invalidate_menu_context_cache(organization_id: int) -> None:
    prefix = str(organization_id)
    keys_to_remove = [k for k in list(_menu_ctx_cache) if k == prefix or k.startswith(f"{prefix}:")]
    for k in keys_to_remove:
        _menu_ctx_cache.pop(k, None)
    try:
        import asyncio

        from app.services.restaurant_context_cache import redis_bump_menu_ctx_cache_version

        loop = asyncio.get_running_loop()
        loop.create_task(redis_bump_menu_ctx_cache_version(int(organization_id)))
    except RuntimeError:
        pass


def detect_category_hint(message: str, menu_items: list[MenuItem]) -> str | None:
    """По тексту сообщения определяет, какую категорию меню смотрит гость (string-match, без LLM)."""
    msg_lower = (message or "").lower()
    if not msg_lower:
        return None
    scores: dict[str, int] = {}
    for item in menu_items:
        cat = (item.category or "").strip()
        if not cat:
            continue
        cat_lower = cat.lower()
        if cat_lower in msg_lower or item.name.lower() in msg_lower:
            scores[cat] = scores.get(cat, 0) + 1
    if not scores:
        return None
    return max(scores, key=lambda k: scores[k])


def build_menu_context_filtered(db_items: list[MenuItem], category_hint: str) -> str:
    """
    Smart Category Filter: полный контекст для найденной категории + upsell-позиций,
    компактный (только name + price) для всего остального.
    """
    hint_lower = category_hint.lower()
    full_items: list[MenuItem] = []
    compact_items: list[MenuItem] = []

    for item in db_items:
        cat_lower = (item.category or "").lower()
        tags_lower = (getattr(item, "tags", None) or "").lower()
        is_hint_cat = hint_lower in cat_lower or cat_lower in hint_lower
        is_upsell = (
            "upsell" in tags_lower
            or any(h in cat_lower for h in _SMART_UPSELL_CAT_HINTS)
        )
        # Стоп-позиции всегда в full — ИИ должен знать о них
        if is_hint_cat or is_upsell or not item.is_available:
            full_items.append(item)
        else:
            compact_items.append(item)

    result = build_menu_context(full_items) if full_items else ""

    if compact_items:
        current_cat = ""
        compact_lines: list[str] = []
        for item in compact_items:
            if item.category != current_cat:
                current_cat = item.category
                compact_lines.append(f"\n## {current_cat}")
            stop_tag = " [СТОП]" if not item.is_available else ""
            compact_lines.append(f"- {item.name}: {float(item.price):.0f} ₸{stop_tag}")
        compact_str = "\n".join(compact_lines)
        result = (
            f"{result}\n\n"
            f"[Остальные позиции — компактно; детали запроси по названию]\n{compact_str}"
        )

    if not result.strip():
        return build_menu_context(db_items)

    total = len(db_items)
    header = (
        f"[Smart filter: подробно {len(full_items)} из {total} позиций "
        f"по теме «{category_hint}»]\n"
    )
    return header + result


async def build_menu_context_for_ai(menu_items: list[MenuItem], user_query: str) -> str:
    """
    Текст меню для промпта. Два режима:
    - Smart Category Filter (default): при >= menu_smart_filter_min_items позиций и
      найденной категории в user_query — полный контекст только для неё + upsell, компакт для остальных.
    - Полный каталог: кэшируется по org_id через Redis + in-memory 90 с.
    """
    if not settings.menu_rag_enabled:
        org_id: int | None = menu_items[0].organization_id if menu_items else None
        stoplist_fp = menu_stoplist_fingerprint(menu_items) if menu_items else "empty"

        # Smart Category Filter
        category_hint: str | None = None
        if (
            settings.menu_smart_filter_enabled
            and len(menu_items) >= settings.menu_smart_filter_min_items
        ):
            category_hint = detect_category_hint(user_query, menu_items)
            if category_hint:
                logger.debug(
                    "menu smart filter: category_hint=%r, items %d/%d",
                    category_hint, 0, len(menu_items),
                )

        cache_k = (
            _menu_cache_key(org_id, category_hint, stoplist_fp)
            if org_id is not None
            else None
        )

        if cache_k is not None:
            cached = _menu_ctx_cache.get(cache_k)
            if cached and (time.monotonic() - cached[1]) < _MENU_CTX_TTL:
                return cached[0]

        if category_hint:
            ctx = build_menu_context_filtered(menu_items, category_hint)
            if cache_k is not None:
                _menu_ctx_cache[cache_k] = (ctx, time.monotonic())
            return ctx

        async def _materialize_full_menu() -> str:
            return build_menu_context(menu_items)

        if org_id is not None:
            ctx = await redis_cached_menu_context_string(
                org_id, _materialize_full_menu, cache_suffix=stoplist_fp,
            )
            if cache_k is not None:
                _menu_ctx_cache[cache_k] = (ctx, time.monotonic())
            return ctx
        return build_menu_context(menu_items)
    if len(menu_items) < int(settings.menu_rag_min_items):
        return build_menu_context(menu_items)
    q = (user_query or "").strip()
    if not q:
        return build_menu_context(menu_items)

    total = len(menu_items)
    with_emb = sum(1 for m in menu_items if m.embedding)
    if with_emb < total:
        logger.info(
            "menu RAG: не все позиции проиндексированы (%s/%s) — полный каталог",
            with_emb,
            total,
        )
        return build_menu_context(menu_items)

    qvec = await embed_query_vector(q)
    if not qvec:
        logger.warning("menu RAG: не удалось получить эмбеддинг запроса — полный каталог")
        return build_menu_context(menu_items)

    top_k = int(settings.menu_rag_top_k)
    subset = top_k_menu_items_by_similarity(menu_items, qvec, top_k=top_k)
    if not subset:
        return build_menu_context(menu_items)

    header = (
        f"[Фрагмент меню: по смыслу отобрано {len(subset)} из {total} позиций под текущую реплику гостя]\n"
    )
    return header + build_menu_context(subset)


def _norm_txt(s: str) -> str:
    t = (s or "").lower().replace("ё", "е").strip()
    return " ".join(t.split())


PACKAGING_SEED: list[dict[str, object]] = [
    {"kind": "manty", "name": "Контейнер для мант", "price": 200, "keywords": "мант", "sort_order": 100},
    {"kind": "shashlik", "name": "Контейнер для шашлыка", "price": 200, "keywords": "шашлык,шашлик", "sort_order": 100},
    {"kind": "plov_half", "name": "Контейнер средний (плов 0.5)", "price": 300, "keywords": "плов+0.5,плов+0,5,плов+500г,плов+полкг", "sort_order": 95},
    {"kind": "plov_1kg_tabak", "name": "Табак (плов 1 кг)", "price": 800, "keywords": "плов+1кг,плов+1 кг,плов+1000г", "option_key": "tabak", "sort_order": 90},
    {"kind": "plov_1kg_foil", "name": "Фольгированный казан (плов 1 кг)", "price": 650, "keywords": "плов+1кг,плов+1 кг,плов+1000г", "option_key": "foil_kazan", "sort_order": 90},
    {"kind": "fries", "name": "Соусница (фри)", "price": 50, "keywords": "фри,fries", "sort_order": 80},
    {"kind": "standard", "name": "Контейнер (ланч-бокс)", "price": 150, "keywords": "", "sort_order": 0},
    {"kind": "delivery", "name": "Доставка", "price": 700, "keywords": "", "sort_order": 0},
]


async def load_packaging_rules(db: AsyncSession, organization_id: int) -> list[PackagingRule]:
    """Загрузить активные правила упаковки арендатора. При отсутствии строк — сиды для этой организации."""
    tenant = or_(
        PackagingRule.organization_id == organization_id,
        PackagingRule.organization_id.is_(None),
    )
    result = await db.execute(
        select(PackagingRule)
        .where(PackagingRule.is_active.is_(True), tenant)
        .order_by(PackagingRule.sort_order.desc(), PackagingRule.id)
    )
    rules = list(result.scalars().all())
    if rules:
        return rules
    for seed in PACKAGING_SEED:
        db.add(PackagingRule(**{**seed, "organization_id": int(organization_id)}))
    await db.flush()
    result2 = await db.execute(
        select(PackagingRule)
        .where(PackagingRule.is_active.is_(True), tenant)
        .order_by(PackagingRule.sort_order.desc(), PackagingRule.id)
    )
    return list(result2.scalars().all())


def _keywords_match(keywords_csv: str, item_name_lower: str, item_category_lower: str) -> bool:
    """
    Проверяет совпадение ключевых слов.
    Формат: 'мант,шашлык+говядина' → ('мант') ИЛИ ('шашлык' И 'говядина').
    """
    raw = (keywords_csv or "").strip()
    if not raw:
        return False
    text = item_name_lower + " " + item_category_lower
    for phrase in raw.split(","):
        phrase = phrase.strip()
        if not phrase:
            continue
        tokens = [t.strip() for t in phrase.split("+") if t.strip()]
        if tokens and all(tok in text for tok in tokens):
            return True
    return False


def match_packaging_rule(
    item_name: str,
    item_category: str,
    rules: list[PackagingRule],
    packaging_choice: str = "",
    scope: str = "item",
) -> PackagingRule | None:
    """
    Находит подходящее правило упаковки для позиции.
    Rules должны быть отсортированы по sort_order DESC (specific → default).
    """
    nl = _norm_txt(item_name)
    cl = _norm_txt(item_category)
    default_rule: PackagingRule | None = None

    for rule in rules:
        rule_scope = (getattr(rule, "scope", None) or "item").strip().lower()
        if rule_scope != scope:
            continue
        cat_match = (getattr(rule, "category_match", None) or "").strip()
        if scope == "category" and cat_match and cat_match.lower() not in item_category.lower():
            continue
        kw = (rule.keywords or "").strip()
        if not kw:
            if scope == "item" and rule.kind == "standard" and default_rule is None:
                default_rule = rule
            elif scope == "category" and default_rule is None:
                default_rule = rule
            continue
        if not _keywords_match(kw, nl, cl):
            continue
        opt = (rule.option_key or "").strip()
        if opt:
            if packaging_choice == opt:
                return rule
            continue
        return rule

    return default_rule


PackagingKind = Literal["manty", "shashlik", "plov_half", "plov_1kg", "fries", "standard"]


def classify_packaging_kind(name: str, category: str) -> PackagingKind:
    """
    Классификация строки заказа для тарифов упаковки.
    Приоритет: манты → шашлык → плов (0.5 / 1кг) → фри → стандарт.
    """
    n = _norm_txt(name).replace(" ", "")
    nl = name.lower()
    c_raw = _norm_txt(category)
    c = c_raw.replace(" ", "")
    if "мант" in nl or "мант" in c_raw:
        return "manty"
    if "шашлык" in nl or "шашлык" in c_raw or "шашлик" in nl or "шашлик" in c_raw:
        return "shashlik"
    if "плов" in nl or "плов" in c_raw:
        if "0,5" in n or "0.5" in nl or "500г" in n or "500г" in c or "полкг" in n:
            return "plov_half"
        if (
            "1кг" in n
            or "1кг" in c
            or "1 кг" in nl
            or "1000г" in n
            or "1000г" in c
        ):
            return "plov_1kg"
        if "0,5" in c or "0.5" in c_raw:
            return "plov_half"
        if "1кг" in c or "1 kg" in c_raw:
            return "plov_1kg"
    if "фри" in nl or "fries" in nl or "фри" in c_raw:
        return "fries"
    return "standard"


def compute_fee_lines(
    food_lines: list[dict],
    foods_subtotal: float,
    order_type: str,
    packaging_rules: list[PackagingRule] | None = None,
) -> tuple[list[dict], float]:
    """
    Упаковка + доставка.
    Если packaging_rules переданы — цены и названия берутся из БД.
    Иначе — fallback на settings (для тестов и обратной совместимости).
    """
    if packaging_rules is not None:
        return _compute_fee_lines_from_rules(food_lines, foods_subtotal, order_type, packaging_rules)
    return _compute_fee_lines_legacy(food_lines, foods_subtotal, order_type)


def _compute_fee_lines_from_rules(
    food_lines: list[dict],
    foods_subtotal: float,
    order_type: str,
    rules: list[PackagingRule],
) -> tuple[list[dict], float]:
    fee_lines: list[dict] = []
    extras_total = 0.0
    needs_packaging = order_type in ("delivery", "pickup")
    rules_by_kind: dict[str, PackagingRule] = {r.kind: r for r in rules}

    if needs_packaging:
        for line in food_lines:
            if not isinstance(line, dict):
                continue
            qty = int(line.get("quantity", 1))
            if qty < 1:
                qty = 1
            choice = (line.get("packaging_plov_1kg") or "").strip()
            rule = match_packaging_rule(
                str(line.get("name", "")),
                str(line.get("category", "")),
                rules,
                packaging_choice=choice,
            )
            category_rule = match_packaging_rule(
                str(line.get("name", "")),
                str(line.get("category", "")),
                rules,
                packaging_choice=choice,
                scope="category",
            )
            if category_rule is not None and float(category_rule.price) > 0:
                rule = category_rule
            if rule is None or float(rule.price) <= 0:
                continue
            unit = float(rule.price)
            total = unit * qty
            fee_lines.append({
                "kind": f"packaging_{rule.kind}",
                "name": rule.name,
                "quantity": qty,
                "unit_price": unit,
                "item_total": total,
                "iiko_id": (rule.iiko_product_id or "").strip() or None,
            })
            extras_total += total

    for rule in rules:
        if (getattr(rule, "scope", None) or "item").strip().lower() != "order":
            continue
        if float(rule.price) <= 0:
            continue
        fee_lines.append({
            "kind": f"packaging_{rule.kind}",
            "name": rule.name,
            "quantity": 1,
            "unit_price": float(rule.price),
            "item_total": float(rule.price),
            "iiko_id": (rule.iiko_product_id or "").strip() or None,
        })
        extras_total += float(rule.price)

    if order_type == "delivery" and foods_subtotal < float(settings.pricing_delivery_free_threshold):
        dr = rules_by_kind.get("delivery")
        d_fee = float(dr.price) if dr else float(settings.pricing_delivery_fee)
        d_name = dr.name if dr else "Доставка"
        d_iiko = ((dr.iiko_product_id or "").strip() or None) if dr else (settings.iiko_product_id_delivery.strip() or None)
        fee_lines.append({
            "kind": "delivery",
            "name": d_name,
            "quantity": 1,
            "unit_price": d_fee,
            "item_total": d_fee,
            "iiko_id": d_iiko,
        })
        extras_total += d_fee

    return fee_lines, extras_total


def _compute_fee_lines_legacy(
    food_lines: list[dict],
    foods_subtotal: float,
    order_type: str,
) -> tuple[list[dict], float]:
    """Fallback: цены из settings (для тестов без БД)."""
    fee_lines: list[dict] = []
    extras_total = 0.0
    needs_packaging = order_type in ("delivery", "pickup")

    for line in food_lines:
        if not isinstance(line, dict):
            continue
        if not needs_packaging:
            continue
        qty = int(line.get("quantity", 1))
        if qty < 1:
            qty = 1
        kind = classify_packaging_kind(str(line.get("name", "")), str(line.get("category", "")))
        if kind == "manty":
            unit = float(settings.packaging_manty_unit_price)
            total = unit * qty
            fee_lines.append({"kind": "packaging_manty", "name": "Контейнер для мант", "quantity": qty, "unit_price": unit, "item_total": total, "iiko_id": settings.iiko_product_id_packaging_manty.strip() or None})
            extras_total += total
        elif kind == "shashlik":
            unit = float(settings.packaging_shashlik_unit_price)
            total = unit * qty
            fee_lines.append({"kind": "packaging_shashlik", "name": "Контейнер для шашлыка", "quantity": qty, "unit_price": unit, "item_total": total, "iiko_id": None})
            extras_total += total
        elif kind == "plov_half":
            unit = float(settings.packaging_plov_half_unit_price)
            total = unit * qty
            fee_lines.append({"kind": "packaging_plov_half", "name": "Контейнер средний (плов 0.5)", "quantity": qty, "unit_price": unit, "item_total": total, "iiko_id": settings.iiko_product_id_packaging_plov_half.strip() or None})
            extras_total += total
        elif kind == "plov_1kg":
            choice = (line.get("packaging_plov_1kg") or "").strip()
            if choice == "tabak":
                unit = float(settings.packaging_plov_1kg_tabak_unit_price)
                fee_lines.append({"kind": "packaging_plov_1kg_tabak", "name": "Контейнер-табак (плов 1 кг)", "quantity": qty, "unit_price": unit, "item_total": unit * qty, "iiko_id": settings.iiko_product_id_packaging_plov_tabak.strip() or None})
            elif choice == "foil_kazan":
                unit = float(settings.packaging_plov_1kg_foil_unit_price)
                fee_lines.append({"kind": "packaging_plov_1kg_foil", "name": "Фольгированный казан (плов 1 кг)", "quantity": qty, "unit_price": unit, "item_total": unit * qty, "iiko_id": settings.iiko_product_id_packaging_plov_foil.strip() or None})
            else:
                continue
            extras_total += unit * qty
        elif kind == "fries":
            unit = float(settings.packaging_fries_unit_price)
            total = unit * qty
            fee_lines.append({"kind": "packaging_fries", "name": "Соусница (фри)", "quantity": qty, "unit_price": unit, "item_total": total, "iiko_id": None})
            extras_total += total
        elif kind == "standard":
            unit = float(settings.packaging_standard_unit_price)
            if unit > 0:
                total = unit * qty
                fee_lines.append({"kind": "packaging_standard", "name": "Контейнер (ланч-бокс)", "quantity": qty, "unit_price": unit, "item_total": total, "iiko_id": None})
                extras_total += total

    if order_type == "delivery" and foods_subtotal < float(settings.pricing_delivery_free_threshold):
        d_fee = float(settings.pricing_delivery_fee)
        fee_lines.append({"kind": "delivery", "name": "Доставка", "quantity": 1, "unit_price": d_fee, "item_total": d_fee, "iiko_id": settings.iiko_product_id_delivery.strip() or None})
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
    packaging_rules: list[PackagingRule] | None = None,
    *,
    prepayment_enforced: bool = True,
) -> tuple[dict[str, object], float]:
    """
    Собирает items_json для БД: блюда, наценки, метаданные заказа, итоговая сумма.
    """
    foods = validated.valid_items
    foods_subtotal = float(validated.total_price)
    fee_lines, extras = compute_fee_lines(foods, foods_subtotal, ai.order_type, packaging_rules=packaging_rules)
    grand_total = foods_subtotal + extras

    requires_order_prepayment = prepayment_enforced and (
        grand_total >= float(settings.order_prepayment_threshold_kzt)
    )

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
    gc = getattr(ai, "guest_count_for_meal", None)
    if gc is not None:
        try:
            order_meta["guest_count"] = max(1, min(50, int(gc)))
        except (TypeError, ValueError):
            pass
    diet_notes = (getattr(ai, "dietary_allergy_notes", None) or "").strip()
    if diet_notes:
        order_meta["dietary_allergy_notes"] = diet_notes[:4000]
    if ai.booking_details:
        order_meta["booking_snapshot"] = {
            "date": ai.booking_details.date,
            "time": ai.booking_details.time,
            "guests": ai.booking_details.guests,
            "hall": ai.booking_details.hall,
        }

    merge_confidence_into_order_meta(order_meta, validated)

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
        # Предупреждение о предоплате добавляется один раз в intent_router,
        # здесь не дублируем — иначе клиент получает 2-3 одинаковых предупреждения.

    return "\n".join(lines)


def format_whatsapp_order_card(
    items_json: dict[str, object],
    validated_summary: str,
) -> str:
    """
    Состав заказа для WhatsApp: разделители, *жирный* для итога (Markdown WhatsApp).
    Содержательно совпадает с format_order_confirmation_summary.
    """
    core = format_order_confirmation_summary(items_json, validated_summary)
    parts: list[str] = [
        "✨ *Ваш заказ*",
        "━━━━━━━━━━━━",
        "",
    ]
    for raw in core.split("\n"):
        line = raw.rstrip()
        if line.startswith("💰 Итого"):
            rest = line.replace("💰", "", 1).strip()
            parts.append(f"💰 *{rest}*")
        else:
            parts.append(line)
    parts.extend(
        [
            "",
            "━━━━━━━━━━━━",
            "_Готовим для вас с душой — до встречи!_",
        ]
    )
    return "\n".join(parts)


def merge_total_into_items_json(items_json: dict[str, object], total_price: float) -> dict[str, object]:
    """Дублирует итог в JSON для отображения в админке."""
    out = dict(items_json)
    out["total_price"] = total_price
    return out


def finalize_order_draft(
    validated: ValidatedOrder,
    ai: AIBrainResponse,
    packaging_rules: list[PackagingRule] | None = None,
    *,
    prepayment_enforced: bool = True,
) -> tuple[dict[str, object], float]:
    """
    Финальный ``items_json`` и сумма по ТЗ: блюда + контейнеры + доставка (см. ``compute_fee_lines``).
    """
    payload, grand_total = build_order_items_json(
        validated,
        ai,
        packaging_rules=packaging_rules,
        prepayment_enforced=prepayment_enforced,
    )
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
    merge_confidence_into_order_meta(
        order_meta,
        ValidatedOrder([], [], 0.0, "", fuzzy_match_details=[]),
    )
    payload: dict[str, object] = {
        "items": food_lines,
        "fee_lines": fee_lines,
        "foods_subtotal": foods_subtotal,
        "order_meta": order_meta,
    }
    return merge_total_into_items_json(payload, grand_total), grand_total
