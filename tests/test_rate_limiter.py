"""
Тесты Rate Limiter (in-memory).
"""

import pytest

from app.core.rate_limiter import _memory_store, check_rate_limit


@pytest.fixture(autouse=True)
def clear_store():
    """Очищаем in-memory store перед каждым тестом."""
    _memory_store.clear()
    yield
    _memory_store.clear()


@pytest.mark.asyncio
async def test_allows_under_limit() -> None:
    """Запросы в пределах лимита проходят."""
    for _ in range(5):
        assert await check_rate_limit("test-phone") is True


@pytest.mark.asyncio
async def test_blocks_over_limit() -> None:
    """После превышения лимита — блокировка."""
    from app.core.config import settings
    limit = settings.rate_limit_per_minute

    for _ in range(limit):
        assert await check_rate_limit("spam-phone") is True

    assert await check_rate_limit("spam-phone") is False


@pytest.mark.asyncio
async def test_different_keys_independent() -> None:
    """Разные ключи (телефоны) имеют независимые лимиты."""
    from app.core.config import settings
    limit = settings.rate_limit_per_minute

    for _ in range(limit):
        await check_rate_limit("phone-a")

    assert await check_rate_limit("phone-a") is False
    assert await check_rate_limit("phone-b") is True
