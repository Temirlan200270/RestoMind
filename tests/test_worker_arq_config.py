"""E5: web enqueue и ARQ worker должны смотреть в одну очередь."""

from __future__ import annotations

from app.services.task_queue import _queue_name
from app.worker import WorkerSettings


def test_worker_queue_matches_web_enqueue_queue() -> None:
    assert WorkerSettings.queue_name == _queue_name()
