"""
E5: GET /api/admin/system/task-queue-health.

Тесты идут через прямой вызов хелпера ``check_task_queue_health`` —
полноценный ASGI прогон не нужен (эндпоинт — тонкий враппер), а
зависимость на реальный ARQ/Redis (которых в CI нет) исключаем
монкипатчем `arq_can_run`.
"""

from __future__ import annotations

import pytest

from app.main import app


def test_system_task_queue_health_route_mounted() -> None:
    matches = [
        r for r in app.routes
        if getattr(r, "path", "") == "/api/admin/system/task-queue-health"
        and "GET" in getattr(r, "methods", set())
    ]
    assert len(matches) == 1


@pytest.mark.asyncio
async def test_check_task_queue_health_in_memory_redis_returns_degraded(monkeypatch) -> None:
    from app.services import task_queue_health as mod

    # InMemoryRedis по умолчанию в тестах → redis = degraded, arq = down,
    # worker = unknown. Это ожидаемое состояние для dev/CI без Redis.
    monkeypatch.setattr(mod, "arq_can_run", lambda: False)

    out = await mod.check_task_queue_health()
    assert out["redis"] in {"ok", "degraded", "down"}
    assert out["arq"] == "down"
    assert out["worker"] in {"unknown", "down"}
    assert "details" in out
    assert "checked_at" in out
    assert isinstance(out["details"], dict)
    assert "redis" in out["details"] and "arq" in out["details"] and "worker" in out["details"]


@pytest.mark.asyncio
async def test_check_task_queue_health_arq_unavailable_reason(monkeypatch) -> None:
    from app.services import task_queue_health as mod

    monkeypatch.setattr(mod, "arq_can_run", lambda: False)
    out = await mod.check_task_queue_health()
    assert out["arq"] == "down"
    assert "reason" in out["details"]["arq"]


@pytest.mark.asyncio
async def test_check_task_queue_health_arq_create_pool_error_is_down(monkeypatch) -> None:
    """Если pool падает (Redis недоступен), arq=down с конкретной ошибкой."""
    from app.services import task_queue_health as mod

    monkeypatch.setattr(mod, "arq_can_run", lambda: True)

    class _BoomPool:
        @staticmethod
        async def boom(*_a, **_kw):
            raise RuntimeError("redis refused")

    async def _fake_create_pool(*_a, **_kw):
        raise RuntimeError("redis refused")

    # Подменяем модуль arq, чтобы create_pool бросал. Импорт идёт лениво,
    # поэтому достаточно заглушить через sys.modules.
    import sys
    import types

    fake_arq = types.ModuleType("arq")
    fake_arq.create_pool = _fake_create_pool  # type: ignore[attr-defined]
    fake_arq_conn = types.ModuleType("arq.connections")

    class _RS:
        @staticmethod
        def from_dsn(_url):
            return object()

    fake_arq_conn.RedisSettings = _RS  # type: ignore[attr-defined]

    saved_arq = sys.modules.get("arq")
    saved_arq_conn = sys.modules.get("arq.connections")
    sys.modules["arq"] = fake_arq
    sys.modules["arq.connections"] = fake_arq_conn
    try:
        out = await mod.check_task_queue_health()
    finally:
        if saved_arq is not None:
            sys.modules["arq"] = saved_arq
        else:
            sys.modules.pop("arq", None)
        if saved_arq_conn is not None:
            sys.modules["arq.connections"] = saved_arq_conn
        else:
            sys.modules.pop("arq.connections", None)

    assert out["arq"] == "down"
    assert "redis refused" in out["details"]["arq"]["error"]
