"""
Диагностика очереди задач (E5): Redis + ARQ + worker heartbeat.

Эндпоинт `GET /api/admin/system/task-queue-health` строится поверх
``check_task_queue_health()``: проверяем, что Redis отвечает, что
конфигурация ARQ позволяет ставить задачи, и что worker недавно писал
свой ARQ health-key (`<queue_name>:health-check`, TTL ставит сам arq).

Контракт ответа стабильный и согласован со стабом UI в
[`app/static/js/admin-app.js`](app/static/js/admin-app.js):

```json
{
  "redis":  "ok" | "degraded" | "down",
  "arq":    "ok" | "degraded" | "down",
  "worker": "ok" | "degraded" | "down" | "unknown",
  "details": { ... }
}
```

`degraded` = подсистема жива, но в режиме «тонкого льда» (например,
Redis отвечает, но через in-memory заглушку, а не TCP); `down` = совсем
не работает; `unknown` — данных нет (worker никогда не писал heartbeat
или метрика недоступна по объективной причине).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Literal

from app.core.config import settings
from app.db.session import InMemoryRedis, redis_client, redis_pubsub_available
from app.services.task_queue import arq_can_run

logger = logging.getLogger(__name__)

HealthStatus = Literal["ok", "degraded", "down", "unknown"]

# Имя суффикса фиксировано в arq.constants.health_check_key_suffix.
# Дублируем его здесь, чтобы не падать при апгрейде arq, если константа уедет.
_ARQ_HEALTH_KEY_SUFFIX = ":health-check"


def _arq_queue_name() -> str:
    raw = (settings.arq_queue_name or "restomind").strip()
    return raw or "restomind"


def _arq_health_key() -> str:
    return f"{_arq_queue_name()}{_ARQ_HEALTH_KEY_SUFFIX}"


async def _check_redis() -> tuple[HealthStatus, dict[str, Any]]:
    """
    Redis: реальный TCP — `ok`; in-memory fallback (нет внешнего сервера) — `degraded`;
    исключение в `ping()` — `down` с текстом ошибки в `details.redis.error`.
    """
    info: dict[str, Any] = {}
    try:
        pong = await redis_client.ping()
    except Exception as exc:  # pragma: no cover — защита от внезапной ошибки клиента
        info["error"] = str(exc)[:300]
        info["mode"] = "error"
        return "down", info

    if not pong:
        info["mode"] = "no_pong"
        return "down", info

    if isinstance(redis_client, InMemoryRedis) or not redis_pubsub_available():
        info["mode"] = "in_memory"
        info["note"] = "InMemoryRedis fallback (нет TCP Redis)"
        return "degraded", info

    info["mode"] = "tcp"
    return "ok", info


async def _check_arq() -> tuple[HealthStatus, dict[str, Any]]:
    """
    ARQ: «ok» = можем создать pool на тех же `RedisSettings`, что использует
    `task_queue.enqueue_job`. «down» = либо `arq_can_run()=False`, либо
    `create_pool` упал. Без `degraded` (бинарное состояние: можем enqueue или нет).
    """
    info: dict[str, Any] = {
        "queue": _arq_queue_name(),
        "enabled": bool(settings.arq_enabled),
    }
    if not arq_can_run():
        info["reason"] = (
            "ARQ недоступен: проверьте REDIS_ENABLED, ARQ_ENABLED, REDIS_URL, "
            "REDIS_MEMORY_ONLY=false."
        )
        return "down", info

    try:
        from arq import create_pool
        from arq.connections import RedisSettings
    except Exception as exc:  # pragma: no cover — arq должен быть установлен
        info["error"] = f"arq import failed: {exc!s}"[:300]
        return "down", info

    try:
        pool = await create_pool(
            RedisSettings.from_dsn(settings.redis_url),
            default_queue_name=_arq_queue_name(),
        )
    except Exception as exc:
        info["error"] = str(exc)[:300]
        return "down", info

    try:
        try:
            await pool.close()
        except Exception:  # pragma: no cover
            logger.debug("ARQ probe: pool.close failed", exc_info=True)
    finally:
        pool = None  # noqa: F841

    return "ok", info


async def _check_worker() -> tuple[HealthStatus, dict[str, Any]]:
    """
    Worker heartbeat: arq‑worker сам пишет `<queue>:health-check` (PSETEX) каждые
    `health_check_interval` секунд (дефолт 3600). Если ключ есть и `TTL>0` —
    `ok`; если ключ присутствует, но TTL не определён (`-1`) — `degraded`;
    отсутствует — `unknown` (worker мог не запускаться, и это бывает в dev).
    """
    info: dict[str, Any] = {"key": _arq_health_key()}

    if isinstance(redis_client, InMemoryRedis):
        info["note"] = "in-memory Redis: heartbeat не пишется"
        return "unknown", info

    try:
        raw = await redis_client.get(info["key"])
    except Exception as exc:
        info["error"] = str(exc)[:300]
        return "unknown", info

    if raw is None:
        info["note"] = "heartbeat key отсутствует — worker не запущен или ещё не писал"
        return "unknown", info

    payload = raw if isinstance(raw, str) else (raw.decode("utf-8", errors="replace") if isinstance(raw, (bytes, bytearray)) else str(raw))
    info["last"] = payload[:300]

    ttl: int | None = None
    try:
        ttl_raw = await redis_client.ttl(info["key"])
        ttl = int(ttl_raw) if ttl_raw is not None else None
    except Exception:  # pragma: no cover — ttl поддерживается всеми реальными клиентами
        ttl = None

    if ttl is None:
        return "ok", info

    info["ttl_seconds"] = ttl
    if ttl > 0:
        return "ok", info
    if ttl == -1:
        return "degraded", info
    return "unknown", info


async def check_task_queue_health() -> dict[str, Any]:
    """Собрать структурированный ответ для UI/мониторинга."""
    redis_status, redis_info = await _check_redis()
    arq_status, arq_info = await _check_arq()
    worker_status, worker_info = await _check_worker()

    # Если Redis недоступен, ARQ почти всегда тоже падает; для консистентности
    # делаем worker `unknown`, чтобы UI не светил «down» из-за каскада.
    if redis_status == "down" and worker_status != "down":
        worker_status = "unknown"

    return {
        "redis": redis_status,
        "arq": arq_status,
        "worker": worker_status,
        "details": {
            "redis": redis_info,
            "arq": arq_info,
            "worker": worker_info,
        },
        "checked_at": datetime.now(tz=timezone.utc).isoformat(),
    }
