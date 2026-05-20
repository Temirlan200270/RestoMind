"""Shared G4 bot SLA / short-mode state.

The webhook pipeline uses the same Redis keys as the admin UI so operators see
the same overload signal that changes the bot prompt.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

SHORT_MODE_THRESHOLD = 3
SLOW_CHAT_TTL_SEC = 600
SLOW_CHAT_SECONDS = 300
PULSE_GREEN_MAX_SEC = 120
PULSE_AMBER_MAX_SEC = 300


def _clean_location_id(location_id: object) -> int | None:
    if location_id is None:
        return None
    try:
        lid = int(location_id)
    except (TypeError, ValueError):
        return None
    return lid if lid > 0 else None


def _loc_part(location_id: int | None = None) -> str:
    lid = _clean_location_id(location_id)
    return f":loc:{lid}" if lid is not None else ""


def org_slow_chats_key(organization_id: int, location_id: int | None = None) -> str:
    return f"org:{int(organization_id)}{_loc_part(location_id)}:slow_chats"


def slow_chat_key(organization_id: int, phone: str, location_id: int | None = None) -> str:
    return f"org:{int(organization_id)}{_loc_part(location_id)}:slow_chat:{(phone or '').strip()}"


def last_msg_key(organization_id: int, phone: str, location_id: int | None = None) -> str:
    return f"last_msg:{int(organization_id)}{_loc_part(location_id)}:{(phone or '').strip()}"


async def _get_int(redis_client: Any, key: str) -> int:
    try:
        return max(0, int(await redis_client.get(key) or 0))
    except Exception:
        return 0


async def _set_int(redis_client: Any, key: str, value: int, ttl: int = SLOW_CHAT_TTL_SEC) -> None:
    value = max(0, int(value))
    try:
        if hasattr(redis_client, "setex"):
            await redis_client.setex(key, ttl, str(value))
        else:
            await redis_client.set(key, str(value), ex=ttl)
    except TypeError:
        await redis_client.set(key, str(value))


async def get_slow_chat_count(redis_client: Any, organization_id: int, location_id: int | None = None) -> int:
    return await _get_int(redis_client, org_slow_chats_key(organization_id, location_id))


async def is_org_in_short_mode(redis_client: Any, organization_id: int, location_id: int | None = None) -> bool:
    return await get_slow_chat_count(redis_client, organization_id, location_id) > SHORT_MODE_THRESHOLD


async def is_chat_slow(redis_client: Any, organization_id: int, phone: str, location_id: int | None = None) -> bool:
    try:
        return bool(await redis_client.get(slow_chat_key(organization_id, phone, location_id)))
    except Exception:
        return False


async def mark_chat_slow_once(
    redis_client: Any,
    organization_id: int,
    phone: str,
    location_id: int | None = None,
) -> bool:
    """Mark chat slow and increment org counter only on first mark."""
    key = slow_chat_key(organization_id, phone, location_id)
    try:
        if await redis_client.get(key):
            return False
        if hasattr(redis_client, "setex"):
            await redis_client.setex(key, SLOW_CHAT_TTL_SEC, "1")
        else:
            await redis_client.set(key, "1", ex=SLOW_CHAT_TTL_SEC)
        count_key = org_slow_chats_key(organization_id, location_id)
        count = await get_slow_chat_count(redis_client, organization_id, location_id)
        await _set_int(redis_client, count_key, count + 1)
        return True
    except TypeError:
        await redis_client.set(key, "1")
        count_key = org_slow_chats_key(organization_id, location_id)
        count = await get_slow_chat_count(redis_client, organization_id, location_id)
        await _set_int(redis_client, count_key, count + 1)
        return True
    except Exception:
        return False


async def clear_chat_slow(
    redis_client: Any,
    organization_id: int,
    phone: str | None = None,
    location_id: int | None = None,
) -> bool:
    """Clear one slow chat flag and decrement org counter if it was present.

    When phone is missing, keep backwards-compatible behavior and decrement the
    aggregate counter once.
    """
    try:
        had_flag = False
        if phone:
            key = slow_chat_key(organization_id, phone, location_id)
            had_flag = bool(await redis_client.get(key))
            if had_flag:
                await redis_client.delete(key)
        else:
            had_flag = True
        if not had_flag:
            return False
        count_key = org_slow_chats_key(organization_id, location_id)
        count = await get_slow_chat_count(redis_client, organization_id, location_id)
        await _set_int(redis_client, count_key, max(0, count - 1))
        return True
    except Exception:
        return False


def wait_seconds_if_user_waiting(
    last_role: str | None,
    last_at: datetime | None,
    *,
    now: datetime | None = None,
) -> int | None:
    """Seconds the guest has waited for a reply since their last message."""
    if str(last_role or "").lower() != "user" or last_at is None:
        return None
    ref = now or datetime.now(timezone.utc)
    ts = last_at if last_at.tzinfo else last_at.replace(tzinfo=timezone.utc)
    return max(0, int((ref.astimezone(timezone.utc) - ts.astimezone(timezone.utc)).total_seconds()))


def pulse_status_for_wait(wait_seconds: int | None) -> str | None:
    """G5 Live Pulse: green <2m, amber 2-5m, red >5m. None when guest is not waiting."""
    if wait_seconds is None:
        return None
    if wait_seconds >= PULSE_AMBER_MAX_SEC:
        return "red"
    if wait_seconds >= PULSE_GREEN_MAX_SEC:
        return "amber"
    return "green"


def chat_live_pulse(
    last_role: str | None,
    last_at: datetime | None,
    *,
    now: datetime | None = None,
    chat_slow: bool = False,
) -> dict[str, Any]:
    wait_sec = wait_seconds_if_user_waiting(last_role, last_at, now=now)
    pulse = pulse_status_for_wait(wait_sec)
    if pulse is None and chat_slow:
        pulse = "red"
        wait_sec = wait_sec if wait_sec is not None else PULSE_AMBER_MAX_SEC
    return {
        "last_role": str(last_role or ""),
        "wait_seconds": wait_sec,
        "pulse": pulse or "green",
    }


def sla_payload(
    *,
    organization_id: int,
    location_id: int | None = None,
    slow_chats: int,
    bot_short_mode: bool,
    phone: str | None = None,
    chat_slow: bool | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "organization_id": int(organization_id),
        "location_id": _clean_location_id(location_id),
        "slow_chats": int(max(0, slow_chats)),
        "bot_short_mode": bool(bot_short_mode),
    }
    if phone:
        payload["phone"] = phone
    if chat_slow is not None:
        payload["chat_slow"] = bool(chat_slow)
        payload["sla_status"] = "red" if chat_slow else ("amber" if bot_short_mode else "green")
    return payload
