"""G10 v1.2 — deterministic shift control plane over G5–G8.

Semantic contract: docs/G10_SEMANTIC_CONTRACT.md (v1.2 projection diff, focus ownership).
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import redis_client
from app.services.money_queue import build_money_queue
from app.services.shift_control import _saved_today_kzt
from app.services.system_events import BusinessEvent, emit_event

logger = logging.getLogger(__name__)

QUEUE_PREVIEW_LIMIT = 5
SKIP_TTL_SEC = 600
DONE_TTL_SEC = 3600
LIVE_IMPACT_TTL_SEC = 90
FOCUS_LEASE_TTL_SEC = 45
S1_LATCH_TTL_SEC = 600
S1_ENTER_RISK_KZT = 10000
S1_EXIT_RISK_KZT = 7000
S1_ENTER_DRAFTS_KZT = 8000
S1_EXIT_DRAFTS_KZT = 6000


async def _redis_safe(coro: Awaitable[Any], default: Any) -> Any:
    """Shift plane degrades gracefully when Redis is unavailable (Render cold start / outage)."""
    try:
        return await coro
    except Exception as exc:
        logger.warning("shift redis unavailable: %s", exc)
        return default

KIND_TO_TYPE = {
    "abandoned_draft": "draft",
    "slow_chat": "chat",
    "pending_prepay": "payment",
    "high_value_stuck": "high_value",
    "menu_confusion": "chat",
    "booking_at_risk": "booking",
}

WEIGHTS = {
    "abandoned_draft": 0.8,
    "pending_prepay": 0.7,
    "slow_chat": 0.5,
    "high_value_stuck": 1.0,
    "menu_confusion": 0.45,
    "booking_at_risk": 0.65,
}

WAIT_MIN_CAP = 30
WAIT_MIN_WEIGHT = 50

ShiftSubtype = Literal["next", "skip", "complete", "reset_skips"]
ACTION_INTENT = {
    "next": "advance",
    "skip": "reject",
    "complete": "complete",
    "reset_skips": "reset_skip_memory",
}

EmptyFocusReason = Literal[
    "no_signals",
    "all_filtered",
    "action_queue_cleared",
    "calm_no_action",
]

FocusOwnership = Literal["mine", "other", "unclaimed", "none"]

_CALM_STATES = frozenset({"S0", "S3"})


@dataclass
class ShiftInput:
    risk_kzt: float
    drafts: list[dict[str, Any]]
    pending_payments: list[dict[str, Any]]
    red_chats: list[dict[str, Any]]
    yellow_chats: list[dict[str, Any]]
    high_value: list[dict[str, Any]]
    queue_size: int
    drafts_value_kzt: float


def _normalize_operator_id(operator_id: str | int | None) -> str:
    raw = str(operator_id or "shared").strip() or "shared"
    safe = re.sub(r"[^a-zA-Z0-9_-]", "", raw)[:64]
    return safe or "shared"


def _filter_excluded(
    items: list[dict[str, Any]],
    *,
    excluded: set[str],
) -> list[dict[str, Any]]:
    return [it for it in items if str(it.get("id") or "") not in excluded]


def _is_s1(i: ShiftInput) -> bool:
    return (
        i.risk_kzt > S1_ENTER_RISK_KZT
        or len(i.red_chats) > 0
        or i.drafts_value_kzt > S1_ENTER_DRAFTS_KZT
    )


def _can_exit_s1_latch(i: ShiftInput) -> bool:
    return (
        i.risk_kzt < S1_EXIT_RISK_KZT
        and len(i.red_chats) == 0
        and i.drafts_value_kzt < S1_EXIT_DRAFTS_KZT
    )


def _s1_latch_key(org_id: int) -> str:
    return f"shift:s1_latch:{int(org_id)}"


def resolve_state(i: ShiftInput) -> str:
    if i.queue_size > 25 or i.risk_kzt > 50000:
        return "S5"
    if _is_s1(i):
        return "S1"
    if (len(i.drafts) > 0 or len(i.pending_payments) > 0) and not _is_s1(i):
        return "S4"
    if len(i.yellow_chats) > 0 or i.queue_size > 5:
        return "S2"
    if i.queue_size <= 5 and i.risk_kzt < 3000:
        return "S3"
    return "S0"


async def resolve_state_effective(org_id: int, i: ShiftInput) -> tuple[str, bool]:
    """Apply S1 hysteresis latch (enter >10k, exit <7k + no red + drafts calm)."""
    base = resolve_state(i)
    latch_key = _s1_latch_key(org_id)
    if base == "S5":
        await _redis_safe(redis_client.delete(latch_key), None)
        return base, False
    if _is_s1(i):
        await _redis_safe(redis_client.setex(latch_key, S1_LATCH_TTL_SEC, "1"), None)
        return "S1", False
    if await _redis_safe(redis_client.get(latch_key), None) and not _can_exit_s1_latch(i):
        return "S1", True
    await _redis_safe(redis_client.delete(latch_key), None)
    return base, False


def derive_state_reason(inp: ShiftInput, state: str, *, s1_latched: bool = False) -> str:
    if state == "S5":
        if inp.queue_size > 25:
            return "queue_spike"
        return "extreme_risk_kzt"
    if state == "S1":
        if s1_latched:
            return "s1_hysteresis_latched"
        if len(inp.red_chats) > 0:
            return "red_chat_exists"
        if inp.drafts_value_kzt > 8000:
            return "high_draft_value"
        if inp.risk_kzt > 10000:
            return "high_risk_kzt"
        return "critical_risk"
    if state == "S4":
        if len(inp.pending_payments) > 0 and len(inp.drafts) > 0:
            return "drafts_and_pending"
        if len(inp.pending_payments) > 0:
            return "pending_prepay_exists"
        return "abandoned_drafts_exist"
    if state == "S2":
        if len(inp.yellow_chats) > 0:
            return "slow_chats_yellow"
        return "queue_busy"
    if state == "S3":
        return "calm_low_risk"
    if state == "S0":
        return "idle_fallback"
    return "unknown"


def compute_projection_gap(
    *,
    state: str,
    shift_input: ShiftInput,
    all_items: list[dict[str, Any]],
    active_items: list[dict[str, Any]],
    has_focus: bool,
    excluded_count: int,
) -> bool:
    if not all_items:
        return False
    if len(active_items) < len(all_items):
        return True
    if state in ("S1", "S4", "S5") and not has_focus:
        return True
    if state in ("S1", "S2", "S4") and excluded_count > 0 and not has_focus:
        return True
    if state in ("S1", "S2", "S4", "S5") and has_focus and len(active_items) == 0:
        return True
    return False


def item_priority_score(item: dict[str, Any]) -> float:
    amount = float(item.get("amount_kzt") or 0)
    wait_min = int(item.get("wait_minutes") or 0)
    kind = str(item.get("kind") or "")
    w = WEIGHTS.get(kind, 0.5)
    return (amount * w) + min(wait_min, WAIT_MIN_CAP) * WAIT_MIN_WEIGHT


def select_focus(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not items:
        return None
    best = max(items, key=item_priority_score)
    payload = _focus_payload(best, reason="highest_priority_score")
    payload["priority_score"] = round(item_priority_score(best), 2)
    return payload


def derive_focus_why(item: dict[str, Any]) -> tuple[str, str, float]:
    """Deterministic why-this-card hints (no LLM). Returns (why_this_card, ai_hint, confidence)."""
    kind = str(item.get("kind") or "")
    wait_min = int(item.get("wait_minutes") or 0)
    pulse = str(item.get("pulse") or "")
    amount = float(item.get("amount_kzt") or 0)

    confidence = 0.75
    if wait_min >= 10:
        confidence = min(0.92, 0.75 + wait_min * 0.01)
    if pulse == "red":
        confidence = max(confidence, 0.88)
    elif pulse == "amber":
        confidence = max(confidence, 0.82)
    if amount > 5000:
        confidence = max(confidence, 0.85)
    confidence = round(min(0.95, confidence), 2)

    why_by_kind: dict[str, str] = {
        "abandoned_draft": "Черновик без подтверждения — высокий риск потери заказа",
        "pending_prepay": "Ожидание предоплаты — клиент может уйти без оплаты",
        "slow_chat": (
            f"Клиент ждёт ответ уже {wait_min} мин — риск ухода"
            if wait_min > 0
            else "Долгое ожидание ответа — клиент может уйти"
        ),
        "menu_confusion": "Повторные вопросы по меню — высокий риск отказа",
        "booking_at_risk": "Бронь без подтверждения — риск no-show",
        "high_value_stuck": "Крупный заказ застрял — нужно вмешательство оператора",
    }
    hint_by_kind: dict[str, str] = {
        "abandoned_draft": "Клиент начал заказ, но не подтвердил — вероятность возврата падает с каждой минутой",
        "pending_prepay": "Ссылка на оплату отправлена — без подтверждения сумма уходит в риск",
        "slow_chat": "Без быстрого ответа гость часто уходит к конкурентам",
        "menu_confusion": "Клиент не нашёл блюдо — высокая вероятность отказа от заказа",
        "booking_at_risk": "Не подтверждённая бронь часто превращается в пустой стол",
        "high_value_stuck": "Сумма заказа выше среднего — задержка бьёт по выручке сильнее",
    }
    why = why_by_kind.get(kind, "Система выбрала задачу с наибольшим влиянием на выручку")
    hint = hint_by_kind.get(kind, "Приоритет по сумме риска и времени ожидания")
    return why, hint, confidence


def _kind_from_focus_id(focus_id: str) -> str:
    prefix = str(focus_id or "").split(":", 1)[0].strip().lower()
    return {
        "draft": "abandoned_draft",
        "prepay": "pending_prepay",
        "chat": "slow_chat",
        "menu": "menu_confusion",
        "booking": "booking_at_risk",
        "high": "high_value_stuck",
    }.get(prefix, "")


def derive_focus_anticipation(item: dict[str, Any]) -> dict[str, Any]:
    """Predictive layer — tension before operator action (no LLM)."""
    kind = str(item.get("kind") or "")
    wait_min = int(item.get("wait_minutes") or 0)
    pulse = str(item.get("pulse") or "")
    amount = float(item.get("amount_kzt") or 0)

    if pulse == "red" or wait_min >= 15:
        tension_level = "imminent"
    elif pulse == "amber" or wait_min >= 8:
        tension_level = "critical"
    elif wait_min >= 4 or amount >= 5000:
        tension_level = "rising"
    else:
        tension_level = "stable"

    risk_trajectory = "rising" if tension_level in ("rising", "critical", "imminent") else "stable"

    anticipation_by_kind: dict[str, str] = {
        "abandoned_draft": "Клиент почти потерян — заказ не подтверждён",
        "pending_prepay": "Оплата зависла — клиент может уйти",
        "slow_chat": (
            f"Клиент ждёт уже {wait_min} мин — почти ушёл"
            if wait_min >= 5
            else "Клиент ждёт ответа — риск растёт"
        ),
        "menu_confusion": "Клиент не понял меню — на грани отказа",
        "booking_at_risk": "Стол под риском no-show",
        "high_value_stuck": "Крупный заказ застрял — потери неизбежны",
    }
    inevitability_by_kind: dict[str, str] = {
        "abandoned_draft": "Без подтверждения заказ уйдёт в потери",
        "pending_prepay": "Без оплаты сумма перейдёт в упущенную выручку",
        "slow_chat": "Каждая минута без ответа снижает шанс возврата",
        "menu_confusion": "Без помощи клиент часто не заказывает",
        "booking_at_risk": "Неподтверждённая бронь часто не приходит",
        "high_value_stuck": "Задержка по крупному заказу бьёт по выручке",
    }
    prefix_by_kind: dict[str, str] = {
        "abandoned_draft": "Клиент уже почти ушёл…",
        "pending_prepay": "Оплата на грани срыва…",
        "slow_chat": "Клиент уже почти ушёл…",
        "menu_confusion": "Клиент был на грани отказа…",
        "booking_at_risk": "Стол почти потерян…",
        "high_value_stuck": "Крупная сумма была под угрозой…",
    }

    anticipation_text = anticipation_by_kind.get(
        kind,
        "Система видит растущий риск по этой задаче",
    )
    if tension_level == "imminent" and kind == "slow_chat":
        anticipation_text = f"Клиент почти ушёл — {wait_min} мин без ответа"
    elif tension_level == "imminent":
        anticipation_text = anticipation_text.replace("почти", "уже почти")

    return {
        "tension_level": tension_level,
        "risk_trajectory": risk_trajectory,
        "anticipation_text": anticipation_text,
        "inevitability_text": inevitability_by_kind.get(
            kind,
            "Без действия риск перейдёт в реальные потери",
        ),
        "predictive_prefix": prefix_by_kind.get(kind, "Риск был высок…"),
        "pre_attention": tension_level in ("rising", "critical", "imminent"),
    }


def _format_money_only(amount_kzt: float) -> str:
    n = int(round(amount_kzt))
    if n <= 0:
        return ""
    formatted = f"{n:,}".replace(",", " ")
    return f"+{formatted} ₸"


def build_live_impact_payload(
    *,
    last_action: str,
    kind: str = "",
    amount_kzt: float = 0,
    wait_minutes: int = 0,
    pulse: str = "",
) -> dict[str, Any]:
    """Compressed predictive outcome for live_impact strip."""
    item_ctx = {
        "kind": kind,
        "wait_minutes": wait_minutes,
        "pulse": pulse,
        "amount_kzt": amount_kzt,
    }
    anticipation = derive_focus_anticipation(item_ctx)

    outcome_emotion_by_kind: dict[str, str] = {
        "abandoned_draft": "Вернули клиента",
        "pending_prepay": "Оплата закрыта",
        "slow_chat": "Вернули клиента",
        "menu_confusion": "Помогли с меню",
        "booking_at_risk": "Бронь спасена",
        "high_value_stuck": "Заказ разблокирован",
    }
    outcome_emotion_by_action: dict[str, str] = {
        "focus_skipped": "Отложили — следующая задача",
        "focus_completed": outcome_emotion_by_kind.get(kind, "Готово"),
    }

    if last_action == "focus_skipped":
        return {
            "last_action": last_action,
            "kind": kind or None,
            "outcome_prefix": anticipation.get("predictive_prefix") or "",
            "outcome_emotion": outcome_emotion_by_action["focus_skipped"],
            "impact_money": "",
            "impact_text": "Отложено",
            "impact_reason": "Следующая задача в очереди",
            "narrative_compressed": True,
            "amount_kzt": 0,
            "animation": "fade_shrink",
        }

    money = _format_money_only(amount_kzt)
    emotion = outcome_emotion_by_kind.get(kind, "Готово")
    prefix = str(anticipation.get("predictive_prefix") or "")

    return {
        "last_action": last_action,
        "kind": kind or None,
        "outcome_prefix": prefix,
        "outcome_emotion": emotion,
        "impact_money": money,
        "impact_text": _format_saved_impact_text(amount_kzt),
        "impact_reason": _impact_reason_for_kind(kind),
        "narrative_compressed": True,
        "amount_kzt": round(float(amount_kzt or 0), 2),
        "animation": "pulse_green",
    }


def _impact_reason_for_kind(kind: str) -> str:
    return {
        "abandoned_draft": "Клиент возвращён в воронку заказа",
        "pending_prepay": "Предоплата обработана",
        "slow_chat": "Клиент получил ответ",
        "menu_confusion": "Вопрос по меню закрыт",
        "booking_at_risk": "Бронь подтверждена",
        "high_value_stuck": "Крупный заказ разблокирован",
    }.get(kind, "Задача выполнена")


def _format_saved_impact_text(amount_kzt: float) -> str:
    n = int(round(amount_kzt))
    if n <= 0:
        return "Задача закрыта"
    formatted = f"{n:,}".replace(",", " ")
    return f"+{formatted} ₸ спасено"


def _live_impact_key(org_id: int, operator_id: str) -> str:
    return f"shift:live_impact:{int(org_id)}:{_normalize_operator_id(operator_id)}"


async def _store_live_impact(org_id: int, operator_id: str, payload: dict[str, Any]) -> None:
    key = _live_impact_key(org_id, operator_id)
    await _redis_safe(
        redis_client.setex(key, LIVE_IMPACT_TTL_SEC, json.dumps(payload, ensure_ascii=False)),
        None,
    )


async def _load_live_impact(org_id: int, operator_id: str) -> dict[str, Any] | None:
    raw = await _redis_safe(redis_client.get(_live_impact_key(org_id, operator_id)), None)
    if not raw:
        return None
    try:
        text = raw.decode() if isinstance(raw, bytes) else str(raw)
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def _focus_payload(item: dict[str, Any], *, reason: str) -> dict[str, Any]:
    kind = str(item.get("kind") or "")
    actions = item.get("actions") or []
    why, ai_hint, confidence = derive_focus_why(item)
    anticipation = derive_focus_anticipation(item)
    return {
        "type": KIND_TO_TYPE.get(kind, kind or "unknown"),
        "id": str(item.get("id") or ""),
        "kind": kind,
        "title": item.get("title"),
        "subtitle": item.get("subtitle"),
        "value_kzt": round(float(item.get("amount_kzt") or 0), 2),
        "wait_minutes": int(item.get("wait_minutes") or 0),
        "reason": reason,
        "phone": item.get("phone"),
        "order_id": item.get("order_id"),
        "booking_id": item.get("booking_id"),
        "pulse": item.get("pulse"),
        "actions": actions,
        "why_this_card": why,
        "ai_hint": ai_hint,
        "confidence": confidence,
        "anticipation": anticipation,
    }


def _split_queue_items(items: list[dict[str, Any]]) -> ShiftInput:
    drafts = [it for it in items if it.get("kind") == "abandoned_draft"]
    pending = [it for it in items if it.get("kind") == "pending_prepay"]
    high_value = [it for it in items if it.get("kind") == "high_value_stuck"]
    red_chats = [
        it for it in items if it.get("kind") == "slow_chat" and str(it.get("pulse") or "") == "red"
    ]
    yellow_chats = [
        it for it in items if it.get("kind") == "slow_chat" and str(it.get("pulse") or "") == "amber"
    ]
    drafts_value = round(sum(float(d.get("amount_kzt") or 0) for d in drafts), 2)
    risk_kzt = round(
        sum(float(it.get("amount_kzt") or 0) for it in items if float(it.get("amount_kzt") or 0) > 0),
        2,
    )
    return ShiftInput(
        risk_kzt=risk_kzt,
        drafts=drafts,
        pending_payments=pending,
        red_chats=red_chats,
        yellow_chats=yellow_chats,
        high_value=high_value,
        queue_size=len(items),
        drafts_value_kzt=drafts_value,
    )


def _skip_set_key(org_id: int) -> str:
    return f"shift:skip_set:{int(org_id)}"


def _next_set_key(org_id: int) -> str:
    return f"shift:next_set:{int(org_id)}"


def _done_set_key(org_id: int) -> str:
    return f"shift:done_set:{int(org_id)}"


def _active_focus_key(org_id: int, operator_id: str) -> str:
    return f"shift:active_focus:{int(org_id)}:{_normalize_operator_id(operator_id)}"


async def _scan_active_focus_leases(org_id: int) -> dict[str, str]:
    """focus_id -> operator_id for all live leases in org."""
    base = f"shift:active_focus:{int(org_id)}:"
    out: dict[str, str] = {}
    cursor = 0
    while True:
        cursor, keys = await redis_client.scan(cursor, match=f"{base}*", count=64)
        for raw in keys or []:
            key = raw.decode() if isinstance(raw, bytes) else str(raw)
            if not key.startswith(base):
                continue
            op = key[len(base) :]
            fid = await redis_client.get(key)
            if fid:
                out[str(fid)] = op
        if cursor == 0:
            break
    return out


async def _clear_active_focus(org_id: int, operator_id: str) -> None:
    await redis_client.delete(_active_focus_key(org_id, operator_id))


async def _smembers_ids(key: str) -> set[str]:
    raw = await redis_client.smembers(key)
    if not raw:
        return set()
    out: set[str] = set()
    for m in raw:
        out.add(m.decode() if isinstance(m, bytes) else str(m))
    return out


async def _srem_member(set_key: str, member: str) -> None:
    await redis_client.srem(set_key, member)


async def _scan_focus_ids(org_id: int, prefix: str) -> set[str]:
    base = f"{prefix}{int(org_id)}:"
    found: set[str] = set()
    cursor = 0
    while True:
        cursor, keys = await redis_client.scan(cursor, match=f"{base}*", count=64)
        for raw in keys or []:
            key = raw.decode() if isinstance(raw, bytes) else str(raw)
            if key.startswith(base):
                found.add(key[len(base) :])
        if cursor == 0:
            break
    return found


async def _prune_exclusion_set(org_id: int, set_key: str, item_key_prefix: str) -> set[str]:
    """Keep SET index consistent with per-item TTL keys (drop ghost members)."""
    members = await _smembers_ids(set_key)
    if not members:
        return set()
    oid = int(org_id)
    valid: set[str] = set()
    for fid in members:
        if await redis_client.get(f"{item_key_prefix}{oid}:{fid}") is not None:
            valid.add(fid)
        else:
            await _srem_member(set_key, fid)
    return valid


async def _load_excluded(org_id: int) -> tuple[set[str], set[str], set[str], set[str]]:
    try:
        oid = int(org_id)
        skipped = await _prune_exclusion_set(oid, _skip_set_key(oid), "shift:skip:")
        next_ids = await _prune_exclusion_set(oid, _next_set_key(oid), "shift:next:")
        done = await _prune_exclusion_set(oid, _done_set_key(oid), "shift:done:")

        if not skipped:
            skipped = await _scan_focus_ids(oid, "shift:skip:")
        if not next_ids:
            next_ids = await _scan_focus_ids(oid, "shift:next:")
        if not done:
            done = await _scan_focus_ids(oid, "shift:done:")

        excluded = skipped | next_ids | done
        return excluded, skipped, next_ids, done
    except Exception as exc:
        logger.warning("shift exclusion load failed org=%s: %s", org_id, exc)
        empty: set[str] = set()
        return empty, empty, empty, empty


async def _register_exclusion(
    org_id: int,
    focus_id: str,
    *,
    subtype: ShiftSubtype,
) -> None:
    oid = int(org_id)
    if subtype == "skip":
        await redis_client.setex(f"shift:skip:{oid}:{focus_id}", SKIP_TTL_SEC, "1")
        await redis_client.sadd(_skip_set_key(oid), focus_id)
    elif subtype == "next":
        await redis_client.setex(f"shift:next:{oid}:{focus_id}", SKIP_TTL_SEC, "1")
        await redis_client.sadd(_next_set_key(oid), focus_id)


async def reset_shift_skip_exclusions(org_id: int) -> int:
    """Clear operator skip/next memory for an org; completed items stay excluded."""
    oid = int(org_id)
    cleared = 0
    for set_key, prefix in (
        (_skip_set_key(oid), "shift:skip:"),
        (_next_set_key(oid), "shift:next:"),
    ):
        members = await _smembers_ids(set_key)
        scanned = await _scan_focus_ids(oid, prefix)
        for fid in members | scanned:
            await redis_client.delete(f"{prefix}{oid}:{fid}")
            cleared += 1
        await redis_client.delete(set_key)
    return cleared


def _ownership_for_focus(
    focus_id: str,
    operator_id: str,
    leases: dict[str, str],
) -> FocusOwnership:
    holder = leases.get(focus_id)
    if not holder:
        return "unclaimed"
    if holder == _normalize_operator_id(operator_id):
        return "mine"
    return "other"


async def renew_focus_claim(
    org_id: int,
    focus_id: str,
    operator_id: str | int | None,
    *,
    owner_token: str | None = None,
) -> tuple[bool, str | None]:
    """Extend operator focus lease (heartbeat). owner_token ignored (simplified lease)."""
    del owner_token
    fid = str(focus_id or "").strip()
    if not fid:
        return False, None
    op = _normalize_operator_id(operator_id)
    key = _active_focus_key(org_id, op)
    current = await redis_client.get(key)
    if current is None or str(current) != fid:
        return False, None
    await redis_client.expire(key, FOCUS_LEASE_TTL_SEC)
    logger.info(
        "shift.focus_heartbeat_renewed org_id=%s operator_id=%s focus_id=%s",
        org_id,
        op,
        fid,
    )
    return True, None


async def release_focus_claim(
    org_id: int,
    focus_id: str,
    operator_id: str | int | None,
    *,
    owner_token: str | None = None,
) -> bool:
    """Drop operator lease when leaving shift tab."""
    del owner_token, focus_id
    op = _normalize_operator_id(operator_id)
    await _clear_active_focus(org_id, op)
    logger.info("shift.focus_lease_released org_id=%s operator_id=%s", org_id, op)
    return True


async def _resolve_focus(
    org_id: int,
    active_items: list[dict[str, Any]],
    *,
    operator_id: str,
) -> tuple[dict[str, Any] | None, FocusOwnership]:
    op = _normalize_operator_id(operator_id)
    if not active_items:
        await _redis_safe(_clear_active_focus(org_id, op), None)
        return None, "none"

    try:
        leases = await _scan_active_focus_leases(org_id)
        lease_key = _active_focus_key(org_id, op)
        current_id = await _redis_safe(redis_client.get(lease_key), None)
        candidate: dict[str, Any] | None = None
        reason = "highest_priority_score"

        if current_id:
            current_id = str(current_id)
            leased_item = next(
                (it for it in active_items if str(it.get("id") or "") == current_id),
                None,
            )
            if leased_item:
                await _redis_safe(redis_client.expire(lease_key, FOCUS_LEASE_TTL_SEC), None)
                candidate = leased_item
                reason = "active_focus_lease"
            else:
                await _redis_safe(_clear_active_focus(org_id, op), None)
                leases = await _scan_active_focus_leases(org_id)

        if candidate is None:
            busy_ids = set(leases.keys())
            pool = [
                it for it in active_items if str(it.get("id") or "") not in busy_ids
            ]
            picked = select_focus(pool) if pool else None
            if not picked:
                return None, "none"
            candidate = next(
                (it for it in active_items if str(it.get("id") or "") == str(picked.get("id") or "")),
                None,
            )
            if candidate is None:
                return None, "none"
            focus_id = str(candidate.get("id") or "")
            await _redis_safe(
                redis_client.setex(lease_key, FOCUS_LEASE_TTL_SEC, focus_id),
                None,
            )
            leases[focus_id] = op
            reason = "highest_priority_score"

        focus_id = str(candidate.get("id") or "")
        ownership = _ownership_for_focus(focus_id, op, leases)
        payload = _focus_payload(candidate, reason=reason)
        payload["priority_score"] = round(item_priority_score(candidate), 2)
        payload["ownership"] = ownership
        return payload, ownership
    except Exception as exc:
        logger.warning("shift focus resolve failed org=%s: %s", org_id, exc)
        picked = select_focus(active_items)
        if not picked:
            return None, "none"
        payload = _focus_payload(picked, reason="highest_priority_score")
        payload["priority_score"] = round(item_priority_score(picked), 2)
        payload["ownership"] = "unclaimed"
        return payload, "unclaimed"


def _empty_focus_reason(
    *,
    state: str,
    all_items: list[dict[str, Any]],
    active_items: list[dict[str, Any]],
    has_focus: bool,
) -> EmptyFocusReason | None:
    if has_focus:
        return None
    if not all_items:
        return "no_signals"
    if not active_items:
        return "all_filtered"
    if state in ("S1", "S2", "S4", "S5"):
        return "action_queue_cleared"
    return "calm_no_action"


def _ui_may_show_calm_empty(*, state: str, empty_focus_reason: EmptyFocusReason | None) -> bool:
    """Hard UI invariant: empty focus ≠ calm unless S0/S3."""
    if empty_focus_reason is None:
        return False
    return state in _CALM_STATES and empty_focus_reason == "calm_no_action"


def _build_predictive_scene(
    focus_item: dict[str, Any] | None,
    *,
    state: str,
    risk_kzt: float,
) -> dict[str, Any]:
    if not focus_item:
        return {
            "active": False,
            "tension_level": "stable",
            "risk_trajectory": "stable",
            "scene_headline": "",
        }
    ant = focus_item.get("anticipation") or {}
    level = str(ant.get("tension_level") or "stable")
    headline = str(ant.get("anticipation_text") or "")
    if state in ("S1", "S5") and risk_kzt > 0:
        headline = headline or "Система фиксирует критический риск потерь"
    return {
        "active": bool(ant.get("pre_attention")),
        "tension_level": level,
        "risk_trajectory": str(ant.get("risk_trajectory") or "stable"),
        "scene_headline": headline,
        "inevitability": str(ant.get("inevitability_text") or ""),
    }


async def _focus_context_for_impact(
    db: AsyncSession,
    org_id: int,
    focus_id: str,
) -> dict[str, Any]:
    from app.services.money_queue import build_money_queue

    fid = str(focus_id or "").strip()
    try:
        money = await build_money_queue(db, org_id)
        for it in money.get("items") or []:
            if str(it.get("id") or "") == fid:
                return {
                    "kind": str(it.get("kind") or _kind_from_focus_id(fid)),
                    "wait_minutes": int(it.get("wait_minutes") or 0),
                    "pulse": str(it.get("pulse") or ""),
                    "amount_kzt": float(it.get("amount_kzt") or 0),
                }
    except Exception as exc:
        logger.warning("focus_context_for_impact failed org=%s fid=%s: %s", org_id, fid, exc)
    return {
        "kind": _kind_from_focus_id(fid),
        "wait_minutes": 0,
        "pulse": "",
        "amount_kzt": 0,
    }


def _shift_actions(*, has_focus: bool, ownership: FocusOwnership) -> list[dict[str, Any]]:
    if not has_focus:
        return [
            {
                "id": "next",
                "label": "Другое дело",
                "hint": "Показать следующую задачу без отказа",
                "type": "shift_action",
                "subtype": "next",
            },
        ]
    actions: list[dict[str, Any]] = []
    if ownership != "other":
        actions.extend(
            [
                {
                    "id": "complete",
                    "label": "Готово",
                    "hint": "Задача выполнена",
                    "type": "shift_action",
                    "subtype": "complete",
                },
                {
                    "id": "skip",
                    "label": "Не сейчас",
                    "hint": "Осознанно отложить эту задачу",
                    "type": "shift_action",
                    "subtype": "skip",
                },
            ]
        )
    actions.append(
        {
            "id": "next",
            "label": "Другое дело",
            "hint": "Показать следующую задачу без отказа",
            "type": "shift_action",
            "subtype": "next",
        }
    )
    return actions[:3]


def _build_compressed_actions(
    focus: dict[str, Any] | None,
    *,
    has_focus: bool,
    ownership: FocusOwnership,
) -> dict[str, Any | None]:
    tertiary: dict[str, Any] = {
        "label": "Другое дело",
        "type": "shift_action",
        "subtype": "next",
        "role": "tertiary",
    }
    if not has_focus or ownership == "other" or not focus:
        return {"primary": None, "secondary": None, "tertiary": tertiary}
    focus_actions = list(focus.get("actions") or [])
    if focus_actions:
        primary = dict(focus_actions[0])
        primary["role"] = "primary"
    else:
        primary = {
            "label": "Готово",
            "type": "shift_action",
            "subtype": "complete",
            "role": "primary",
        }
    secondary: dict[str, Any] = {
        "label": "Не сейчас",
        "type": "shift_action",
        "subtype": "skip",
        "role": "secondary",
    }
    return {"primary": primary, "secondary": secondary, "tertiary": tertiary}


def _build_presentation(
    *,
    state: str,
    shift_input: ShiftInput,
    all_items: list[dict[str, Any]],
    active_items: list[dict[str, Any]],
    has_focus: bool,
    empty_reason: EmptyFocusReason | None,
    projection_gap: bool,
    state_reason: str,
    ownership: FocusOwnership,
    operator_id: str,
    skipped: int,
    next_ids: int,
    done: int,
    s1_latched: bool = False,
    active_shift_input: ShiftInput | None = None,
) -> dict[str, Any]:
    active_inp = active_shift_input or shift_input
    return {
        "system_state": state,
        "state_reason": state_reason,
        "operational_focus": has_focus,
        "empty_focus_reason": empty_reason,
        "projection_gap": projection_gap,
        "ui_may_show_calm_empty": _ui_may_show_calm_empty(
            state=state,
            empty_focus_reason=empty_reason,
        ),
        "ui_invariant": "never_infer_calm_from_empty_queue_unless_S0_S3",
        "focus_ownership": ownership,
        "operator_id": _normalize_operator_id(operator_id),
        "debug_trace": {
            "state_reason": state_reason,
            "projection_gap": projection_gap,
            "queue_total": shift_input.queue_size,
            "queue_active": len(active_items),
            "active_risk_kzt": active_inp.risk_kzt,
            "excluded_skip": skipped,
            "excluded_next": next_ids,
            "excluded_done": done,
            "red_chats": len(shift_input.red_chats),
            "drafts": len(shift_input.drafts),
            "s1_latched": s1_latched,
        },
    }


async def build_shift_state(
    db: AsyncSession,
    org_id: int,
    *,
    location_id: int | None = None,
    allowed_location_ids: set[int] | None = None,
    operator_id: str | int | None = None,
) -> dict[str, Any]:
    money = await build_money_queue(
        db,
        org_id,
        location_id=location_id,
        allowed_location_ids=allowed_location_ids,
    )
    all_items = list(money.get("items") or [])
    excluded, skipped, next_ids, done = await _load_excluded(org_id)

    shift_input = _split_queue_items(all_items)
    state, s1_latched = await resolve_state_effective(org_id, shift_input)
    state_reason = derive_state_reason(shift_input, state, s1_latched=s1_latched)
    active_items = _filter_excluded(all_items, excluded=excluded)
    active_shift_input = _split_queue_items(active_items)
    focus_item, ownership = await _resolve_focus(
        org_id,
        active_items,
        operator_id=_normalize_operator_id(operator_id),
    )
    focus_id = str(focus_item.get("id") or "") if focus_item else ""
    queue_preview = [
        _focus_payload(it, reason="queue")
        for it in active_items
        if str(it.get("id") or "") != focus_id
    ][:QUEUE_PREVIEW_LIMIT]

    saved_today = await _saved_today_kzt(
        db,
        org_id,
        location_id=location_id,
        allowed_location_ids=allowed_location_ids,
    )
    from app.services.money_recovery import get_recovered_today_kzt

    recovered_stats = await get_recovered_today_kzt(db, org_id)
    recovered_today = float(recovered_stats.get("recovered_kzt") or 0)
    priority = float(focus_item.get("priority_score") or 0) if focus_item else 0.0
    empty_reason = _empty_focus_reason(
        state=state,
        all_items=all_items,
        active_items=active_items,
        has_focus=focus_item is not None,
    )
    excluded_count = len(skipped) + len(next_ids) + len(done)
    projection_gap = compute_projection_gap(
        state=state,
        shift_input=shift_input,
        all_items=all_items,
        active_items=active_items,
        has_focus=focus_item is not None,
        excluded_count=excluded_count,
    )
    summary = money.get("summary") or {}
    op_norm = _normalize_operator_id(operator_id)
    risk_kzt = float(summary.get("money_at_risk_kzt") or shift_input.risk_kzt)
    empty_focus_while_risk_positive = focus_item is None and risk_kzt > 0
    presentation = _build_presentation(
        state=state,
        shift_input=shift_input,
        all_items=all_items,
        active_items=active_items,
        has_focus=focus_item is not None,
        empty_reason=empty_reason,
        projection_gap=projection_gap,
        state_reason=state_reason,
        ownership=ownership,
        operator_id=op_norm,
        skipped=len(skipped),
        next_ids=len(next_ids),
        done=len(done),
        s1_latched=s1_latched,
        active_shift_input=active_shift_input,
    )

    payload = {
        "location_id": location_id,
        "state": state,
        "priority_score": round(priority, 2),
        "focus": focus_item,
        "queue": queue_preview,
        "metrics": {
            "risk_kzt": risk_kzt,
            "active_risk_kzt": active_shift_input.risk_kzt,
            "saved_today_kzt": saved_today,
            "confirmed_revenue_today_kzt": saved_today,
            "recovered_today_kzt": recovered_today,
            "focus_completed_today": int(recovered_stats.get("focus_completed_count") or 0),
            "at_risk_count": int(summary.get("total") or shift_input.queue_size),
            "queue_size": shift_input.queue_size,
            "queue_size_active": len(active_items),
            "shift_empty_focus_while_risk_positive": int(empty_focus_while_risk_positive),
            "excluded_skip": len(skipped),
            "excluded_next": len(next_ids),
            "excluded_done": len(done),
        },
        "actions": _shift_actions(has_focus=focus_item is not None, ownership=ownership),
        "compressed_actions": _build_compressed_actions(
            focus_item,
            has_focus=focus_item is not None,
            ownership=ownership,
        ),
        "live_impact": await _load_live_impact(org_id, op_norm),
        "predictive_scene": _build_predictive_scene(focus_item, state=state, risk_kzt=risk_kzt),
        "presentation": presentation,
    }

    if empty_focus_while_risk_positive:
        logger.warning(
            "shift_empty_focus_while_risk_positive org_id=%s operator_id=%s state=%s "
            "state_reason=%s risk_kzt=%s queue_total=%s queue_active=%s empty_focus_reason=%s",
            org_id,
            op_norm,
            state,
            state_reason,
            round(risk_kzt, 2),
            shift_input.queue_size,
            len(active_items),
            empty_reason,
        )

    logger.info(
        "shift_state_built org_id=%s operator_id=%s state=%s state_reason=%s projection_gap=%s "
        "focus_id=%s focus_reason=%s ownership=%s queue_total=%s queue_active=%s s1_latched=%s",
        org_id,
        op_norm,
        state,
        state_reason,
        projection_gap,
        focus_id or None,
        (focus_item or {}).get("reason"),
        ownership,
        shift_input.queue_size,
        len(active_items),
        s1_latched,
    )
    return payload


async def apply_shift_action(
    db: AsyncSession,
    org_id: int,
    subtype: ShiftSubtype,
    focus_id: str | None,
    *,
    location_id: int | None = None,
    operator_id: str | int | None = None,
) -> None:
    fid = str(focus_id or "").strip()
    intent = ACTION_INTENT.get(subtype, subtype)
    op = _normalize_operator_id(operator_id)

    if subtype == "reset_skips":
        cleared = await reset_shift_skip_exclusions(org_id)
        await _clear_active_focus(org_id, op)
        logger.warning(
            "shift_skip_memory_reset org_id=%s operator_id=%s cleared=%s event_emitted=false",
            org_id,
            op,
            cleared,
        )
        return

    if subtype in ("next", "skip"):
        if not fid:
            return
        await _register_exclusion(org_id, fid, subtype=subtype)
        await _clear_active_focus(org_id, op)
        if subtype == "skip":
            ctx = await _focus_context_for_impact(db, org_id, fid)
            await _store_live_impact(
                org_id,
                op,
                build_live_impact_payload(
                    last_action="focus_skipped",
                    kind=str(ctx.get("kind") or ""),
                    wait_minutes=int(ctx.get("wait_minutes") or 0),
                    pulse=str(ctx.get("pulse") or ""),
                ),
            )
        logger.info(
            "shift_action_applied org_id=%s operator_id=%s subtype=%s intent=%s focus_id=%s event_emitted=false",
            org_id,
            op,
            subtype,
            intent,
            fid,
        )
        return

    if subtype != "complete" or not fid:
        return

    done_key = f"shift:done:{int(org_id)}:{fid}"
    claimed = await redis_client.set(done_key, op, nx=True, ex=DONE_TTL_SEC)
    if not claimed:
        logger.info(
            "shift_action_applied org_id=%s operator_id=%s subtype=complete focus_id=%s "
            "event_emitted=false duplicate=true",
            org_id,
            op,
            fid,
        )
        return

    await redis_client.sadd(_done_set_key(int(org_id)), fid)
    await _clear_active_focus(org_id, op)
    from app.services.money_recovery import resolve_focus_recovery_with_aov

    amount_kzt, focus_kind = await resolve_focus_recovery_with_aov(db, org_id, fid)
    ctx = await _focus_context_for_impact(db, org_id, fid)
    kind = str(focus_kind or ctx.get("kind") or "")
    await _store_live_impact(
        org_id,
        op,
        build_live_impact_payload(
            last_action="focus_completed",
            kind=kind,
            amount_kzt=float(amount_kzt or 0),
            wait_minutes=int(ctx.get("wait_minutes") or 0),
            pulse=str(ctx.get("pulse") or ""),
        ),
    )
    await emit_event(
        db,
        BusinessEvent(
            org_id=int(org_id),
            type="shift.focus_completed",
            actor="operator",
            entity_type="shift_focus",
            entity_id=fid,
            location_id=location_id,
            payload={
                "focus_id": fid,
                "intent": intent,
                "operator_id": op,
                "amount_kzt": amount_kzt,
                "kind": focus_kind,
            },
        ),
    )
    await db.commit()
    logger.info(
        "shift_action_applied org_id=%s operator_id=%s subtype=complete focus_id=%s event_emitted=true",
        org_id,
        op,
        fid,
    )
