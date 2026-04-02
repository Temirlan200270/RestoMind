"""
Структурированные логи этапов пайплайна (наблюдаемость): STT → LLM → merge → маршрутизация.
Пишет одну JSON-строку в логгер restomind.pipeline — удобно для grep/ELK.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger("restomind.pipeline")


def log_pipeline_stage(
    stage: str,
    *,
    phone: str = "",
    extra: dict | None = None,
) -> None:
    payload: dict = {"stage": stage}
    if phone:
        payload["phone"] = phone
    if extra:
        for k, v in extra.items():
            if v is not None:
                payload[k] = v
    try:
        logger.info(json.dumps(payload, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        logger.info("%s %s", stage, phone)
