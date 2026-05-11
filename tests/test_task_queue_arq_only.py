"""E5: очередь задач не имеет BackgroundTasks fallback."""

from __future__ import annotations

import pytest

from app.services.task_queue import TaskQueueEnqueueError, dispatch_arq_or_background


class _BackgroundTasksMustNotRun:
    def add_task(self, *_args, **_kwargs) -> None:
        raise AssertionError("BackgroundTasks fallback must not run")


@pytest.mark.asyncio
async def test_dispatch_propagates_arq_error_without_background_fallback(monkeypatch) -> None:
    async def _fail_enqueue(_name: str, **_kwargs) -> None:
        raise TaskQueueEnqueueError("redis unavailable")

    monkeypatch.setattr("app.services.task_queue.enqueue_job", _fail_enqueue)

    with pytest.raises(TaskQueueEnqueueError):
        await dispatch_arq_or_background(
            "whatsapp_process_text",
            _BackgroundTasksMustNotRun(),  # type: ignore[arg-type]
            phone="+77000000000",
            message_text="hi",
        )
