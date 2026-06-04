from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.db.session import InMemoryRedis
from app.services import chat_serializer as cs
from app.services.chat_serializer import ChatMessagePayload


@pytest.fixture
def fake_redis(monkeypatch) -> InMemoryRedis:
    store = InMemoryRedis()
    monkeypatch.setattr(cs, "redis_client", store)
    return store


def _payload(seq: int = 1) -> ChatMessagePayload:
    return ChatMessagePayload(
        phone="+77001112233",
        message_text=f"msg-{seq}",
        whatsapp_message_id=f"wmid-{seq}",
        organization_id=1,
    )


@pytest.mark.asyncio
async def test_acquire_lock_blocks_concurrent_caller(fake_redis) -> None:
    ok1 = await cs.acquire_chat_lock(1, "+77001112233")
    ok2 = await cs.acquire_chat_lock(1, "+77001112233")
    assert ok1 is True
    assert ok2 is False
    await cs.release_chat_lock(1, "+77001112233")


@pytest.mark.asyncio
async def test_lock_owner_token_controls_renew_and_release(fake_redis) -> None:
    assert await cs.acquire_chat_lock(1, "+77001112233", "owner-a") is True
    assert await cs.renew_chat_lock(1, "+77001112233", "owner-b") is False
    assert await cs.release_chat_lock(1, "+77001112233", "owner-b") is False
    assert await cs.renew_chat_lock(1, "+77001112233", "owner-a") is True
    assert await cs.release_chat_lock(1, "+77001112233", "owner-a") is True
    assert await fake_redis.get(cs.chat_lock_key(1, "+77001112233")) is None


@pytest.mark.asyncio
async def test_renew_lock_accepts_real_redis_bytes(monkeypatch) -> None:
    class BytesGetRedis:
        def __init__(self) -> None:
            self.expired_key = ""

        async def get(self, _key: str) -> bytes:
            return b"owner-a"

        async def expire(self, key: str, _ttl: int) -> None:
            self.expired_key = key

    store = BytesGetRedis()
    monkeypatch.setattr(cs, "redis_client", store)

    assert await cs.renew_chat_lock(1, "+77001112233", "owner-a") is True
    assert store.expired_key == cs.chat_lock_key(1, "+77001112233")


@pytest.mark.asyncio
async def test_queue_fifo_and_drain(fake_redis) -> None:
    await cs.enqueue_pending(1, "+77001112233", _payload(1))
    await cs.enqueue_pending(1, "+77001112233", _payload(2))
    first = await cs.drain_next(1, "+77001112233")
    second = await cs.drain_next(1, "+77001112233")
    assert first is not None and first.message_text == "msg-1"
    assert second is not None and second.message_text == "msg-2"


@pytest.mark.asyncio
async def test_drain_next_decodes_real_redis_bytes(monkeypatch) -> None:
    class BytesRedis:
        def __init__(self, raw: bytes) -> None:
            self.raw = raw

        async def lpop(self, _key: str) -> bytes | None:
            raw = self.raw
            self.raw = b""
            return raw

    payload = _payload(9)
    monkeypatch.setattr(cs, "redis_client", BytesRedis(payload.to_json().encode("utf-8")))

    drained = await cs.drain_next(1, "+77001112233")

    assert drained is not None
    assert drained.message_text == "msg-9"


@pytest.mark.asyncio
async def test_double_text_burst_serializes_processing(fake_redis) -> None:
    order: list[str] = []
    in_flight = 0
    max_in_flight = 0

    async def process_one(p: ChatMessagePayload) -> None:
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0.02)
        in_flight -= 1
        order.append(p.message_text)

    await asyncio.gather(
        cs.run_serialized_chat_pipeline(1, "+77001112233", _payload(1), process_one=process_one),
        cs.run_serialized_chat_pipeline(1, "+77001112233", _payload(2), process_one=process_one),
        cs.run_serialized_chat_pipeline(1, "+77001112233", _payload(3), process_one=process_one),
    )

    assert len(order) == 3
    assert max_in_flight == 1
    assert set(order) == {"msg-1", "msg-2", "msg-3"}


@pytest.mark.asyncio
async def test_queue_dedupes_same_whatsapp_message_id(fake_redis) -> None:
    p = _payload(1)
    await cs.enqueue_pending(1, "+77001112233", p)
    await cs.enqueue_pending(1, "+77001112233", p)
    first = await cs.drain_next(1, "+77001112233")
    second = await cs.drain_next(1, "+77001112233")
    assert first is not None
    assert second is None
