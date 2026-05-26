"""

RC-A / RC v3: ранжирование кандидатов допродажи по детерминированной формуле.



Используется в build_sales_strategy и apply_db_upsell_rules вместо «первый в списке».

"""



from __future__ import annotations



import re

from dataclasses import dataclass, field

from datetime import datetime, timezone

from typing import Any



from app.db.models import MenuItem, UpsellRule

from app.services.upsell_utils import (

    _menu_row_is_drink_candidate,

    collect_cart_tag_profile,

    is_preferred_upsell_candidate,

    parse_menu_tags,

    rejected_upsell_iiko_ids,

    upsell_rejection_ids_in_cooldown,

)



_DRINK_CAT_HINTS = ("напит", "кофе", "чай", "бар", "сок")

_SIDE_CAT_HINTS = ("салат", "гарнир", "закуск", "side")

_REJECTED_PENALTY = -500.0

_RECENT_OFFER_PENALTY = -12.0

_COOLDOWN_PENALTY = -400.0

_LOW_CHECK_TOTAL = 3500.0

_PRICE_VS_CART_RATIO = 0.55





def _norm_name(s: str) -> str:

    return re.sub(r"\s+", " ", (s or "").strip().lower())





@dataclass

class UpsellScoreResult:

    score: float

    reasons: list[str]

    menu_item: MenuItem

    blocked_reasons: list[str] = field(default_factory=list)

    candidate_rank: int = 0





def _empty_explainability(*, alternatives_seen: int = 0) -> dict[str, Any]:

    return {

        "score": None,

        "score_reasons": [],

        "blocked_reasons": [],

        "candidate_rank": None,

        "alternatives_seen": alternatives_seen,

    }





def _cart_categories(cart_items: list[dict[str, Any]]) -> set[str]:

    out: set[str] = set()

    for it in cart_items:

        if not isinstance(it, dict):

            continue

        c = (it.get("category") or "").strip().lower()

        if c:

            out.add(c)

    return out





def _cart_total(cart_items: list[dict[str, Any]]) -> float:

    total = 0.0

    for it in cart_items:

        if not isinstance(it, dict):

            continue

        try:

            qty = float(it.get("quantity") or 1)

        except (TypeError, ValueError):

            qty = 1.0

        line_total = it.get("item_total")

        if line_total is None:

            try:

                line_total = float(it.get("price") or 0) * qty

            except (TypeError, ValueError):

                line_total = 0.0

        else:

            try:

                line_total = float(line_total)

            except (TypeError, ValueError):

                line_total = 0.0

        total += line_total

    return total





def _offered_iiko_recently(order_meta: dict[str, Any], iiko_id: str, *, tail: int = 8) -> bool:

    trace = order_meta.get("recommendation_trace")

    if not isinstance(trace, list):

        return False

    want = iiko_id.strip().lower()

    for ev in trace[-tail:]:

        if not isinstance(ev, dict):

            continue

        if str(ev.get("offered_iiko_id") or "").strip().lower() == want:

            return True

    return False





def _rejected_iiko_set(context: dict[str, Any]) -> set[str]:

    meta = context.get("order_meta")

    order_meta = meta if isinstance(meta, dict) else {}

    blocked = set(rejected_upsell_iiko_ids(order_meta))

    for ev in context.get("recent_rejections") or []:

        status = (

            getattr(ev, "status", None)

            or (ev.get("status") if isinstance(ev, dict) else None)

            or ""

        ).strip().lower()

        if status != "rejected":

            continue

        oid = (

            getattr(ev, "offered_item_id", None)

            or (ev.get("offered_item_id") if isinstance(ev, dict) else None)

            or ""

        ).strip().lower()

        if oid:

            blocked.add(oid)

    return blocked





def _margin_score(item: MenuItem) -> tuple[float, str | None]:

    price = float(item.price or 0)

    cost = item.cost_price

    if cost is None or price <= 0:

        return 0.0, None

    margin_pct = ((price - float(cost)) / price) * 100.0

    pts = min(20.0, max(0.0, margin_pct * 0.2))

    return pts, f"margin_{margin_pct:.0f}pct"





