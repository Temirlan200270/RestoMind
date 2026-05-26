"""Per-chat single-flight: dedupe (DB) + FIFO queue + lease lock (G10 simplified)."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.db.session import InMemoryRedis, redis_client

logger = logging.getLogger(__name__)

LOCK_TTL_SEC = 15
LOCK_RENEW_INTERVAL_SEC = 5
QUEUE_TTL_SEC = 300
MAX_QUEUE_LEN = 20
MAX_DRAIN_PER_CYCLE = 20

_RELEASE_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
else
  return 0
end
"""


@dataclass(frozen=True)
class ChatMessagePayload:
    phone: str
    message_text: str
    whatsapp_message_id: str
    organization_id: int
    voice_audio: tuple[bytes, str] | None = None
    channel: str = "whatsapp"
    telegram_chat_id: int = 0

    def to_json(self) -> str:
        voice_b64 = None
        voice_mime = None
        if self.voice_audio:
            voice_b64 = base64.b64encode(self.voice_audio[0]).decode("ascii")
            voice_mime = self.voice_audio[1]
        return json.dumps(
            {
                "phone": self.phone,
                "message_text": self.message_text,
                "whatsapp_message_id": self.whatsapp_message_id,
                "organization_id": self.organization_id,
                "voice_b64": voice_b64,
                "voice_mime": voice_mime,
                "channel": self.channel,
                "telegram_chat_id": self.telegram_chat_id,
            },
            ensure_ascii=False,
        )

    @classmethod
    def from_json(cls, raw: str) -> ChatMessagePayload:
        data = json.loads(raw)
        voice_audio = None
        if data.get("voice_b64"):
            voice_audio = (
                base64.b64decode(str(data["voice_b64"])),
                str(data.get("voice_mime") or "audio/ogg"),
            )
        return cls(
            phone=str(data["phone"]),
            message_text=str(data.get("message_text") or ""),
            whatsapp_message_id=str(data.get("whatsapp_message_id") or ""),
            organization_id=int(data["organization_id"]),
            voice_audio=voice_audio,
            channel=str(data.get("channel") or "whatsapp"),
            telegram_chat_id=int(data.get("telegram_chat_id") or 0),
        )


def _phone_scope(org_id: int, phone: str) -> str:
    normalized = re.sub(r"\D", "", str(phone or ""))[-12:] or "unknown"
    return hashlib.sha256(f"{int(org_id)}:{normalized}".encode()).hexdigest()[:16]


def chat_lock_key(org_id: int, phone: str) -> str:
    return f"chat:lock:{int(org_id)}:{_phone_scope(org_id, phone)}"


def chat_queue_key(org_id: int, phone: str) -> str:
    return f"chat:queue:{int(org_id)}:{_phone_scope(org_id, phone)}"


async def _eval_release(key: str, owner_id: str) -> bool:
    if isinstance(redis_client, InMemoryRedis):
        if await redis_client.get(key) == owner_id:
            await redis_client.delete(key)
            return True
        return False
    result = await redis_client.eval(_RELEASE_LUA, 1, key, owner_id, "0")
    return bool(int(result or 0))


async def acquire_chat_lock(org_id: int, phone: str, *, ttl_sec: int = LOCK_TTL_SEC) -> bool:
    key = chat_lock_key(org_id, phone)
    try:
        claimed = await redis_client.set(key, "active", nx=True, ex=int(ttl_sec))
        if claimed:
            logger.info(
                "chat_serializer.lock_acquired org_id=%s phone_scope=%s",
                org_id,
                _phone_scope(org_id, phone),
            )
        return bool(claimed)
    except Exception as exc:
        logger.warning("chat_serializer.lock_acquire_failed org_id=%s err=%s", org_id, exc)
        return True


async def renew_chat_lock(org_id: int, phone: str, *, ttl_sec: int = LOCK_TTL_SEC) -> bool:
    key = chat_lock_key(org_id, phone)
    try:
        if await redis_client.get(key) == "active":
            await redis_client.expire(key, int(ttl_sec))
            return True
        return False
    except Exception as exc:
        logger.warning("chat_serializer.lock_renew_failed org_id=%s err=%s", org_id, exc)
        return False


async def release_chat_lock(org_id: int, phone: str) -> bool:
    key = chat_lock_key(org_id, phone)
    try:
        ok = await _eval_release(key, "active")
        if ok:
            logger.info(
                "chat_serializer.released org_id=%s phone_scope=%s",
                org_id,
                _phone_scope(org_id, phone),
            )
        return ok
    except Exception as exc:
        logger.warning("chat_serializer.lock_release_failed org_id=%s err=%s", org_id, exc)
        return False


