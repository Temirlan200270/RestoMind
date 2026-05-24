"""Postgres-safe DailyOrgStats reads (rollback after failed SELECT)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.services.analytics_consumer import _safe_daily_stats_mappings


@pytest.mark.asyncio
async def test_safe_daily_stats_mappings_rolls_back_on_error() -> None:
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=SQLAlchemyError("column dialogs_count does not exist"))
    db.rollback = AsyncMock()

    result = await _safe_daily_stats_mappings(db, "SELECT 1", {"org_id": 1})

    assert result is None
    db.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_safe_daily_stats_mappings_returns_mappings_on_success() -> None:
    db = AsyncMock()
    mappings = MagicMock()
    execute_result = MagicMock()
    execute_result.mappings.return_value = mappings
    db.execute = AsyncMock(return_value=execute_result)

    result = await _safe_daily_stats_mappings(db, "SELECT 1", {"org_id": 1})

    assert result is mappings
    db.rollback.assert_not_called()
