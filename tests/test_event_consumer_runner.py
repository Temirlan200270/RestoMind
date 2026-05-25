"""Tests for post-commit event consumer scheduling."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.db.models import Organization
from app.services.system_events import BusinessEvent, emit_event


@pytest.mark.asyncio
async def test_emit_event_schedules_async_consumers_after_commit(db_session):
    """При event_consumers_async consumers не блокируют emit в той же сессии."""
    from app.core.config import settings

    settings.event_consumers_async = True
    event = BusinessEvent(
        org_id=1,
        type="order.created",
        actor="system",
        payload={"order_id": 1},
    )

    with patch(
        "app.services.event_consumer_runner.schedule_event_consumers_after_commit",
    ) as schedule_mock:
        with patch(
            "app.services.event_consumer_runner.run_event_consumers",
            new_callable=AsyncMock,
        ) as run_sync_mock:
            db_session.add(Organization(id=1, name="T", slug="t"))
            await db_session.flush()
            result = await emit_event(db_session, event)

    assert result is not None
    schedule_mock.assert_called_once()
    run_sync_mock.assert_not_called()


@pytest.mark.asyncio
async def test_emit_event_runs_sync_consumers_when_async_disabled(db_session):
    from app.core.config import settings

    settings.event_consumers_async = False
    event = BusinessEvent(
        org_id=1,
        type="order.created",
        actor="system",
        payload={"order_id": 2},
    )

    with patch(
        "app.services.event_consumer_runner.schedule_event_consumers_after_commit",
    ) as schedule_mock:
        with patch(
            "app.services.event_consumer_runner.run_event_consumers",
            new_callable=AsyncMock,
        ) as run_sync_mock:
            db_session.add(Organization(id=1, name="T", slug="t2"))
            await db_session.flush()
            await emit_event(db_session, event)

    schedule_mock.assert_not_called()
    run_sync_mock.assert_called_once()


@pytest.mark.asyncio
async def test_run_event_consumers_isolated_commits():
    from app.services.event_consumer_runner import run_event_consumers_isolated

    event = BusinessEvent(org_id=1, type="order.created", actor="ai", payload={})
    session = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)

    with patch("app.db.session.async_session_factory", return_value=cm):
        with patch(
            "app.services.event_consumer_runner.run_event_consumers",
            new_callable=AsyncMock,
        ) as run_mock:
            await run_event_consumers_isolated(event)

    run_mock.assert_awaited_once_with(event, session)
    session.commit.assert_awaited_once()