async def _queue_length(org_id: int, phone: str) -> int:
    key = chat_queue_key(org_id, phone)
    try:
        if isinstance(redis_client, InMemoryRedis):
            return len(redis_client._store.get(key, []))
        raw = await redis_client.llen(key)
        return int(raw or 0)
    except Exception:
        return 0


async def _queue_contains_message_id(org_id: int, phone: str, whatsapp_message_id: str) -> bool:
    wmid = (whatsapp_message_id or "").strip()
    if not wmid:
        return False
    key = chat_queue_key(org_id, phone)
    try:
        if isinstance(redis_client, InMemoryRedis):
            items = redis_client._store.get(key, [])
        else:
            items = await redis_client.lrange(key, 0, -1)
        for raw in items or []:
            try:
                data = json.loads(raw if isinstance(raw, str) else raw.decode())
                if str(data.get("whatsapp_message_id") or "") == wmid:
                    return True
            except (json.JSONDecodeError, TypeError, AttributeError):
                continue
        return False
    except Exception:
        return False


async def _trim_queue_head(org_id: int, phone: str) -> None:
    key = chat_queue_key(org_id, phone)
    try:
        if isinstance(redis_client, InMemoryRedis):
            items = redis_client._store.get(key, [])
            if items:
                items.pop(0)
            return
        await redis_client.lpop(key)
    except Exception as exc:
        logger.warning("chat_serializer.queue_trim_failed org_id=%s err=%s", org_id, exc)


async def enqueue_pending(org_id: int, phone: str, payload: ChatMessagePayload) -> int:
    wmid = (payload.whatsapp_message_id or "").strip()
    if wmid and await _queue_contains_message_id(org_id, phone, wmid):
        logger.info("chat_serializer.queue_deduped org_id=%s wmid=%s", org_id, wmid[:16])
        return await _queue_length(org_id, phone)

    key = chat_queue_key(org_id, phone)
    try:
        while await _queue_length(org_id, phone) >= MAX_QUEUE_LEN:
            await _trim_queue_head(org_id, phone)
            logger.warning(
                "chat_serializer.queue_overflow org_id=%s phone_scope=%s maxlen=%s",
                org_id,
                _phone_scope(org_id, phone),
                MAX_QUEUE_LEN,
            )
        length = await redis_client.rpush(key, payload.to_json())
        await redis_client.expire(key, QUEUE_TTL_SEC)
        logger.info(
            "chat_serializer.queued org_id=%s phone_scope=%s queue_len=%s wmid=%s",
            org_id,
            _phone_scope(org_id, phone),
            length,
            wmid[:16] if wmid else "",
        )
        return int(length or 0)
    except Exception as exc:
        logger.error("chat_serializer.enqueue_failed org_id=%s err=%s", org_id, exc)
        return 0


async def drain_next(org_id: int, phone: str) -> ChatMessagePayload | None:
    key = chat_queue_key(org_id, phone)
    try:
        if isinstance(redis_client, InMemoryRedis):
            items = redis_client._store.get(key, [])
            if not items:
                return None
            raw = items.pop(0)
            return ChatMessagePayload.from_json(raw)
        raw = await redis_client.lpop(key)
        if not raw:
            return None
        return ChatMessagePayload.from_json(str(raw))
    except Exception as exc:
        logger.warning("chat_serializer.drain_failed org_id=%s err=%s", org_id, exc)
        return None


async def run_serialized_chat_pipeline(
    org_id: int,
    phone: str,
    initial: ChatMessagePayload,
    *,
    process_one: Callable[[ChatMessagePayload], Awaitable[None]],
) -> bool:
    """Enqueue, acquire lease lock, drain FIFO until empty (renew lock per message)."""
    await enqueue_pending(org_id, phone, initial)
    acquired = await acquire_chat_lock(org_id, phone)
    if not acquired:
        return False

    processed = 0
    try:
        while processed < MAX_DRAIN_PER_CYCLE:
            item = await drain_next(org_id, phone)
            if item is None:
                break
            if not await renew_chat_lock(org_id, phone):
                await enqueue_pending(org_id, phone, item)
                break
            await process_one(item)
            processed += 1
    finally:
        await release_chat_lock(org_id, phone)
    return True
