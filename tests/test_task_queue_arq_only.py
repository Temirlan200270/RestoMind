"""E5: когда ARQ настроен — ошибка enqueue пробрасывается без BackgroundTasks fallback."""

from __future__ import annotations

import pytest

from app.services.task_queue import TaskQueueEnqueueError, dispatch_arq_or_background


class _BackgroundTasksMustNotRun:
    def add_task(self, *_args, **_kwargs) -> None:
        raise AssertionError("BackgroundTasks fallback must not run")


@pytest.mark.asyncio
async def test_dispatch_propagates_arq_error_without_background_fallback(monkeypatch) -> None:
    """Если ARQ настроен (arq_can_run=True) но enqueue упал — ошибка пробрасывается."""
    async def _fail_enqueue(_name: str, **_kwargs) -> None:
        raise TaskQueueEnqueueError("redis unavailable")

    monkeypatch.setattr("app.services.task_queue.arq_can_run", lambda: True)
    monkeypatch.setattr("app.services.task_queue.enqueue_job", _fail_enqueue)
    monkeypatch.setattr("app.services.task_queue.settings.whatsapp_text_background_enabled", False)

    with pytest.raises(TaskQueueEnqueueError):
        await dispatch_arq_or_background(
            "whatsapp_process_text",
            _BackgroundTasksMustNotRun(),  # type: ignore[arg-type]
            phone="+77000000000",
            message_text="hi",
        )


@pytest.mark.asyncio
async def test_whatsapp_text_uses_realtime_background_when_enabled(monkeypatch) -> None:
    calls: list[tuple] = []

    class _Background:
        def add_task(self, *args, **kwargs) -> None:
            calls.append((args, kwargs))

    monkeypatch.setattr("app.services.task_queue.arq_can_run", lambda: True)
    monkeypatch.setattr("app.services.task_queue.settings.whatsapp_text_background_enabled", True)

    async def _must_not_enqueue(_name: str, **_kwargs) -> None:
        raise AssertionError("WhatsApp realtime text should not wait in ARQ")

    monkeypatch.setattr("app.services.task_queue.enqueue_job", _must_not_enqueue)

    await dispatch_arq_or_background(
        "whatsapp_process_text",
        _Background(),  # type: ignore[arg-type]
        phone="+77000000000",
        message_text="hi",
    )

    assert calls
