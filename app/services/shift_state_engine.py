"""G10 v1.2 — deterministic shift control plane over G5–G8.

Semantic contract: docs/G10_SEMANTIC_CONTRACT.md (v1.2 projection diff, focus ownership).
"""

from __future__ import annotations

import logging
import re
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
FOCUS_LEASE_TTL_SEC = 45
S1_LATCH_TTL_SEC = 600
S1_ENTER_RISK_KZT = 10000
S1_EXIT_RISK_KZT = 7000
S1_ENTER_DRAFTS_KZT = 8000
S1_EXIT_DRAFTS_KZT = 6000

KIND_TO_TYPE = {
    "abandoned_draft": "draft",
    "slow_chat": "chat",
    "pending_prepay": "payment",
    "high_value_stuck": "high_value",
}

WEIGHTS = {
    "abandoned_draft": 0.8,
    "pending_prepay": 0.7,
    "slow_chat": 0.5,
    "high_value_stuck": 1.0,
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
        await redis_client.delete(latch_key)
        return base, False
    if _is_s1(i):
        await redis_client.setex(latch_key, S1_LATCH_TTL_SEC, "1")
        return "S1", False
    if await redis_client.get(latch_key) and not _can_exit_s1_latch(i):
        return "S1", True
    await redis_client.delete(latch_key)
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


def _focus_payload(item: dict[str, Any], *, reason: str) -> dict[str, Any]:
    kind = str(item.get("kind") or "")
    actions = item.get("actions") or []
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
        "pulse": item.get("pulse"),
        "actions": actions,
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
        await _clear_active_focus(org_id, op)
        return None, "none"

    leases = await _scan_active_focus_leases(org_id)
    lease_key = _active_focus_key(org_id, op)
    current_id = await redis_client.get(lease_key)
    candidate: dict[str, Any] | None = None
    reason = "highest_priority_score"

    if current_id:
        current_id = str(current_id)
        leased_item = next(
            (it for it in active_items if str(it.get("id") or "") == current_id),
            None,
        )
        if leased_item:
            await redis_client.expire(lease_key, FOCUS_LEASE_TTL_SEC)
            candidate = leased_item
            reason = "active_focus_lease"
        else:
            await _clear_active_focus(org_id, op)
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
        await redis_client.setex(lease_key, FOCUS_LEASE_TTL_SEC, focus_id)
        leases[focus_id] = op
        reason = "highest_priority_score"

    focus_id = str(candidate.get("id") or "")
    ownership = _ownership_for_focus(focus_id, op, leases)
    payload = _focus_payload(candidate, reason=reason)
    payload["priority_score"] = round(item_priority_score(candidate), 2)
    payload["ownership"] = ownership
    return payload, ownership


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


def _shift_actions(*, has_focus: bool, ownership: FocusOwnership) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = [
        {
            "id": "next",
            "label": "Другое дело",
            "hint": "Показать следующую задачу без отказа",
            "type": "shift_action",
            "subtype": "next",
        },
    ]
    if has_focus and ownership != "other":
        actions.append(
            {
                "id": "skip",
                "label": "Не сейчас",
                "hint": "Осознанно отложить эту задачу",
                "type": "shift_action",
                "subtype": "skip",
            }
        )
        actions.append(
            {
                "id": "complete",
                "label": "Готово",
                "hint": "Задача выполнена",
                "type": "shift_action",
                "subtype": "complete",
            }
        )
    return actions[:3]


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
            "at_risk_count": int(summary.get("total") or shift_input.queue_size),
            "queue_size": shift_input.queue_size,
            "queue_size_active": len(active_items),
            "shift_empty_focus_while_risk_positive": int(empty_focus_while_risk_positive),
            "excluded_skip": len(skipped),
            "excluded_next": len(next_ids),
            "excluded_done": len(done),
        },
        "actions": _shift_actions(has_focus=focus_item is not None, ownership=ownership),
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
    await emit_event(
        db,
        BusinessEvent(
            org_id=int(org_id),
            type="shift.focus_completed",
            actor="operator",
            entity_type="shift_focus",
            entity_id=fid,
            location_id=location_id,
            payload={"focus_id": fid, "intent": intent, "operator_id": op},
        ),
    )
    await db.commit()
    logger.info(
        "shift_action_applied org_id=%s operator_id=%s subtype=complete focus_id=%s event_emitted=true",
        org_id,
        op,
        fid,
    )