def _category_match_score(

    item: MenuItem,

    *,

    cart_cats: set[str],

    cart_tags: set[str],

    rule: UpsellRule | None,

) -> tuple[float, list[str]]:

    reasons: list[str] = []

    score = 0.0

    cat = (item.category or "").lower()

    tags = parse_menu_tags(item.tags)



    if rule is not None:

        suggest = (rule.suggest_category or "").strip().lower()

        if suggest and suggest in cat:

            score += 12.0

            reasons.append("rule_category_match")



    spicy_cart = bool(cart_tags & {"spicy", "острое", "острый"})

    main_cart = bool(cart_tags & {"main_course", "meat", "heavy"})

    has_side_in_cart = bool(cart_tags & {"side_dish"})



    if spicy_cart and (_menu_row_is_drink_candidate(item) or tags & {"drink"}):
        score += 22.0
        reasons.append("category_match_spicy_drink")

    elif main_cart and not has_side_in_cart and (

        tags & {"side_dish", "goes_well_with_meat"}

        or any(h in cat for h in _SIDE_CAT_HINTS)

    ):

        score += 14.0

        reasons.append("category_match_main_side")

    elif not cart_cats and is_preferred_upsell_candidate(item):

        score += 4.0

        reasons.append("category_match_preferred")



    return score, reasons





def _historical_pair_score(

    item: MenuItem,

    cart_items: list[dict[str, Any]],

    pair_scores: dict[str, dict[str, float]] | None,

) -> tuple[float, str | None]:

    """Co-occurrence score from mined pair_scores or fallback to menu upsell_pairs."""

    iid = (item.iiko_id or "").strip().lower()

    if not iid:

        return 0.0, None



    best_score = 0.0

    if isinstance(pair_scores, dict) and pair_scores:

        for it in cart_items:

            if not isinstance(it, dict):

                continue

            base = str(it.get("iiko_id") or it.get("iiko_item_id") or "").strip().lower()

            if not base:

                continue

            offered_map = pair_scores.get(base) or {}

            try:

                sc = float(offered_map.get(iid) or 0.0)

            except (TypeError, ValueError):

                sc = 0.0

            if sc > best_score:

                best_score = sc

        if best_score > 0:

            pts = min(25.0, best_score * 0.25)

            return pts, f"historical_pair_{best_score:.0f}"



    for it in cart_items:

        if not isinstance(it, dict):

            continue

        pairs_raw = str(it.get("upsell_pairs") or "").strip()

        if not pairs_raw:

            continue

        for part in pairs_raw.split(","):

            if part.strip().lower() == iid:

                return 5.0, "historical_pair_manual"

    return 0.0, None





def _copilot_list_score(

    item: MenuItem,

    copilot_feed: dict[str, Any] | None,

    list_key: str,

    reason_tag: str,

    *,

    base_pts: float = 22.0,

    step: float = 2.0,

) -> tuple[float, str | None]:

    if not copilot_feed:

        return 0.0, None

    rows = copilot_feed.get(list_key) or []

    if not isinstance(rows, list):

        return 0.0, None

    iid = (item.iiko_id or "").strip().lower()

    nm = _norm_name(item.name)

    for idx, row in enumerate(rows[:10]):

        if not isinstance(row, dict):

            continue

        row_iid = str(row.get("iiko_id") or "").strip().lower()

        row_nm = _norm_name(str(row.get("name") or ""))

        if (iid and row_iid == iid) or (nm and row_nm == nm):

            pts = max(8.0, base_pts - float(idx) * step)

            return pts, reason_tag

    return 0.0, None





def _promote_today_score(item: MenuItem, copilot_feed: dict[str, Any] | None) -> tuple[float, str | None]:

    return _copilot_list_score(

        item,

        copilot_feed,

        "promote_today_candidates",

        "promote_today",

    )





def _high_margin_score(item: MenuItem, copilot_feed: dict[str, Any] | None) -> tuple[float, str | None]:

    return _copilot_list_score(

        item,

        copilot_feed,

        "high_margin_candidates",

        "high_margin_copilot",

        base_pts=18.0,

        step=1.5,

    )





