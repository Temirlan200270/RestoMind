"""
Системная диагностика для админки (E5).

Содержит лёгкие read-only эндпоинты, которые UI и внешние мониторы
дёргают, чтобы понять состояние подсистем (Redis / ARQ / worker).

Org-scope здесь не нужен: данные относятся к всему процессу
(подключение к Redis, ARQ-очередь, worker). Доступ — обычный admin
session, как и у `/api/admin/readiness`.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Query, Request

from app.services.faq_cache import get_faq_cache_metrics
from app.services.task_queue_health import check_task_queue_health

from .deps import admin_org_from_session, require_admin_session_active

logger = logging.getLogger(__name__)

system_router = APIRouter(dependencies=[Depends(require_admin_session_active)])


@system_router.get("/system/task-queue-health")
async def system_task_queue_health() -> dict[str, Any]:
    """
    Состояние очереди задач: Redis, ARQ, worker heartbeat.

    Контракт ответа (см. ``app/services/task_queue_health.py``):

    ```json
    {
      "redis":  "ok" | "degraded" | "down",
      "arq":    "ok" | "degraded" | "down",
      "worker": "ok" | "degraded" | "down" | "unknown",
      "details": { ... },
      "checked_at": "ISO8601"
    }
    ```

    UI (`admin-app.js → refreshTaskQueueHealth`) ожидает именно эти три
    верхнеуровневых ключа со значениями из множества выше — стиль бейджа
    подбирается по строке (`taskQueueStatusClass`).
    """
    return await check_task_queue_health()


@system_router.get("/system/faq-cache-metrics")
async def system_faq_cache_metrics(
    request: Request,
    days: int = Query(default=7, ge=1, le=31),
) -> dict[str, Any]:
    """
    Prod smoke: hit/miss/save по ключам ``rm:metrics:faq_cache:*`` для текущего филиала.

    ``hit_rate_pct`` = hit / (hit + miss); ``save`` — записи после LLM intent=faq.
    """
    org_id = admin_org_from_session(request)
    return await get_faq_cache_metrics(org_id, days=days)
