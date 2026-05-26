"""Метрики надёжности LLM / Decision Engine для Owner Intelligence."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ChatLog


async def build_llm_reliability_metrics(
    db: AsyncSession,
    organization_id: int,
    *,
    period_days: int = 7,
) -> dict[str, object]:
    """Агрегаты по chat_logs.meta за период (SQLite-safe)."""
    since = datetime.now(timezone.utc) - timedelta(days=max(1, period_days))

    result = await db.execute(
        select(ChatLog.meta_json, ChatLog.content)
        .where(
            ChatLog.organization_id == organization_id,
            ChatLog.role == "assistant",
            ChatLog.created_at >= since,
        )
        .limit(5000),
    )
    rows = result.all()

    total = len(rows)
    technical_fallback = 0
    escalated = 0
    fallback_phrase = "технические сложности"

    for meta, content in rows:
        meta_d = meta if isinstance(meta, dict) else {}
        if meta_d.get("technical_fallback"):
            technical_fallback += 1
        if str(meta_d.get("intent") or "").lower() == "escalate":
            escalated += 1
        elif fallback_phrase in str(content or "").lower():
            technical_fallback += 1

    def pct(n: int, d: int) -> float:
        return round(100.0 * n / d, 1) if d else 0.0

    return {
        "period_days": period_days,
        "assistant_messages": total,
        "pct_technical_fallback": pct(technical_fallback, total),
        "pct_escalated": pct(escalated, total),
        "technical_fallback_count": technical_fallback,
        "escalated_count": escalated,
    }
