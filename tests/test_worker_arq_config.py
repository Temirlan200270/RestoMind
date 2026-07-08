"""E5: web enqueue и ARQ worker должны смотреть в одну очередь."""

from __future__ import annotations

from app.services.task_queue import _queue_name
from app.worker import WorkerSettings


def test_worker_queue_matches_web_enqueue_queue() -> None:
    assert WorkerSettings.queue_name == _queue_name()


def test_worker_health_heartbeat_is_frequent_enough_for_admin_ui() -> None:
    assert WorkerSettings.health_check_interval <= 60
