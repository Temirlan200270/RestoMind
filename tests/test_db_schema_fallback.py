"""Location scope fallback rolls back before retry on schema lag."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.services.db_schema_fallback import with_location_scope_fallback


@pytest.mark.asyncio
async def test_location_scope_fallback_rolls_back_before_retry() -> None:
    db = AsyncMock()
    db.rollback = AsyncMock()
    calls = {"n": 0}

    async def run(loc_id: int | None, allowed: set[int] | None) -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            raise SQLAlchemyError("column location_id does not exist")
        return "ok"

    result = await with_location_scope_fallback(
        db=db,
        location_id=1,
        allowed_location_ids={1},
        run=run,
    )

    assert result == "ok"
    assert calls["n"] == 2
    db.rollback.assert_awaited_once()
