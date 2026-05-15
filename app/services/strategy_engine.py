"""Детерминированные правила допродажи из БД (после LLM, до ответа клиенту)."""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MenuItem, UpsellRule
from app.schemas.ai_schemas import AIBrainResponse
from app.services.upsell_utils import (
    cart_iiko_ids,
    collect_cart_tag_profile,
    is_forbidden_upsell_candidate,
    is_preferred_upsell_candidate,
    rejected_upsell_iiko_ids,
)

logger = logging.getLogger(__name__)


def _cart_categories(items: list[dict[str, Any]]) -> set[str]:
    out: set[str] = set()
    for it in items:
        if not isinstance(it, dict):
            continue
        c = (it.get("category") or "").strip().lower()
        if c:
            out.add(c)
    return out


def _rejected_iiko(meta: dict[str, Any]) -> set[str]:
    # backward-compatible name: used only inside this module
    return rejected_upsell_iiko_ids(meta)


def _offered_recently(meta: dict[str, Any], iiko_id: str) -> bool:
    trace = meta.get("recommendation_trace")
    if not isinstance(trace, list):
        return False
    want = iiko_id.strip().lower()
    for ev in trace[-8:]:
        if not isinstance(ev, dict):
            continue
        if str(ev.get("offered_iiko_id") or "").strip().lower() == want:
            return True
    return False


async def load_active_upsell_rules(db: AsyncSession, organization_id: int) -> list[UpsellRule]:
    res = await db.execute(
        select(UpsellRule)
        .where(
            UpsellRule.organization_id == organization_id,
            UpsellRule.is_active.is_(True),
        )
        .order_by(UpsellRule.sort_order.desc(), UpsellRule.id.asc()),
    )
    return list(res.scalars().all())


def _menu_candidates(
    menu_items: list[MenuItem],
    organization_id: int,
    suggest_substr: str,
    exclude_iiko: set[str],
    cart_tags: set[str],
) -> list[MenuItem]:
    ss = (suggest_substr or "").strip().lower()
    out: list[MenuItem] = []
    for m in menu_items:
        if not m.is_available:
            continue
        if m.organization_id is not None and int(m.organization_id) != int(organization_id):
            continue
        cat = (m.category or "").lower()
        if ss and ss not in cat:
            continue
        iid = (m.iiko_id or "").strip().lower()
        if iid and iid in exclude_iiko:
            continue
        if is_forbidden_upsell_candidate(m, cart_tags=cart_tags):
            continue
        out.append(m)
    out.sort(key=lambda x: (0 if is_preferred_upsell_candidate(x) else 1, float(x.price or 0)))
    return out


