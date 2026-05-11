"""
E5: enqueue в `task_queue` пишет структурный лог-сигнал.

Проверяем, что и успех, и ошибка попадают в лог с полем
``event=task_queue_enqueue`` и стабильным набором ключей: `queue`, `job`,
`outcome`, опционально `job_id` и `error`. Лог-сборщик (Loki/Sentry) сможет
строить дашборды без парсинга свободного текста.
"""

from __future__ import annotations

import logging

import pytest

from app.services import task_queue as tq


class _FakePool:
    def __init__(self, *, fail: bool = False, returned_id: str | None = "job-7") -> None:
        self._fail = fail
        self._returned_id = returned_id
        self.calls: list[tuple[str, tuple, dict]] = []

    async def enqueue_job(self, name: str, *args, **kwargs):
        self.calls.append((name, args, kwargs))
        if self._fail:
            raise RuntimeError("redis dead")

        class _Job:
            def __init__(self, jid: str | None) -> None:
                self.job_id = jid

        return _Job(self._returned_id)


@pytest.mark.asyncio
async def test_enqueue_emits_structured_success_log(monkeypatch, caplog) -> None:
    pool = _FakePool()

    async def _fake_get_pool() -> _FakePool:
        return pool

    monkeypatch.setattr(tq, "_get_pool", _fake_get_pool)
    caplog.set_level(logging.INFO, logger=tq.__name__)

    await tq.enqueue_job("whatsapp_process_text", phone="+77001112233", message_text="hi")

    records = [r for r in caplog.records if getattr(r, "event", None) == "task_queue_enqueue"]
    assert records, "ожидался хотя бы один структурный лог enqueue"
    rec = records[-1]
    assert rec.outcome == "enqueued"
    assert rec.queue == tq._queue_name()
    assert rec.job == "whatsapp_process_text"
    assert rec.job_id == "job-7"


@pytest.mark.asyncio
async def test_enqueue_emits_structured_error_log_on_failure(monkeypatch, caplog) -> None:
    pool = _FakePool(fail=True)

    async def _fake_get_pool() -> _FakePool:
        return pool

    monkeypatch.setattr(tq, "_get_pool", _fake_get_pool)
    caplog.set_level(logging.ERROR, logger=tq.__name__)

    with pytest.raises(tq.TaskQueueEnqueueError):
        await tq.enqueue_job("whatsapp_process_text", phone="+77001112233")

    err = [r for r in caplog.records if getattr(r, "event", None) == "task_queue_enqueue"]
    assert err, "ошибочный enqueue должен писать структурный лог"
    rec = err[-1]
    assert rec.outcome == "enqueue_failed"
    assert rec.queue == tq._queue_name()
    assert rec.job == "whatsapp_process_text"
    assert "redis dead" in rec.error


@pytest.mark.asyncio
async def test_enqueue_logs_pool_unavailable_path(monkeypatch, caplog) -> None:
    """Если `_get_pool` бросает TaskQueueEnqueueError — отдельный outcome=pool_unavailable."""

    async def _explode() -> None:
        raise tq.TaskQueueEnqueueError("ARQ queue unavailable")

    monkeypatch.setattr(tq, "_get_pool", _explode)
    caplog.set_level(logging.ERROR, logger=tq.__name__)

    with pytest.raises(tq.TaskQueueEnqueueError):
        await tq.enqueue_job("payment_notify_customer", order_id=42)

    err = [r for r in caplog.records if getattr(r, "event", None) == "task_queue_enqueue"]
    assert err
    rec = err[-1]
    assert rec.outcome == "pool_unavailable"
    assert rec.job == "payment_notify_customer"
