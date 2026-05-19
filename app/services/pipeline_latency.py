"""
Pipeline latency tracking & SLA monitor (P4 sprint).

Записывает задержки обработки WhatsApp-сообщения по стадиям в `pipeline_latency_logs`.
Предоставляет агрегированные метрики (p50/p95/max) и проверку SLA-порогов.

Запись происходит fire-and-forget из webhooks.py — не блокирует основной ответ.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import PipelineLatencyLog, SystemEvent
from app.db.session import async_session_factory

logger = logging.getLogger(__name__)


# ──────────────── fire-and-forget запись ────────────────


def schedule_log_pipeline_latency(
    organization_id: int,
    stage_ms: dict[str, int],
    pipeline_type: str = "whatsapp_text",
) -> None:
    """Fire-and-forget: записывает задержки в фоне. Не ждёт результата."""
    if not settings.pipeline_latency_enabled:
        return
    if not stage_ms:
        return
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(
            _write_latency(int(organization_id), stage_ms, pipeline_type),
            name=f"pipeline_latency_log_{organization_id}",
        )
    except RuntimeError:
        pass


async def _write_latency(org_id: int, stage_ms: dict[str, int], pipeline_type: str) -> None:
    try:
        async with async_session_factory() as db:
            row = PipelineLatencyLog(
                organization_id=org_id,
                pipeline_type=pipeline_type,
                dedupe_ms=stage_ms.get("dedupe"),
                context_ms=stage_ms.get("context"),
                llm_ms=stage_ms.get("llm_ok") or stage_ms.get("llm"),
                route_ms=stage_ms.get("route_ok") or stage_ms.get("route"),
                reply_ms=stage_ms.get("reply"),
                total_ms=stage_ms.get("total"),
            )
            db.add(row)
            await db.commit()
    except Exception as exc:
        logger.debug("pipeline_latency write failed: %s", exc)


# ──────────────── агрегация метрик ────────────────


async def get_latency_summary(
    db: AsyncSession,
    org_id: int,
    hours: int = 24,
) -> list[dict[str, Any]]:
    """
    Возвращает p50/p95/max по каждой стадии за последние N часов.
    Работает на SQLite (Python-сортировка) и PostgreSQL.
    """
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    rows = (await db.execute(
        select(PipelineLatencyLog).where(
            PipelineLatencyLog.organization_id == org_id,
            PipelineLatencyLog.created_at >= since,
        )
    )).scalars().all()

    if not rows:
        return []

    stage_fields = {
        "dedupe": "dedupe_ms",
        "context": "context_ms",
        "llm": "llm_ms",
        "route": "route_ms",
        "reply": "reply_ms",
        "total": "total_ms",
    }

    result = []
    for stage, field in stage_fields.items():
        values = [getattr(r, field) for r in rows if getattr(r, field) is not None]
        if not values:
            continue
        values.sort()
        n = len(values)
        p50 = values[int(n * 0.50)]
        p95 = values[min(int(n * 0.95), n - 1)]
        result.append({
            "stage": stage,
            "p50_ms": p50,
            "p95_ms": p95,
            "max_ms": values[-1],
            "samples": n,
        })

    return result


async def get_sla_violations_count(db: AsyncSession, org_id: int, hours: int = 24) -> int:
    """Количество SLA-нарушений (SystemEvent sla_violation) за последние N часов."""
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    count = await db.scalar(
        select(func.count(SystemEvent.id)).where(
            SystemEvent.organization_id == org_id,
            SystemEvent.event_type == "sla_violation",
            SystemEvent.created_at >= since,
        )
    )
    return int(count or 0)


async def check_sla_thresholds(db: AsyncSession, org_id: int) -> None:
    """
    Проверяет p95 за последние 1 час против порогов.
    При превышении — emit SystemEvent(sla_violation).
    """
    summary = await get_latency_summary(db, org_id, hours=1)
    for stage_data in summary:
        stage = stage_data["stage"]
        p95 = stage_data["p95_ms"]
        threshold = settings.sla_llm_p95_ms if stage == "llm" else settings.sla_total_p95_ms
        if stage not in ("llm", "total"):
            continue
        if p95 > threshold:
            idempotency_key = f"sla_violation:{org_id}:{stage}:{datetime.now(timezone.utc).strftime('%Y%m%d%H')}"
            try:
                from app.services.system_events import BusinessEvent, emit_event
                await emit_event(
                    db,
                    BusinessEvent(
                        id=idempotency_key,
                        org_id=org_id,
                        type="system.sla_violated",
                        actor="system",
                        entity_type="pipeline_stage",
                        entity_id=stage,
                        payload={"stage": stage, "p95_ms": p95, "threshold_ms": threshold},
                    ),
                )
                logger.warning("SLA violation: org=%s stage=%s p95=%dms threshold=%dms", org_id, stage, p95, threshold)
            except Exception as exc:
                logger.debug("SLA event emit failed: %s", exc)
