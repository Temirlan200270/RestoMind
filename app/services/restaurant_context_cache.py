"""
Кэш «контекста ресторана» для AI: блок времени/расписания (in-process) и опционально строка меню (Redis).

Инвалидация строки меню — ``invalidate_menu_context_cache`` в ``order_logic`` (in-process + Redis TTL).
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

from app.core.config import settings
from app.db.session import redis_client
from app.services.time_context import format_org_current_time_block

logger = logging.getLogger(__name__)

_TIME_BLOCK_TTL_SEC = 45.0
_time_block_cache: dict[int, tuple[str, float, str]] = {}
# org_id -> (rendered_block, monotonic_ts, schedule_fingerprint)


def _schedule_fingerprint(schedule_json: dict[str, Any] | list[Any] | None, tz: str) -> str:
    raw = json.dumps(schedule_json or {}, sort_keys=True, ensure_ascii=False)
    base = f"{tz}|{raw}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:24]


def cached_format_org_current_time_block(
    organization_id: int,
    timezone_name: str | None,
    schedule_json: dict[str, Any] | list[Any] | None,
) -> str:
    """
    Кэширует текст расписания/«сейчас у заведения» на десятки секунд — без лишних вычислений timezone.
    Меняется при смене ``schedule_json`` / ``timezone`` (fingerprint).
    """
    oid = int(organization_id)
    tz = (timezone_name or "").strip() or "Etc/GMT-5"
    fp = _schedule_fingerprint(
        schedule_json if isinstance(schedule_json, (dict, list)) else None,
        tz,
    )
    now_m = time.monotonic()
    hit = _time_block_cache.get(oid)
    if hit:
        text, ts, old_fp = hit
        if old_fp == fp and (now_m - ts) < _TIME_BLOCK_TTL_SEC:
            return text

    rendered = format_org_current_time_block(tz, schedule_json)
    _time_block_cache[oid] = (rendered, now_m, fp)
    return rendered


def invalidate_org_time_block_cache(organization_id: int) -> None:
    """Вызывать при PATCH расписания организации (опционально; TTL всё равно короткий)."""
    _time_block_cache.pop(int(organization_id), None)


async def redis_cached_menu_context_string(org_id: int, build_fn: Any) -> str:
    """
    Если Redis доступен — пробуем взять готовую строку меню (ключ ``rm:menu_ctx:{org_id}``).
    ``build_fn`` — async callable без аргументов, возвращает свежестроку при промахе.
    """
    if not settings.redis_enabled or getattr(settings, "redis_memory_only", False):
        out = await build_fn()
        return str(out)

    key = f"rm:menu_ctx:{int(org_id)}"
    ttl = int(getattr(settings, "restaurant_menu_ctx_redis_ttl_sec", 90))
    try:
        raw = await redis_client.get(key)
        if raw:
            if isinstance(raw, str):
                return raw
            if isinstance(raw, (bytes, bytearray)):
                return raw.decode("utf-8", errors="replace")
            return str(raw)
    except Exception as exc:
        logger.warning("redis menu ctx get org=%s: %s", org_id, exc)

    built = await build_fn()
    try:
        await redis_client.setex(key, max(30, ttl), built)
    except Exception as exc:
        logger.warning("redis menu ctx set org=%s: %s", org_id, exc)
    return built


async def redis_bump_menu_ctx_cache_version(org_id: int) -> None:
    """Лучший-effort сброс Redis-ключа меню (синхронные вызовы invalidator могут не дождаться)."""
    if not settings.redis_enabled or getattr(settings, "redis_memory_only", False):
        return
    key = f"rm:menu_ctx:{int(org_id)}"
    try:
        await redis_client.delete(key)
    except Exception as exc:
        logger.warning("redis menu ctx delete org=%s: %s", org_id, exc)
