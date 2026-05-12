"""
iiko Webhook endpoint.

iiko отправляет POST при изменении меню или стоп-листа.
RestoMind дебаунсирует (Redis, 30 сек), ставит ARQ-задачу, ACK 200.

Настройка в iiko:
  Menu → Settings → Notifications → Webhook URL:
  https://<your-domain>/api/iiko/webhook

Опциональная защита:
  IIKO_WEBHOOK_SECRET=<secret>
  → iiko должен слать заголовок X-Iiko-Webhook-Secret: <secret>
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/iiko", tags=["iiko Webhook"])


@router.post("/webhook")
async def iiko_webhook(request: Request) -> dict[str, Any]:
    """
    Получает событие из iiko и ставит sync в очередь.

    Тело (всё необязательно — если пустое, синкаем все активные орги):
        {
          "event_type": "menu_updated" | "stoplist_updated" | "all_updated",
          "organizationId": "<iiko uuid>"   // UUID орг в iiko
        }
    """
    from app.core.config import settings
    from app.db.models import Organization
    from app.db.session import async_session_factory
    from app.services.task_queue import enqueue_job

    # ── 1. Проверка секрета ────────────────────────────────────────
    secret = (settings.iiko_webhook_secret or "").strip()
    if secret:
        header_secret = (request.headers.get("X-Iiko-Webhook-Secret") or "").strip()
        if header_secret != secret:
            logger.warning("iiko webhook: неверный секрет (IP=%s)", request.client.host if request.client else "?")
            raise HTTPException(status_code=403, detail="Invalid webhook secret")

    # ── 2. Разбор тела ────────────────────────────────────────────
    body: dict[str, Any] = {}
    try:
        raw = await request.body()
        if raw:
            import json
            body = json.loads(raw)
            if not isinstance(body, dict):
                body = {}
    except Exception:
        body = {}

    event_type = (body.get("event_type") or "all_updated").strip().lower()
    iiko_org_id = (body.get("organizationId") or body.get("organization_id") or "").strip()

    # ── 3. Найти организации ─────────────────────────────────────
    async with async_session_factory() as db:
        if iiko_org_id:
            orgs = (await db.execute(
                select(Organization).where(
                    Organization.iiko_organization_id == iiko_org_id,
                    Organization.is_active.is_(True),
                )
            )).scalars().all()
        else:
            orgs = (await db.execute(
                select(Organization).where(Organization.is_active.is_(True))
            )).scalars().all()

    if not orgs:
        logger.info("iiko webhook: орг не найдена (iiko_org_id=%r)", iiko_org_id)
        return {"ok": True, "queued": [], "note": "no matching organizations"}

    # ── 4. Debounce + enqueue ─────────────────────────────────────
    queued: list[dict[str, Any]] = []
    debounce_sec = settings.iiko_webhook_sync_debounce_sec

    try:
        from app.integrations.redis_client import get_redis_client
        redis = await get_redis_client()
    except Exception:
        redis = None

    want_stoplist = event_type in ("stoplist_updated", "all_updated")
    want_menu = event_type in ("menu_updated", "all_updated")

    for org in orgs:
        org_id = int(org.id)

        if want_stoplist:
            key = f"rm:iiko_sync:stoplist:{org_id}"
            if redis and await _debounce_check(redis, key, debounce_sec):
                logger.debug("iiko webhook: stoplist sync debounced for org_id=%s", org_id)
            else:
                try:
                    await enqueue_job("iiko_stoplist_sync", org_id=org_id)
                    queued.append({"org_id": org_id, "kind": "stoplist"})
                except Exception as exc:
                    logger.warning("iiko webhook: enqueue stoplist failed org_id=%s: %s", org_id, exc)
                    # fallback: fire-and-forget
                    import asyncio
                    from app.services.iiko_sync_tasks import run_stoplist_sync
                    asyncio.create_task(run_stoplist_sync(org_id))
                    queued.append({"org_id": org_id, "kind": "stoplist", "via": "background"})

        if want_menu:
            key = f"rm:iiko_sync:menu:{org_id}"
            if redis and await _debounce_check(redis, key, debounce_sec):
                logger.debug("iiko webhook: menu sync debounced for org_id=%s", org_id)
            else:
                try:
                    await enqueue_job("iiko_menu_sync", org_id=org_id)
                    queued.append({"org_id": org_id, "kind": "menu"})
                except Exception as exc:
                    logger.warning("iiko webhook: enqueue menu failed org_id=%s: %s", org_id, exc)
                    import asyncio
                    from app.services.iiko_sync_tasks import run_menu_sync
                    asyncio.create_task(run_menu_sync(org_id))
                    queued.append({"org_id": org_id, "kind": "menu", "via": "background"})

    logger.info("iiko webhook: event=%r iiko_org=%r → queued=%s", event_type, iiko_org_id, queued)
    return {"ok": True, "queued": queued}


async def _debounce_check(redis: Any, key: str, ttl_sec: int) -> bool:
    """
    Возвращает True если задача уже поставлена (дебаунс активен).
    Устанавливает ключ с TTL если его не было.
    """
    try:
        existed = await redis.set(key, "1", ex=ttl_sec, nx=True)
        # nx=True: SET только если ключа нет. Если SET выполнен → ключа не было → дебаунс НЕ активен.
        return existed is None  # None → ключ уже был → дебаунс активен
    except Exception:
        return False  # при ошибке Redis пропускаем дебаунс