def _guest_preference_score(item: MenuItem, user_preferences: dict[str, Any] | None) -> tuple[float, list[str]]:

    if not user_preferences:

        return 0.0, []

    reasons: list[str] = []

    score = 0.0

    drinks_freq = user_preferences.get("drinks_frequency")

    cat = (item.category or "").lower()

    is_drink = _menu_row_is_drink_candidate(item) or any(h in cat for h in _DRINK_CAT_HINTS)



    if drinks_freq is not None:

        try:

            df = float(drinks_freq)

        except (TypeError, ValueError):

            df = None

        else:

            if is_drink and df >= 0.35:

                score += 8.0

                reasons.append("guest_pref_drinks")

            elif is_drink and df < 0.1:

                score -= 6.0

                reasons.append("guest_pref_low_drinks")



    never_cats = user_preferences.get("never_categories") or set()

    if never_cats and any(nc in cat for nc in never_cats if nc):

        score -= 8.0

        reasons.append("guest_pref_never_category")



    return score, reasons





def _time_of_day_score(item: MenuItem, org_tz: str | None) -> tuple[float, str | None]:

    tz_str = (org_tz or "UTC").strip() or "UTC"

    try:

        import zoneinfo



        tz = zoneinfo.ZoneInfo(tz_str)

    except Exception:

        tz = timezone.utc

    hour = datetime.now(tz).hour

    cat = (item.category or "").lower()

    is_drink = _menu_row_is_drink_candidate(item)

    is_side = any(h in cat for h in _SIDE_CAT_HINTS)



    if 6 <= hour < 11 and is_drink:

        return 6.0, "time_morning_drink"

    if 11 <= hour < 17 and is_side:

        return 4.0, "time_lunch_side"

    if 17 <= hour < 24 and (is_drink or is_side):

        return 5.0, "time_evening_addon"

    return 0.0, None





def _price_vs_cart_penalty(item: MenuItem, cart_items: list[dict[str, Any]]) -> tuple[float, list[str]]:

    reasons: list[str] = []

    cart_total = _cart_total(cart_items)

    upsell_price = float(item.price or 0)

    if upsell_price <= 0:

        return 0.0, reasons



    if cart_total > 0 and upsell_price > cart_total * _PRICE_VS_CART_RATIO:

        reasons.append("penalty_price_vs_cart")

        return -18.0, reasons



    if cart_total < _LOW_CHECK_TOTAL and upsell_price > cart_total * 0.35:

        reasons.append("penalty_price_low_check")

        return -22.0, reasons



    return 0.0, reasons





def _offer_frequency_penalty(

    item: MenuItem,

    offer_frequency_penalties: dict[str, float] | None,

) -> tuple[float, list[str]]:

    if not offer_frequency_penalties:

        return 0.0, []

    iid = (item.iiko_id or "").strip().lower()

    if not iid:

        return 0.0, []

    try:

        penalty = float(offer_frequency_penalties.get(iid) or 0.0)

    except (TypeError, ValueError):

        penalty = 0.0

    if penalty >= 0:

        return 0.0, []

    return penalty, ["penalty_offer_frequency"]





def _penalty_scores(item: MenuItem, context: dict[str, Any]) -> tuple[float, list[str]]:

    reasons: list[str] = []

    score = 0.0

    iid = (item.iiko_id or "").strip().lower()

    if not iid:

        return 0.0, reasons



    if iid in _rejected_iiko_set(context):

        score += _REJECTED_PENALTY

        reasons.append("penalty_rejected")



    user_meta = context.get("user_meta")

    if isinstance(user_meta, dict) and iid in upsell_rejection_ids_in_cooldown(user_meta, cooldown_hours=48.0):

        score += _COOLDOWN_PENALTY

        reasons.append("penalty_cooldown")



    meta = context.get("order_meta")

    order_meta = meta if isinstance(meta, dict) else {}

    if _offered_iiko_recently(order_meta, iid):

        score += _RECENT_OFFER_PENALTY

        reasons.append("penalty_recently_offered")



    return score, reasons





def _blocked_reasons_from(reasons: list[str], score: float) -> list[str]:

    blocked: list[str] = []

    for tag in reasons:

        if tag.startswith("penalty_"):

            blocked.append(tag)

    if score <= -100.0 and "blocked_low_score" not in blocked:

        blocked.append("blocked_low_score")

    return blocked





def _resolve_menu_for_tags(context: dict[str, Any], candidates: list[MenuItem]) -> list[MenuItem]:

    menu_ctx = context.get("menu_items")

    if isinstance(menu_ctx, list) and menu_ctx:

        return [m for m in menu_ctx if isinstance(m, MenuItem)]

    return candidates





