"""Small helper for observable fire-and-forget asyncio tasks."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable
from typing import Any

logger = logging.getLogger(__name__)

_BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()


def spawn_tracked(
    awaitable: Awaitable[Any],
    *,
    name: str | None = None,
    log: logging.Logger | None = None,
) -> asyncio.Task[Any] | None:
    """Create a background task, keep a strong ref, and log task failures."""
    task_logger = log or logger
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        close = getattr(awaitable, "close", None)
        if callable(close):
            close()
        task_logger.debug("background task skipped: no running event loop name=%s", name)
        return None

    task = loop.create_task(awaitable, name=name)
    _BACKGROUND_TASKS.add(task)

    def _on_done(done: asyncio.Task[Any]) -> None:
        _BACKGROUND_TASKS.discard(done)
        try:
            done.result()
        except asyncio.CancelledError:
            return
        except Exception:
            task_logger.exception("background task failed name=%s", done.get_name())

    task.add_done_callback(_on_done)
    return task
