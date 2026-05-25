"""Тесты FAQ-кеша в Redis."""

import pytest

from app.schemas.ai_schemas import AIBrainResponse, OrderItem
from app.services.faq_cache import (
    get_cached_faq_reply,
    kb_fingerprint_from_text,
    normalize_faq_question,
    save_faq_reply,
    should_save_faq_reply,
)


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.ttl: dict[str, int] = {}
        self.counters: dict[str, int] = {}

    async def get(self, key: str):
        return self.store.get(key)

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self.store[key] = value
        self.ttl[key] = ttl

    async def incr(self, key: str) -> int:
        self.counters[key] = self.counters.get(key, 0) + 1
        return self.counters[key]

    async def expire(self, key: str, ttl: int) -> None:
        self.ttl[key] = ttl


@pytest.mark.asyncio
async def test_save_and_get_faq_reply(monkeypatch) -> None:
    fake = FakeRedis()
    monkeypatch.setattr("app.services.faq_cache.redis_client", fake)
    monkeypatch.setattr("app.services.faq_cache.settings.faq_cache_enabled", True)
    monkeypatch.setattr("app.services.faq_cache.settings.redis_enabled", True)

    kb_fp = kb_fingerprint_from_text("Парковка есть.")
    await save_faq_reply(
        org_id=1,
        message_text="Где парковка?",
        kb_fingerprint=kb_fp,
        reply="Парковка есть во дворе.",
    )
    got = await get_cached_faq_reply(
        org_id=1,
        message_text="Где парковка?",
        kb_fingerprint=kb_fp,
    )
    assert got == "Парковка есть во дворе."


@pytest.mark.asyncio
async def test_kb_fingerprint_mismatch(monkeypatch) -> None:
    fake = FakeRedis()
    monkeypatch.setattr("app.services.faq_cache.redis_client", fake)
    monkeypatch.setattr("app.services.faq_cache.settings.faq_cache_enabled", True)
    monkeypatch.setattr("app.services.faq_cache.settings.redis_enabled", True)

    await save_faq_reply(
        org_id=1,
        message_text="принимаете карты",
        kb_fingerprint="aaa",
        reply="Да, принимаем.",
    )
    got = await get_cached_faq_reply(
        org_id=1,
        message_text="принимаете карты",
        kb_fingerprint="bbb",
    )
    assert got is None


def test_normalize_faq_question() -> None:
    assert normalize_faq_question("  Где парковка?  ") == "где парковка"


def test_should_save_faq_reply() -> None:
    faq = AIBrainResponse(intent="faq", reply_text="Да, есть.")
    assert should_save_faq_reply(faq, has_draft=False) is True

    with_items = AIBrainResponse(
        intent="faq",
        reply_text="Ок",
        items=[OrderItem(name="X", quantity=1)],
    )
    assert should_save_faq_reply(with_items, has_draft=False) is False

    with_draft = AIBrainResponse(intent="faq", reply_text="Да.")
    assert should_save_faq_reply(with_draft, has_draft=True) is False


@pytest.mark.asyncio
async def test_too_short_question_not_cached(monkeypatch) -> None:
    fake = FakeRedis()
    monkeypatch.setattr("app.services.faq_cache.redis_client", fake)
    monkeypatch.setattr("app.services.faq_cache.settings.faq_cache_enabled", True)
    monkeypatch.setattr("app.services.faq_cache.settings.redis_enabled", True)

    await save_faq_reply(
        org_id=1,
        message_text="да",
        kb_fingerprint="x",
        reply="ок",
    )
    assert not fake.store