def _score_candidate(item: MenuItem, context: dict[str, Any], cart_tags: set[str]) -> UpsellScoreResult:

    cart_items = [x for x in (context.get("cart_items") or []) if isinstance(x, dict)]

    cart_cats = _cart_categories(cart_items)

    rule = context.get("rule")

    rule_obj = rule if isinstance(rule, UpsellRule) else None

    prefs = context.get("user_preferences")

    prefs_d = prefs if isinstance(prefs, dict) and prefs else None

    copilot = context.get("copilot_feed")

    copilot_d = copilot if isinstance(copilot, dict) else None

    pair_scores = context.get("pair_scores")

    pair_scores_d = pair_scores if isinstance(pair_scores, dict) else None

    offer_penalties = context.get("offer_frequency_penalties")

    offer_penalties_d = offer_penalties if isinstance(offer_penalties, dict) else None



    reasons: list[str] = []

    total = 0.0



    cat_pts, cat_rs = _category_match_score(

        item, cart_cats=cart_cats, cart_tags=cart_tags, rule=rule_obj,

    )

    total += cat_pts

    reasons.extend(cat_rs)



    m_pts, m_r = _margin_score(item)

    total += m_pts

    if m_r:

        reasons.append(m_r)



    hp_pts, hp_r = _historical_pair_score(item, cart_items, pair_scores_d)

    total += hp_pts

    if hp_r:

        reasons.append(hp_r)



    gp_pts, gp_rs = _guest_preference_score(item, prefs_d)

    total += gp_pts

    reasons.extend(gp_rs)



    pt_pts, pt_r = _promote_today_score(item, copilot_d)

    total += pt_pts

    if pt_r:

        reasons.append(pt_r)



    hm_pts, hm_r = _high_margin_score(item, copilot_d)

    total += hm_pts

    if hm_r:

        reasons.append(hm_r)



    tod_pts, tod_r = _time_of_day_score(item, context.get("org_tz"))

    total += tod_pts

    if tod_r:

        reasons.append(tod_r)



    if is_preferred_upsell_candidate(item):

        total += 3.0

        reasons.append("preferred_candidate")



    freq_pts, freq_rs = _offer_frequency_penalty(item, offer_penalties_d)

    total += freq_pts

    reasons.extend(freq_rs)



    price_pts, price_rs = _price_vs_cart_penalty(item, cart_items)

    total += price_pts

    reasons.extend(price_rs)



    pen_pts, pen_rs = _penalty_scores(item, context)

    total += pen_pts

    reasons.extend(pen_rs)



    final_score = round(total, 2)

    blocked = _blocked_reasons_from(reasons, final_score)

    return UpsellScoreResult(

        score=final_score,

        reasons=reasons,

        menu_item=item,

        blocked_reasons=blocked,

    )





def rank_upsell_candidates(

    candidates: list[MenuItem],

    *,

    context: dict[str, Any],

) -> tuple[list[UpsellScoreResult], int]:

    """Ранжирует кандидатов по убыванию score; возвращает alternatives_seen."""

    if not candidates:

        return [], 0

    cart_items = [x for x in (context.get("cart_items") or []) if isinstance(x, dict)]

    menu_rows = _resolve_menu_for_tags(context, candidates)

    cart_tags, _, _ = collect_cart_tag_profile(cart_items, menu_rows)

    scored = [_score_candidate(m, context, cart_tags) for m in candidates]

    scored.sort(key=lambda r: (-r.score, (r.menu_item.iiko_id or "").lower()))

    alternatives_seen = len(scored)

    for idx, row in enumerate(scored, start=1):

        row.candidate_rank = idx

    return scored, alternatives_seen





def pick_best_candidate(

    candidates: list[MenuItem],

    *,

    context: dict[str, Any],

) -> tuple[MenuItem | None, dict[str, Any]]:

    """Возвращает лучшего кандидата и explainability meta для attribution."""

    ranked, alternatives_seen = rank_upsell_candidates(candidates, context=context)

    if not ranked:

        return None, _empty_explainability(alternatives_seen=0)



    best = ranked[0]

    explain = {

        "score": best.score,

        "score_reasons": list(best.reasons),

        "blocked_reasons": list(best.blocked_reasons),

        "candidate_rank": best.candidate_rank,

        "alternatives_seen": alternatives_seen,

    }

    if best.score <= -100.0:

        return None, explain

    return best.menu_item, explain