async def apply_db_upsell_rules(
    db: AsyncSession,
    *,
    organization_id: int,
    reply_text: str,
    items_json: dict[str, Any],
    grand_total: float,
    menu_items: list[MenuItem],
    ai_eff: AIBrainResponse,
) -> tuple[str, dict[str, Any]]:
    """
    Если подходит правило — добавляет одну фразу и событие в ``recommendation_trace``.
    Возвращает (новый reply, новый items_json).
    """
    if (
        ai_eff.is_recommendation
        or (ai_eff.upsell_offered or "").strip()
        or (getattr(ai_eff, "upsell_offered_id", None) or "").strip()
    ):
        return reply_text, items_json
    raw_items = items_json.get("items")
    items = [x for x in (raw_items or []) if isinstance(x, dict)]
    meta = dict(items_json.get("order_meta") or {}) if isinstance(items_json.get("order_meta"), dict) else {}
    rules = await load_active_upsell_rules(db, organization_id)
    if not rules:
        return reply_text, items_json

    cats = _cart_categories(items)
    cart_tags, _has_side_tag, _has_drink_tag = collect_cart_tag_profile(items, menu_items)
    in_cart = cart_iiko_ids(items)
    rejected = _rejected_iiko(meta)

    # Ленивый кэш timezone организации (TTL 5 мин) — для trigger_mode=time_of_day
    _org_tz_str: str | None = None

    async def _get_org_tz() -> str:
        nonlocal _org_tz_str
        if _org_tz_str is not None:
            return _org_tz_str
        from app.db.models import Organization
        org = await db.get(Organization, organization_id)
        _org_tz_str = (getattr(org, "timezone", None) or "UTC") if org else "UTC"
        return _org_tz_str

    for rule in rules:
        trig = (rule.trigger_category or "").strip().lower()
        mode = (rule.trigger_mode or "missing_category").strip()

        # --- Проверка условия срабатывания ---
        triggered = False
        if mode == "missing_category":
            if not trig:
                continue
            triggered = not any(trig in c for c in cats)

        elif mode == "item_present":
            # Срабатывает, если указанная категория УЖЕ есть в корзине
            if not trig:
                continue
            triggered = any(trig in c for c in cats)

        elif mode == "time_of_day":
            # trigger_category используется как диапазон часов: "HH-HH", напр. "17-22"
            tv = trig  # trigger_category содержит диапазон
            try:
                start_h, end_h = (int(p) for p in tv.split("-", 1))
                tz_str = await _get_org_tz()
                try:
                    import zoneinfo
                    tz = zoneinfo.ZoneInfo(tz_str)
                except Exception:
                    from datetime import timezone as _dtz
                    tz = _dtz.utc
                current_hour = datetime.now(tz).hour
                triggered = start_h <= current_hour < end_h
            except (ValueError, AttributeError):
                continue

        else:
            continue

        if not triggered:
            continue

        # --- Проверка суммы заказа ---
        mn = float(rule.min_order_sum or 0)
        if grand_total < mn:
            continue
        mx = rule.max_order_sum
        if mx is not None and float(grand_total) > float(mx):
            continue

        candidates = _menu_candidates(
            menu_items, organization_id, rule.suggest_category, in_cart | rejected, cart_tags,
        )
        if not candidates:
            continue
        pick = candidates[0]
        iid = (pick.iiko_id or "").strip()
        if not iid:
            continue
        if _offered_recently(meta, iid):
            continue

        tmpl = (rule.phrase_template or "").strip() or "К заказу отлично подойдёт {item_name} ({price} ₸). Добавить?"
        try:
            extra = tmpl.format(
                item_name=pick.name,
                price=int(float(pick.price or 0)),
            )
        except (KeyError, ValueError):
            extra = tmpl.replace("{item_name}", pick.name).replace("{price}", str(int(float(pick.price or 0))))

        trace = list(meta.get("recommendation_trace") or [])
        if not isinstance(trace, list):
            trace = []
        trace.append({
            "source": "upsell_rule",
            "rule_id": rule.id,
            "offered_iiko_id": iid,
            "offered": pick.name,
            "reason": f"DB rule #{rule.id} {mode}={trig}",
        })
        meta["recommendation_trace"] = trace[-15:]
        new_ij = dict(items_json)
        new_ij["order_meta"] = meta
        return f"{reply_text}\n\n💡 {extra}", new_ij

    return reply_text, items_json


def apply_guest_meal_guidance_reply(
    reply_text: str,
    items_json: dict[str, Any],
    menu_rows: list[MenuItem],
) -> str:
    """
    Детерминированный слой после ИИ: если в заказе много гостей, а в чеке только «порционные»
    позиции без признака «на компанию» — добавляем короткую подсказку (не меняет корзину).
    """
    meta = items_json.get("order_meta") if isinstance(items_json.get("order_meta"), dict) else {}
    raw_g = meta.get("guest_count")
    try:
        guests = int(raw_g) if raw_g is not None else 0
    except (TypeError, ValueError):
        guests = 0
    if guests < 3:
        return reply_text

    raw_items = items_json.get("items")
    lines = [x for x in (raw_items or []) if isinstance(x, dict)]
    if len(lines) < 1:
        return reply_text

    by_iiko: dict[str, MenuItem] = {}
    for m in menu_rows or []:
        iid = (m.iiko_id or "").strip().lower()
        if iid:
            by_iiko[iid] = m

    def _line_kind(line: dict[str, Any]) -> str:
        iid = str(line.get("iiko_item_id") or line.get("iiko_id") or "").strip().lower()
        if iid and iid in by_iiko:
            pk = (getattr(by_iiko[iid], "portion_kind", None) or "single").strip().lower()
            return "shareable" if pk == "shareable" else "single"
        return "single"

    kinds = [_line_kind(x) for x in lines]
    if any(k == "shareable" for k in kinds):
        return reply_text
    if all(k == "single" for k in kinds):
        return (
            f"{reply_text}\n\n"
            f"👥 Для компании из {guests} гостей порционных позиций может не хватить по объёму. "
            "Если хотите — могу подсказать блюда «на компанию» из меню или уточнить у кухни порции."
        )
    return reply_text
