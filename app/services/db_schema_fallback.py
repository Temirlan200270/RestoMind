"""Graceful degradation when prod DB lags behind Alembic head (missing columns/tables)."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar

from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)

T = TypeVar("T")


def looks_like_missing_column(exc: Exception, *column_names: str) -> bool:
    msg = str(exc).lower()
    if "does not exist" not in msg and "no such column" not in msg:
        return False
    return any(name.lower() in msg for name in column_names)


async def with_location_scope_fallback(
    *,
    location_id: int | None,
    allowed_location_ids: set[int] | None,
    run: Callable[[int | None, set[int] | None], Awaitable[T]],
) -> T:
    """Retry without location filters when ``location_id`` columns are not migrated yet."""
    if location_id is None and allowed_location_ids is None:
        return await run(None, None)
    try:
        return await run(location_id, allowed_location_ids)
    except SQLAlchemyError as exc:
        if not looks_like_missing_column(exc, "location_id"):
            raise
        logger.warning(
            "location scope query failed (schema lag); retrying org-wide: %s",
            exc,
        )
        return await run(None, None)
