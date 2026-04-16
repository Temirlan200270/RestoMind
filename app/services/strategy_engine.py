"""Детерминированные правила допродажи из БД (после LLM, до ответа клиенту)."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MenuItem, UpsellRule
from app.schemas.ai_schemas import AIBrainResponse
from app.services.upsell_utils import cart_iiko_ids, rejected_upsell_iiko_ids

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
        out.append(m)
    out.sort(key=lambda x: float(x.price or 0))
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
    in_cart = cart_iiko_ids(items)
    rejected = _rejected_iiko(meta)

    for rule in rules:
        trig = (rule.trigger_category or "").strip().lower()
        if rule.trigger_mode != "missing_category" or not trig:
            continue
        if any(trig in c for c in cats):
            continue
        mn = float(rule.min_order_sum or 0)
        if grand_total < mn:
            continue
        mx = rule.max_order_sum
        if mx is not None and float(grand_total) > float(mx):
            continue

        candidates = _menu_candidates(
            menu_items, organization_id, rule.suggest_category, in_cart | rejected,
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
            "reason": f"DB rule #{rule.id} missing_category={trig}",
        })
        meta["recommendation_trace"] = trace[-15:]
        new_ij = dict(items_json)
        new_ij["order_meta"] = meta
        return f"{reply_text}\n\n💡 {extra}", new_ij

    return reply_text, items_json
