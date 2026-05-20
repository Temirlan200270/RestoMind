"""Event-driven self-healing — realtime thresholds + heal:mute dedup."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import redis_client
from app.services.healing_actions import (
    _ESCALATION_SPIKE_THRESHOLD,
    _PAYMENT_FAILED_THRESHOLD,
    _create_insight_if_new,
    try_acquire_healing_mute,
)
from app.services.system_events import BusinessEvent

logger = logging.getLogger(__name__)

REALTIME_EVENT_THRESHOLDS: dict[str, tuple[int, str, str, str, str]] = {
    "payment.failed": (
        _PAYMENT_FAILED_THRESHOLD,
        "payment_failed_spike",
        "Проблемы с платежами (realtime)",
        "Сработал порог ошибок оплаты за текущий час — проверьте платёжный шлюз и webhook.",
        "critical",
    ),
    "ai.escalated": (
        _ESCALATION_SPIKE_THRESHOLD,
        "escalation_spike",
        "Spike эскалаций (realtime)",
        "Сработал порог эскалаций за текущий час — проверьте нагрузку на операторов и ответы бота.",
        "warning",
    ),
}


def _counter_key(org_id: int, event_type: str) -> str:
    hour = datetime.now(tz=timezone.utc).strftime("%Y%m%d%H")
    return f"heal:counter:{int(org_id)}:{event_type}:{hour}"


async def maybe_trigger_realtime_healing(db: AsyncSession, event: BusinessEvent) -> bool:
    spec = REALTIME_EVENT_THRESHOLDS.get(event.type)
    if spec is None:
        return False

    threshold, insight_type, title_prefix, summary, severity = spec
    key = _counter_key(event.org_id, event.type)
    try:
        count = await redis_client.incr(key)
        if count == 1:
            await redis_client.expire(key, 3600)
    except Exception as exc:
        logger.warning("healing.realtime_counter_failed org=%s type=%s err=%s", event.org_id, event.type, exc)
        return False

    if int(count) < int(threshold):
        return False

    if not await try_acquire_healing_mute(int(event.org_id), insight_type):
        logger.debug("healing.mute_skipped org=%s insight_type=%s", event.org_id, insight_type)
        return False

    created = await _create_insight_if_new(
        db,
        int(event.org_id),
        insight_type=insight_type,
        title=title_prefix,
        summary=f"{summary} (событий за час: {count})",
        severity=severity,
        dedup_hours=6,
    )
    if created:
        logger.warning(
            "healing.realtime_triggered org=%s type=%s count=%s",
            event.org_id,
            event.type,
            count,
        )
    return created
