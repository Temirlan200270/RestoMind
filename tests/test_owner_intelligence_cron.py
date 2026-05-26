"""Owner Intelligence cron — order_ai_audit_backfill_tick."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.worker import order_ai_audit_backfill_tick


@pytest.mark.asyncio
async def test_order_ai_audit_backfill_tick_runs_for_active_orgs() -> None:
    mock_audit = AsyncMock(return_value={"processed": 2, "high_or_critical": 0, "candidates": 2})
    mock_upsell = AsyncMock(return_value={"processed": 1, "events_updated": 1, "candidates": 1})

    class _Scalars:
        def all(self):
            return [1, 2]

    class _Result:
        def scalars(self):
            return _Scalars()

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def execute(self, _stmt):
            return _Result()

        async def commit(self):
            return None

    with patch("app.db.session.async_session_factory", return_value=_Session()), patch(
        "app.services.order_ai_audit.backfill_order_ai_audits",
        mock_audit,
    ), patch(
        "app.services.upsell_attribution.backfill_upsell_attribution",
        mock_upsell,
    ):
        await order_ai_audit_backfill_tick({})

    assert mock_audit.await_count == 2
    assert mock_upsell.await_count == 2
