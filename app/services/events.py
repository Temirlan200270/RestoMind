"""
Система событий реального времени через Redis Pub/Sub.
Используется для push-уведомлений в WebSocket админки.
При отключённом Redis — fallback на asyncio broadcast.
"""

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from datetime import datetime
from typing import Any

from app.core.config import settings
from app.db.session import redis_connection_kwargs, redis_pubsub_available
from app.services.async_tasks import spawn_tracked

logger = logging.getLogger(__name__)

CHANNEL_NAME = "admin_events"


def _org_channel(org_id: int) -> str:
    """Org-scoped Pub/Sub канал: каждый WS подписывается только на свой org."""
    return f"admin_events:org:{org_id}"

# Хранилище подключённых WebSocket-подписчиков (in-memory fallback)
_subscribers: set[asyncio.Queue[str]] = set()
_pub_client: Any | None = None
_cached_redis_url: str = ""


async def _get_pub_client():
    """Ленивый Redis client для publish (переиспользуется между вызовами)."""
    global _pub_client, _cached_redis_url
    from redis.asyncio import Redis

    url = (settings.redis_url or "").strip()
    if not url:
        return None
    if _pub_client is None or _cached_redis_url != url:
        try:
            if _pub_client is not None:
                await _pub_client.aclose()
        except Exception:
            pass
        _cached_redis_url = url
        _pub_client = Redis.from_url(url, **redis_connection_kwargs())
    return _pub_client


async def publish_event(event_type: str, data: dict[str, Any]) -> None:
    """
    Опубликовать событие для всех подписчиков админки.

    Args:
        event_type: Тип события (new_message, order_updated, human_needed).
        data: Данные события.
    """
    payload = json.dumps(
        {"type": event_type, "data": data, "ts": datetime.now().isoformat()},
        ensure_ascii=False,
    )

    if redis_pubsub_available():
        try:
            pub_client = await _get_pub_client()
            if pub_client is None:
                raise RuntimeError("Redis URL not configured")
            await pub_client.publish(CHANNEL_NAME, payload)
        except Exception as exc:
            logger.error("Redis publish error: %s", exc)
            _broadcast_in_memory(payload)
    else:
        _broadcast_in_memory(payload)

    logger.debug("Event published: %s", event_type)
    try:
        from app.services.notification_router import notify_staff_from_event

        # Fire-and-forget: уведомления не блокируют realtime-путь
        spawn_tracked(
            notify_staff_from_event(event_type, data),
            name=f"notify_staff_{event_type}",
            log=logger,
        )
    except Exception:
        logger.exception("Staff notification task creation failed for %s", event_type)


def _broadcast_in_memory(payload: str) -> None:
    """Рассылает событие во все in-memory очереди подписчиков."""
    dead: list[asyncio.Queue[str]] = []
    for q in _subscribers:
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            dead.append(q)
    for q in dead:
        _subscribers.discard(q)


async def subscribe_events(org_id: int | None = None) -> AsyncGenerator[str, None]:
    """Асинхронный генератор событий.

    Если org_id задан — подписывается на org-scoped канал (Phase 2a).
    Без Redis — asyncio.Queue broadcast с фильтром на стороне клиента.
    """
    channel = _org_channel(org_id) if org_id is not None else CHANNEL_NAME
    if redis_pubsub_available():
        try:
            async for event in _subscribe_redis(channel):
                yield event
        except Exception as exc:
            logger.error("Redis subscribe error: %s — переключение на in-memory", exc)
            async for event in _subscribe_memory():
                yield event
    else:
        async for event in _subscribe_memory():
            yield event


async def publish_org_event(org_id: int, event_type: str, data: dict) -> None:
    """Публикует событие в org-scoped канал (Phase 2a).

    Также публикует в глобальный канал для обратной совместимости.
    """
    enriched = {**data, "organization_id": int(org_id), "org_id": int(org_id)}
    payload = json.dumps(
        {"type": event_type, "data": enriched, "ts": datetime.now().isoformat()},
        ensure_ascii=False,
    )

    if redis_pubsub_available():
        try:
            pub_client = await _get_pub_client()
            if pub_client is None:
                raise RuntimeError("Redis URL not configured")
            await pub_client.publish(_org_channel(int(org_id)), payload)
            await pub_client.publish(CHANNEL_NAME, payload)
        except Exception as exc:
            logger.error("Redis org publish error: %s", exc)
            _broadcast_in_memory(payload)
    else:
        _broadcast_in_memory(payload)

    logger.debug("Org event published: org=%s type=%s", org_id, event_type)
    try:
        from app.services.notification_router import notify_staff_from_event

        spawn_tracked(
            notify_staff_from_event(event_type, enriched),
            name=f"notify_staff_{org_id}_{event_type}",
            log=logger,
        )
    except Exception:
        logger.exception("Staff notification task creation failed for %s", event_type)


async def _subscribe_redis(channel: str = CHANNEL_NAME) -> AsyncGenerator[str, None]:
    """Подписка на Redis Pub/Sub канал (глобальный или org-scoped)."""
    from redis.asyncio import Redis

    sub_client = Redis.from_url(settings.redis_url, **redis_connection_kwargs())
    try:
        pubsub = sub_client.pubsub()
        await pubsub.subscribe(channel)
        logger.info("WebSocket подписан на Redis канал %s", channel)

        async for message in pubsub.listen():
            if message["type"] == "message":
                yield message["data"]
    finally:
        await pubsub.unsubscribe(channel)
        await sub_client.aclose()


async def _subscribe_memory() -> AsyncGenerator[str, None]:
    """Подписка через in-memory очередь (fallback без Redis)."""
    queue: asyncio.Queue[str] = asyncio.Queue(maxsize=100)
    _subscribers.add(queue)
    logger.info("WebSocket подписан на in-memory broadcast")
    try:
        while True:
            event = await queue.get()
            yield event
    finally:
        _subscribers.discard(queue)
