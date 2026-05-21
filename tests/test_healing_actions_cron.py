"""Cron cold healing: run_healing_actions (cancellation_surge, ai_message_drop)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.db.models import OperationalInsight, Organization
from app.db.session import InMemoryRedis
from app.services.healing_actions import run_healing_actions


def _cancel_surge_rows() -> list[dict]:
    """7 дней DESC: ≥25% отмен (4+ заказов)."""
    rows = []
    for day in range(20, 13, -1):
        rows.append({
            "date": f"2026-05-{day:02d}",
            "orders_confirmed": 3,
            "orders_cancelled": 1,
            "ai_messages_count": 10,
            "payments_failed": 0,
        })
    return rows


def _ai_drop_rows() -> list[dict]:
    """recent_7 низкий, prev_7 высокий (ORDER BY day DESC)."""
    rows = []
    for day_offset in range(14):
        rows.append({
            "date": f"2026-05-{20 - day_offset:02d}",
            "orders_confirmed": 1,
            "orders_cancelled": 0,
            "ai_messages_count": 5 if day_offset < 7 else 50,
            "payments_failed": 0,
        })
    return rows


@pytest.mark.asyncio
async def test_cancellation_surge_creates_insight(db_session, monkeypatch) -> None:
    from app.services import healing_actions as ha

    org = Organization(name="Cancel Surge", slug="cancel-surge")
    db_session.add(org)
    await db_session.flush()
    org_id = int(org.id)

    fake = InMemoryRedis()
    monkeypatch.setattr(ha, "redis_client", fake)
    monkeypatch.setattr(ha, "get_event_stats", AsyncMock(return_value=_cancel_surge_rows()))
    monkeypatch.setattr(ha, "get_today_event_summary", AsyncMock(return_value={"payments_failed": 0}))
    monkeypatch.setattr(
        "app.services.recommendations.generate_recommendations",
        AsyncMock(return_value=[]),
    )

    actions = await run_healing_actions(db_session, org_id)
    await db_session.flush()

    assert any(a.startswith("insight:cancellation_surge:") for a in actions)
    row = await db_session.scalar(
        select(OperationalInsight).where(
            OperationalInsight.organization_id == org_id,
            OperationalInsight.insight_type == "cancellation_surge",
        )
    )
    assert row is not None
    assert row.severity == "warning"


@pytest.mark.asyncio
async def test_ai_message_drop_creates_critical_insight(db_session, monkeypatch) -> None:
    from app.services import healing_actions as ha

    org = Organization(name="AI Drop", slug="ai-drop")
    db_session.add(org)
    await db_session.flush()
    org_id = int(org.id)

    fake = InMemoryRedis()
    monkeypatch.setattr(ha, "redis_client", fake)
    monkeypatch.setattr(ha, "get_event_stats", AsyncMock(return_value=_ai_drop_rows()))
    monkeypatch.setattr(ha, "get_today_event_summary", AsyncMock(return_value={"payments_failed": 0}))

    actions = await run_healing_actions(db_session, org_id)
    await db_session.flush()

    assert any(a.startswith("insight:ai_message_drop:") for a in actions)
    row = await db_session.scalar(
        select(OperationalInsight).where(
            OperationalInsight.organization_id == org_id,
            OperationalInsight.insight_type == "ai_message_drop",
        )
    )
    assert row is not None
    assert row.severity == "critical"


@pytest.mark.asyncio
async def test_healing_mute_blocks_second_cancellation_insight(db_session, monkeypatch) -> None:
    from app.services import healing_actions as ha

    org = Organization(name="Mute Cancel", slug="mute-cancel")
    db_session.add(org)
    await db_session.flush()
    org_id = int(org.id)

    fake = InMemoryRedis()
    monkeypatch.setattr(ha, "redis_client", fake)
    monkeypatch.setattr(ha, "get_event_stats", AsyncMock(return_value=_cancel_surge_rows()))
    monkeypatch.setattr(ha, "get_today_event_summary", AsyncMock(return_value={"payments_failed": 0}))
    monkeypatch.setattr(
        "app.services.recommendations.generate_recommendations",
        AsyncMock(return_value=[]),
    )

    first = await run_healing_actions(db_session, org_id)
    await db_session.flush()
    second = await run_healing_actions(db_session, org_id)
    await db_session.flush()

    assert any(a.startswith("insight:cancellation_surge:") for a in first)
    assert not any(a.startswith("insight:cancellation_surge:") for a in second)
    rows = (
        await db_session.execute(
            select(OperationalInsight).where(
                OperationalInsight.organization_id == org_id,
                OperationalInsight.insight_type == "cancellation_surge",
            )
        )
    ).scalars().all()
    assert len(rows) == 1
