"""
Секундомер этапов inbound-пайплайна WhatsApp: складывает длительности в ``rm_stage_ms`` для JSON-лога.
"""

from __future__ import annotations

import time
from typing import Any

from app.core.pipeline_log import log_pipeline_stage


class PipelineStopwatch:
    """Накопление длительностей между последовательными ``split()``."""

    __slots__ = ("_last", "rm_stage_ms")

    def __init__(self, *, t0: float | None = None) -> None:
        self._last = time.monotonic() if t0 is None else t0
        self.rm_stage_ms: dict[str, float] = {}

    def split(self, stage: str) -> None:
        now = time.monotonic()
        dt_ms = round((now - self._last) * 1000, 2)
        self.rm_stage_ms[stage] = dt_ms
        self._last = now


def log_pipeline_rm_stage_ms(
    *,
    phone_tail: str,
    rm_stage_ms: dict[str, float],
    extra: dict[str, Any] | None = None,
) -> None:
    """Одна строка в ``restomind.pipeline`` с полем ``rm_stage_ms``."""
    payload: dict[str, Any] = {"rm_stage_ms": rm_stage_ms}
    if extra:
        payload.update({k: v for k, v in extra.items() if v is not None})
    log_pipeline_stage("pipeline_timing", phone=phone_tail, extra=payload)
