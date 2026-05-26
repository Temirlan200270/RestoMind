from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import UpsellOfferEvent

USER_META_UPSELL_REJECTS_KEY = "upsell_rejections"
"""В User.meta_json: map iiko_id (lower) → ISO-8601 UTC от последнего отказа."""

UPSELL_EVENT_STATUS_REJECTED = "rejected"
UPSELL_EVENT_STATUS_SHOWN = "shown"

_DEFAULT_CROSS_ORDER_OFFER_DAYS = 7


def cart_iiko_ids(items: list[dict[str, Any]]) -> set[str]:
    out: set[str] = set()
    for it in items:
        if not isinstance(it, dict):
            continue
        iid = str(it.get("iiko_id") or "").strip().lower()
        if iid:
            out.add(iid)
    return out


def rejected_upsell_iiko_ids(meta: Mapping[str, Any]) -> set[str]:
    raw = meta.get("upsell_rejected_iiko_ids")
    if not isinstance(raw, list):
        return set()
    return {str(x).strip().lower() for x in raw if str(x).strip()}


def _norm_item_name(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _norm_tag_token(s: str) -> str:
    t = (s or "").strip().lower()
    t = re.sub(r"\s+", "_", t)
    t = re.sub(r"[^\w\-]+", "", t, flags=re.UNICODE)
    return t


def parse_menu_tags(tags: str | None) -> set[str]:
    """Токены тегов меню: «острое, drink» → {'острое','drink'} с нормализацией латиницы."""
    if not tags or not str(tags).strip():
        return set()
    out: set[str] = set()
    for part in str(tags).split(","):
        raw = part.strip()
        if not raw:
            continue
        out.add(_norm_tag_token(raw))
        # Латиница для макросов админки: spicy, main_course, …
        ascii_t = _norm_tag_token(re.sub(r"[^a-zA-Z0-9_\-]+", "_", raw))
        if ascii_t:
            out.add(ascii_t)
    return {x for x in out if x}


def menu_by_iiko_lower(menu_items: Sequence[Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for m in menu_items:
        iid = str(getattr(m, "iiko_id", None) or "").strip().lower()
        if iid:
            out[iid] = m
    return out


def collect_cart_tag_profile(
    cart_items: list[dict[str, Any]],
    menu_items: Sequence[Any],
) -> tuple[set[str], bool, bool]:
    """
    (union_tags, cart_has_side_dish_tag, cart_has_drink_tag)
    Теги берутся из строк меню по iiko_id позиций корзины.
    """
    by_iiko = menu_by_iiko_lower(menu_items)
    union: set[str] = set()
    has_side = False
    has_drink_tag = False
    for it in cart_items:
        if not isinstance(it, dict):
            continue
        iid = str(it.get("iiko_id") or "").strip().lower()
        m = by_iiko.get(iid) if iid else None
        if m is None:
            continue
        tags = parse_menu_tags(getattr(m, "tags", None))
        union |= tags
        if "side_dish" in tags:
            has_side = True
        if "drink" in tags:
            has_drink_tag = True
    return union, has_side, has_drink_tag


def upsell_rejection_ids_in_cooldown(
    user_meta: Mapping[str, Any] | None,
    *,
    cooldown_hours: float = 48.0,
    now: datetime | None = None,
) -> set[str]:
    """UUID iiko, от которых гость отказался менее cooldown_hours назад (по User.meta_json)."""
    if not user_meta or not isinstance(user_meta, dict):
        return set()
    raw = user_meta.get(USER_META_UPSELL_REJECTS_KEY)
    if not isinstance(raw, dict):
        return set()
    now_utc = now or datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    cutoff = now_utc - timedelta(hours=cooldown_hours)
    blocked: set[str] = set()
    for k, v in raw.items():
        iid = str(k).strip().lower()
        if not iid:
            continue
        ts: datetime | None = None
        if isinstance(v, str) and v.strip():
            try:
                raw_ts = v.strip().replace("Z", "+00:00")
                ts = datetime.fromisoformat(raw_ts)
            except ValueError:
                ts = None
        elif isinstance(v, (int, float)):
            ts = datetime.fromtimestamp(float(v), tz=timezone.utc)
        if ts is None:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts >= cutoff:
            blocked.add(iid)
    return blocked


def _dt_as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _sql_dt_for_filter(dt: datetime) -> datetime:
    u = _dt_as_utc(dt)
    if settings.db_mode == "sqlite":
        return u.replace(tzinfo=None)
    return u


def _norm_offered_iiko_ids(values: Sequence[Any]) -> set[str]:
    out: set[str] = set()
    for raw in values:
        iid = str(raw or "").strip().lower()
        if iid:
            out.add(iid)
    return out


async def recently_rejected_iiko_ids(
    db: AsyncSession,
    org_id: int,
    user_id: int,
    *,
    days: float = 7,
) -> set[str]:
    """iiko_id, от которых гость отказался в UpsellOfferEvent за последние N дней."""
    cutoff = _sql_dt_for_filter(datetime.now(timezone.utc) - timedelta(days=float(days)))
    res = await db.execute(
        select(UpsellOfferEvent.offered_item_id).where(
            UpsellOfferEvent.organization_id == int(org_id),
            UpsellOfferEvent.user_id == int(user_id),
            UpsellOfferEvent.status == UPSELL_EVENT_STATUS_REJECTED,
            UpsellOfferEvent.created_at >= cutoff,
        ),
    )
    return _norm_offered_iiko_ids(res.scalars().all())


async def recently_offered_iiko_ids(
    db: AsyncSession,
    org_id: int,
    user_id: int,
    *,
    order_id: int | None = None,
    days: float = _DEFAULT_CROSS_ORDER_OFFER_DAYS,
) -> set[str]:
    """
    iiko_id, которые уже предлагали гостю (status=shown и финальные статусы).

    С order_id — только в рамках текущего заказа; без — за последние ``days`` дней.
    """
    q = select(UpsellOfferEvent.offered_item_id).where(
        UpsellOfferEvent.organization_id == int(org_id),
        UpsellOfferEvent.user_id == int(user_id),
        UpsellOfferEvent.offered_item_id.is_not(None),
    )
    if order_id is not None:
        q = q.where(UpsellOfferEvent.order_id == int(order_id))
    else:
        cutoff = _sql_dt_for_filter(datetime.now(timezone.utc) - timedelta(days=float(days)))
        q = q.where(UpsellOfferEvent.created_at >= cutoff)
    res = await db.execute(q)
    return _norm_offered_iiko_ids(res.scalars().all())


async def max_one_upsell_per_order(db: AsyncSession, order_id: int) -> bool:
    """True, если для заказа уже есть upsell-предложение — новый оффер блокируем."""
    if not order_id:
        return False
    res = await db.execute(
        select(UpsellOfferEvent.id).where(
            UpsellOfferEvent.order_id == int(order_id),
            UpsellOfferEvent.status == UPSELL_EVENT_STATUS_SHOWN,
        ).limit(1),
    )
    return res.scalar_one_or_none() is not None


def record_upsell_rejections_on_user(user: Any, rejected_ids: Sequence[str]) -> None:
    """Пишет отказы в user.meta_json[upsell_rejections] (ISO UTC)."""
    when = datetime.now(timezone.utc)
    prev = getattr(user, "meta_json", None)
    meta = dict(prev) if isinstance(prev, dict) else {}
    rej_raw = meta.get(USER_META_UPSELL_REJECTS_KEY)
    rej: dict[str, str] = {}
    if isinstance(rej_raw, dict):
        for rk, rv in rej_raw.items():
            ks = str(rk).strip().lower()
            if ks and isinstance(rv, str):
                rej[ks] = rv
    for rid in rejected_ids:
        ks = str(rid).strip().lower()
        if ks:
            rej[ks] = when.isoformat()
    meta[USER_META_UPSELL_REJECTS_KEY] = rej
    user.meta_json = meta


@dataclass(frozen=True)
class TagPairingResult:
    candidates: list[Any]
    gastro_hint: str


def _menu_row_is_drink_candidate(m: Any) -> bool:
    nm = re.sub(r"\s+", " ", str(getattr(m, "name", "") or "").strip().lower())
    cat = re.sub(r"\s+", " ", str(getattr(m, "category", "") or "").strip().lower())
    drink_name = ("чай", "кофе", "кола", "напит", "сок", "компот", "лимонад", "капучино", "эспрессо", "латте")
    drink_cat = ("напит", "кофе", "чай", "бар", "сок")
    if any(h in nm for h in drink_name):
        return True
    return any(h in cat for h in drink_cat)


def is_forbidden_upsell_candidate(menu_item: Any, *, cart_tags: set[str] | None = None) -> bool:
    """True, если позицию нельзя предлагать как допродажу в текущем контексте."""
    nm = _norm_item_name(str(getattr(menu_item, "name", "") or ""))
    tags = parse_menu_tags(getattr(menu_item, "tags", None))
    if tags & {"not_upsell", "internal"}:
        return True

    # Жёсткий стоп: позиции для завтраков и похожие не предлагать вне явного breakfast-контекста.
    if "only_breakfast" in tags:
        return True

    milk_like = ("молоко", "milk", "кефир")
    is_milk = any(x in nm for x in milk_like) or bool(tags & {"milk", "dairy"})
    if not is_milk:
        return False

    ct = cart_tags or set()
    breakfast_context = bool(ct & {"breakfast", "coffee", "dessert"})
    hot_meat_context = bool(ct & {"main_course", "meat", "heavy", "hot", "плов", "шашлык", "мант"})
    if hot_meat_context and not breakfast_context:
        return True
    return False


def is_preferred_upsell_candidate(menu_item: Any) -> bool:
    """True, если позиция в приоритетном списке для допродаж."""
    nm = _norm_item_name(str(getattr(menu_item, "name", "") or ""))
    tags = parse_menu_tags(getattr(menu_item, "tags", None))
    if tags & {"upsell", "drink", "side_dish", "goes_well_with_meat"}:
        return True
    preferred_tokens = ("чай", "лимонад", "компот", "кола", "лепеш", "лепёш", "лаваш", "ачучук", "ачичук")
    return any(t in nm for t in preferred_tokens)


def find_pairing_by_tag(
    cart_tags: set[str],
    menu_items: Sequence[Any],
    *,
    skip_iiko: set[str],
    skip_names: set[str],
    has_drink_in_cart_heuristic: bool,
    cart_has_side_dish_tag: bool,
) -> TagPairingResult:
    """
    Strategy Engine v2 — гастро-приоритеты по тегам строк меню.
    Правила: spicy → drink; main_course без side_dish → гарнир (goes_well_with_meat / side_dish).
    """
    from app.services.sales_strategy import _find_menu_item  # noqa: PLC0415

    hints: list[str] = []
    picked: list[Any] = []

    spicy_hit = "spicy" in cart_tags or "острое" in cart_tags or "острый" in cart_tags
    has_drink = bool(has_drink_in_cart_heuristic or cart_tags & {"drink", "напиток"})

    if spicy_hit and not has_drink:
        drink_pick: Any | None = None
        for cand in menu_items:
            if not getattr(cand, "is_available", True):
                continue
            iid = str(getattr(cand, "iiko_id", None) or "").strip().lower()
            if iid and iid in skip_iiko:
                continue
            mn = _norm_item_name(str(getattr(cand, "name", "") or ""))
            if mn in skip_names:
                continue
            tset = parse_menu_tags(getattr(cand, "tags", None))
            if "drink" in tset and _menu_row_is_drink_candidate(cand):
                drink_pick = cand
                break
        if drink_pick is None:
            for cand in menu_items:
                if not getattr(cand, "is_available", True):
                    continue
                iid = str(getattr(cand, "iiko_id", None) or "").strip().lower()
                if iid and iid in skip_iiko:
                    continue
                mn = _norm_item_name(str(getattr(cand, "name", "") or ""))
                if mn in skip_names:
                    continue
                tset = parse_menu_tags(getattr(cand, "tags", None))
                if "drink" in tset:
                    drink_pick = cand
                    break
        if drink_pick is None:
            drink_pick = _find_menu_item(
                list(menu_items),
                ("чай", "айран", "компот", "кола", "сок", "лимонад"),
                skip_iiko=skip_iiko,
                skip_names=skip_names,
            )
        if drink_pick is not None:
            picked.append(drink_pick)
            hints.append("[Теги: spicy + drink]")

    main_hit = "main_course" in cart_tags or "основное" in cart_tags
    if main_hit and not cart_has_side_dish_tag:

        def _side_ok(cand: Any) -> bool:
            if not getattr(cand, "is_available", True):
                return False
            iid = str(getattr(cand, "iiko_id", None) or "").strip().lower()
            if iid and iid in skip_iiko:
                return False
            if _norm_item_name(str(getattr(cand, "name", "") or "")) in skip_names:
                return False
            ts = parse_menu_tags(getattr(cand, "tags", None))
            if "goes_well_with_meat" in ts or "side_dish" in ts or "гарнир" in ts:
                return True
            return False

        side_pick: Any | None = None
        for cand in menu_items:
            if not _side_ok(cand):
                continue
            ts = parse_menu_tags(getattr(cand, "tags", None))
            if "goes_well_with_meat" in ts:
                side_pick = cand
                break
        if side_pick is None:
            for cand in menu_items:
                if _side_ok(cand):
                    side_pick = cand
                    break
        if side_pick is not None:
            picked.append(side_pick)
            hints.append("[Теги: main_course + side_dish]")

    seen: set[str] = set()
    uniq: list[Any] = []
    for m in picked:
        iid = str(getattr(m, "iiko_id", None) or "").strip().lower()
        if iid and iid not in seen:
            seen.add(iid)
            uniq.append(m)
    return TagPairingResult(candidates=uniq, gastro_hint=" ".join(hints).strip())

