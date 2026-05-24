"""Event-driven core tail closure: admin order events, cumulative stats, ops aggregates."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.services.analytics_consumer import (
    HANDLED_EVENT_TYPES,
    _sum_event_rows,
    get_cumulative_event_totals,
    on_business_event,
)
from app.services.system_events import BusinessEvent


def test_ops_event_types_handled() -> None:
    for event_type in (
        "system.pricing_adjusted",
        "system.sla_violated",
        "system.healing_wa_sent",
        "order.draft_recovery_sent",
        "integration.whatsapp.failed",
    ):
        assert event_type in HANDLED_EVENT_TYPES


def test_sum_event_rows_period_totals() -> None:
    rows = [
        {
            "orders_created": 3,
            "orders_cancelled": 1,
            "orders_confirmed": 2,
            "revenue_kzt": 1000.0,
            "escalations": 1,
            "dialogs_count": 5,
        },
        {
            "orders_created": 2,
            "orders_cancelled": 0,
            "orders_confirmed": 2,
            "revenue_kzt": 500.0,
            "escalations": 0,
            "dialogs_count": 2,
        },
    ]
    totals = _sum_event_rows(rows)
    assert totals["orders_created"] == 5
    assert totals["orders_cancelled"] == 1
    assert totals["revenue_kzt"] == 1500.0
    assert totals["escalations"] == 1
    assert totals["dialogs_count"] == 7


@pytest.mark.asyncio
async def test_get_cumulative_event_totals() -> None:
    db = AsyncMock()
    row = {
        "orders_created": 10,
        "orders_cancelled": 2,
        "orders_confirmed": 8,
        "revenue_kzt": 12000.0,
        "row_count": 5,
    }
    mappings = MagicMock()
    mappings.first.return_value = row
    execute_result = MagicMock()
    execute_result.mappings.return_value = mappings
    db.execute = AsyncMock(return_value=execute_result)

    result = await get_cumulative_event_totals(db, 1)

    assert result is not None
    assert result["total_orders"] == 8
    assert result["revenue_kzt"] == 12000.0


@pytest.mark.asyncio
async def test_on_business_event_ops_column_upsert() -> None:
    db = AsyncMock()
    event = BusinessEvent(
        id="system.sla_violated:1",
        org_id=1,
        type="system.sla_violated",
        actor="system",
        payload={},
    )
    with patch("app.services.analytics_consumer._upsert_daily_stat", new_callable=AsyncMock) as upsert:
        await on_business_event(event, db)
        upsert.assert_awaited_once()
        assert upsert.await_args.args[3] == "sla_violations"


@pytest.mark.asyncio
async def test_get_cumulative_event_totals_rolls_back_on_error() -> None:
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=SQLAlchemyError("missing column"))
    db.rollback = AsyncMock()

    result = await get_cumulative_event_totals(db, 1)

    assert result is None
    db.rollback.assert_awaited_once()
