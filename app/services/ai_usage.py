"""
Учёт токенов AI по организации/дню — upsert в ai_usage_logs.
Вызывается fire-and-forget после каждого LLM-вызова на call-site где известен org_id.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import async_session_factory

logger = logging.getLogger(__name__)


async def log_ai_usage(
    organization_id: int,
    provider: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
) -> None:
    """Добавить токены к дневному счётчику для org. Upsert: один ряд = org + day."""
    try:
        today = date.today()
        async with async_session_factory() as db:
            await db.execute(
                text("""
                    INSERT INTO ai_usage_logs
                        (organization_id, day, provider, model,
                         prompt_tokens, completion_tokens, total_tokens, call_count)
                    VALUES
                        (:org_id, :day, :provider, :model,
                         :pt, :ct, :tt, 1)
                    ON CONFLICT (organization_id, day)
                    DO UPDATE SET
                        prompt_tokens     = ai_usage_logs.prompt_tokens + EXCLUDED.prompt_tokens,
                        completion_tokens = ai_usage_logs.completion_tokens + EXCLUDED.completion_tokens,
                        total_tokens      = ai_usage_logs.total_tokens + EXCLUDED.total_tokens,
                        call_count        = ai_usage_logs.call_count + 1,
                        model             = EXCLUDED.model,
                        updated_at        = now()
                """),
                {
                    "org_id": organization_id,
                    "day": today,
                    "provider": provider or "openai",
                    "model": model or "",
                    "pt": prompt_tokens,
                    "ct": completion_tokens,
                    "tt": total_tokens,
                },
            )
            await db.commit()
    except Exception:
        logger.exception("ai_usage log failed for org=%s", organization_id)


def schedule_log_ai_usage(
    organization_id: int,
    ai_response_usage: dict | None,
) -> None:
    """Fire-and-forget: создаёт asyncio.Task для логирования токенов."""
    import asyncio

    if not ai_response_usage:
        return
    try:
        asyncio.create_task(
            log_ai_usage(
                organization_id=organization_id,
                provider=str(ai_response_usage.get("provider") or "openai"),
                model=str(ai_response_usage.get("model") or ""),
                prompt_tokens=int(ai_response_usage.get("prompt_tokens") or 0),
                completion_tokens=int(ai_response_usage.get("completion_tokens") or 0),
                total_tokens=int(ai_response_usage.get("total_tokens") or 0),
            )
        )
    except RuntimeError:
        pass
