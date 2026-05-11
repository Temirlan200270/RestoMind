import pytest

from app.services.restaurant_context_cache import (
    cached_format_org_current_time_block,
    invalidate_org_time_block_cache,
    redis_cached_menu_context_string,
)


def test_cached_time_block_stable_until_invalidate() -> None:
    oid = 424243
    invalidate_org_time_block_cache(oid)
    sched = {"mon": {"is_closed": False}}
    a = cached_format_org_current_time_block(oid, "Etc/GMT-5", sched)
    b = cached_format_org_current_time_block(oid, "Etc/GMT-5", sched)
    assert a == b
    assert isinstance(a, str) and a.strip()
    invalidate_org_time_block_cache(oid)
    c = cached_format_org_current_time_block(oid, "Etc/GMT-5", sched)
    assert c == a


@pytest.mark.asyncio
async def test_redis_menu_context_cache_accepts_decoded_string(monkeypatch) -> None:
    from app.services import restaurant_context_cache as mod

    class _Redis:
        async def get(self, _key):
            return "cached menu"

    async def _build():
        raise AssertionError("cache hit should not rebuild")

    monkeypatch.setattr(mod.settings, "redis_enabled", True)
    monkeypatch.setattr(mod.settings, "redis_memory_only", False)
    monkeypatch.setattr(mod, "redis_client", _Redis())

    out = await redis_cached_menu_context_string(1, _build)
    assert out == "cached menu"
