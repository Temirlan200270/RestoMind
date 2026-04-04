"""
Strategy Engine (Phase 18+): правила допродаж и анти-повторов до вызова Gemini.
STATE → LOGIC → AI: здесь только LOGIC — модель получает готовое текстовое задание,
а цены и состав корзины по-прежнему считает бэкенд после ответа (merge + finalize).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.db.models import MenuItem


def _norm_name(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


@dataclass
class StrategyDecision:
    """Решение «что продавать / когда остановиться» — не заменяет AIBrainResponse."""

    goal: str
    """upsell | assist_choice | close_order | neutral"""

    target_iiko_ids: list[str] = field(default_factory=list)
    target_name_hints: list[str] = field(default_factory=list)
    restriction: str = ""


# Ключевые слова в названии позиции корзины → подстроки для поиска кандидата в меню (как в меню ресторана)
_PAIRING_TRIGGERS: list[tuple[tuple[str, ...], tuple[str, ...]]] = [
    (("плов",), ("ачичук", "ачу-чук", "ачу")),
    (("мант",), ("чай",)),
    (("шашлык",), ("лаваш", "лепёшк")),
]

_DRINK_NAME_HINTS = (
    "чай",
    "кофе",
    "кола",
    "напит",
    "сок",
    "компот",
    "лимонад",
    "капучино",
    "эспрессо",
    "латте",
)
_DRINK_CAT_HINTS = ("напит", "кофе", "чай", "бар", "сок")


def _cart_iiko_ids(cart_items: list[dict]) -> set[str]:
    out: set[str] = set()
    for it in cart_items:
        if not isinstance(it, dict):
            continue
        iid = (it.get("iiko_id") or "").strip()
        if iid:
            out.add(iid.lower())
    return out


def _line_text(it: dict) -> str:
    return _norm_name(str(it.get("name") or ""))


def _has_drink_in_cart(cart_items: list[dict]) -> bool:
    for it in cart_items:
        if not isinstance(it, dict):
            continue
        nm = _line_text(it)
        cat = _norm_name(str(it.get("category") or ""))
        if any(h in nm for h in _DRINK_NAME_HINTS):
            return True
        if any(h in cat for h in _DRINK_CAT_HINTS):
            return True
    return False


def _offered_names(meta: dict) -> set[str]:
    """Уже предложенные названия (для анти-повтора по тексту)."""
    s: set[str] = set()
    rec = meta.get("recommendation")
    if isinstance(rec, dict) and rec.get("offered"):
        s.add(_norm_name(str(rec["offered"])))
    tr = meta.get("recommendation_trace")
    if isinstance(tr, list):
        for e in tr:
            if isinstance(e, dict) and e.get("offered"):
                s.add(_norm_name(str(e["offered"])))
    return {x for x in s if x}


def _offered_iiko_ids(meta: dict) -> set[str]:
    """UUID уже предложенных допродаж (из recommendation / trace)."""
    s: set[str] = set()
    rec = meta.get("recommendation")
    if isinstance(rec, dict):
        oid = (rec.get("offered_iiko_id") or "").strip().lower()
        if oid:
            s.add(oid)
    tr = meta.get("recommendation_trace")
    if isinstance(tr, list):
        for e in tr:
            if not isinstance(e, dict):
                continue
            oid = (e.get("offered_iiko_id") or "").strip().lower()
            if oid:
                s.add(oid)
    return s


def _rejected_iiko_ids(meta: dict) -> set[str]:
    """UUID отказов по допродажам (order_meta.upsell_rejected_iiko_ids)."""
    raw = meta.get("upsell_rejected_iiko_ids")
    if not isinstance(raw, list):
        return set()
    return {str(x).strip().lower() for x in raw if str(x).strip()}


def _rejected_names(meta: dict) -> set[str]:
    """Legacy: строковые отказы в upsell_rejected (имена), до внедрения UUID."""
    raw = meta.get("upsell_rejected")
    if not isinstance(raw, list):
        return set()
    return {_norm_name(str(x)) for x in raw if x}


def _trace_len(meta: dict) -> int:
    tr = meta.get("recommendation_trace")
    if isinstance(tr, list):
        return len(tr)
    return 0


def _find_menu_item(
    menu_items: list[MenuItem],
    name_substrings: tuple[str, ...],
    *,
    skip_iiko: set[str],
    skip_names: set[str],
) -> MenuItem | None:
    for m in menu_items:
        if not m.is_available:
            continue
        iid = (m.iiko_id or "").strip().lower()
        if iid and iid in skip_iiko:
            continue
        mn = _norm_name(m.name)
        if mn in skip_names:
            continue
        for sub in name_substrings:
            if sub in mn:
                return m
    return None


def build_sales_strategy(
    cart_items: list[dict],
    total_price: float,
    order_meta: dict,
    menu_items: list[MenuItem],
) -> StrategyDecision:
    """
    Мозг продаж: до Gemini решаем цель и кандидатов; модель формулирует reply_text и JSON.
    """
    meta = order_meta if isinstance(order_meta, dict) else {}

    if _trace_len(meta) >= 2:
        return StrategyDecision(
            goal="close_order",
            restriction=(
                "Уже было несколько рекомендаций в этом заказе. НЕ предлагай новые блюда — "
                "помоги завершить оформление (тип получения, оплата, уточнения)."
            ),
        )

    offered = _offered_names(meta)
    rejected = _rejected_names(meta)
    skip_iiko = (
        _cart_iiko_ids(cart_items)
        | _offered_iiko_ids(meta)
        | _rejected_iiko_ids(meta)
    )
    skip_names = offered | rejected

    if not cart_items:
        return StrategyDecision(
            goal="assist_choice",
            restriction=(
                "Корзина пуста. Мягко помоги выбрать блюда из меню; не навязывай списки из 5+ позиций."
            ),
        )

    candidates: list[MenuItem] = []

    # Нет явного напитка в корзине
    if not _has_drink_in_cart(cart_items):
        for sub in ("чай", "капучино", "кола"):
            m = _find_menu_item(menu_items, (sub,), skip_iiko=skip_iiko, skip_names=skip_names)
            if m:
                candidates.append(m)
                break

    # Низкий чек — лёгкая добавка
    if 0 < total_price < 5000:
        for sub in ("самса", "лепёшк", "лаваш"):
            m = _find_menu_item(menu_items, (sub,), skip_iiko=skip_iiko, skip_names=skip_names)
            if m:
                candidates.append(m)
                break

    # Связки к блюдам в корзине
    for it in cart_items:
        if not isinstance(it, dict):
            continue
        nm = _line_text(it)
        for triggers, candidate_subs in _PAIRING_TRIGGERS:
            if not any(t in nm for t in triggers):
                continue
            m = _find_menu_item(
                menu_items,
                candidate_subs,
                skip_iiko=skip_iiko,
                skip_names=skip_names,
            )
            if m:
                candidates.append(m)

    # Уникальные по iiko_id
    seen: set[str] = set()
    uniq: list[MenuItem] = []
    for m in candidates:
        iid = (m.iiko_id or "").strip().lower()
        if iid and iid not in seen:
            seen.add(iid)
            uniq.append(m)

    if not uniq:
        return StrategyDecision(
            goal="close_order",
            restriction="Нечего предлагать из правил (всё уже в корзине или уже предлагали). Не навязывай допродажу.",
        )

    top = uniq[0]
    iid = (top.iiko_id or "").strip()
    return StrategyDecision(
        goal="upsell",
        target_iiko_ids=[iid] if iid else [],
        target_name_hints=[top.name],
        restriction=(
            "Максимум **одна** рекомендация в этом сообщении. Объясни коротко «почему к этому заказу». "
            "Если предлагаешь — заполни is_recommendation, upsell_offered (как в меню), "
            "upsell_offered_id (UUID из меню, если есть) и upsell_reasoning."
        ),
    )


def format_strategy_for_prompt(decision: StrategyDecision) -> str:
    """Текстовый блок в system instruction (после меню и черновика)."""
    if decision.goal == "neutral" and not decision.restriction:
        return ""

    lines = [
        "## Стратегия продаж (задание сервера)",
        "Следуй цели ниже при **intent=order** и осмысленном диалоге о заказе. "
        "Не выдумывай цены и не подменяй состав корзины — его считает бэкенд.",
        f"- **Цель:** `{decision.goal}`",
    ]
    if decision.target_name_hints:
        lines.append(f"- **Кого предложить по смыслу (есть в меню):** {', '.join(decision.target_name_hints)}")
    if decision.target_iiko_ids:
        lines.append(
            f"- **iiko_id для поля upsell_offered_id (если рекомендуешь):** "
            f"{', '.join(decision.target_iiko_ids)}",
        )
    if decision.restriction:
        lines.append(f"- **Ограничение:** {decision.restriction}")
    if decision.goal == "close_order":
        lines.append("- При этой цели **не** включай допродажу: `is_recommendation=false`, без upsell-полей.")
    lines.append("")
    return "\n".join(lines)
