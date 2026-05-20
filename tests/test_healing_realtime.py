from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db.models import OperationalInsight, Organization
from app.db.session import InMemoryRedis
from app.services.healing_realtime import maybe_trigger_realtime_healing
from app.services.system_events import BusinessEvent


@pytest.mark.asyncio
async def test_payment_failed_spike_triggers_insight_immediately(db_session, monkeypatch) -> None:
    from app.services import healing_realtime as hr

    org = Organization(name="Heal RT", slug="heal-rt")
    db_session.add(org)
    await db_session.flush()

    fake = InMemoryRedis()
    monkeypatch.setattr(hr, "redis_client", fake)
    monkeypatch.setattr("app.services.healing_actions.redis_client", fake)

    for i in range(3):
        await maybe_trigger_realtime_healing(
            db_session,
            BusinessEvent(
                org_id=int(org.id),
                type="payment.failed",
                actor="system",
                entity_type="payment",
                entity_id=f"pay-{i}",
                payload={},
            ),
        )
    await db_session.flush()

    row = await db_session.scalar(
        select(OperationalInsight).where(
            OperationalInsight.organization_id == int(org.id),
            OperationalInsight.insight_type == "payment_failed_spike",
        )
    )
    assert row is not None


@pytest.mark.asyncio
async def test_below_threshold_no_insight(db_session, monkeypatch) -> None:
    from app.services import healing_realtime as hr

    org = Organization(name="Heal Low", slug="heal-low")
    db_session.add(org)
    await db_session.flush()

    fake = InMemoryRedis()
    monkeypatch.setattr(hr, "redis_client", fake)
    monkeypatch.setattr("app.services.healing_actions.redis_client", fake)

    await maybe_trigger_realtime_healing(
        db_session,
        BusinessEvent(
            org_id=int(org.id),
            type="payment.failed",
            actor="system",
            entity_type="payment",
            entity_id="pay-1",
            payload={},
        ),
    )
    await db_session.flush()

    row = await db_session.scalar(
        select(OperationalInsight).where(
            OperationalInsight.organization_id == int(org.id),
        )
    )
    assert row is None


@pytest.mark.asyncio
async def test_idempotent_insight_dedup(db_session, monkeypatch) -> None:
    from app.services import healing_realtime as hr

    org = Organization(name="Heal Dedup", slug="heal-dedup")
    db_session.add(org)
    await db_session.flush()

    fake = InMemoryRedis()
    monkeypatch.setattr(hr, "redis_client", fake)
    monkeypatch.setattr("app.services.healing_actions.redis_client", fake)

    event = BusinessEvent(
        org_id=int(org.id),
        type="payment.failed",
        actor="system",
        entity_type="payment",
        entity_id="pay-x",
        payload={},
    )
    for _ in range(5):
        await maybe_trigger_realtime_healing(db_session, event)
    await db_session.flush()

    rows = (
        await db_session.execute(
            select(OperationalInsight).where(
                OperationalInsight.organization_id == int(org.id),
                OperationalInsight.insight_type == "payment_failed_spike",
            )
        )
    ).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_healing_mute_blocks_second_trigger(db_session, monkeypatch) -> None:
    from app.services import healing_actions as ha
    from app.services import healing_realtime as hr

    org = Organization(name="Heal Mute", slug="heal-mute")
    db_session.add(org)
    await db_session.flush()

    fake = InMemoryRedis()
    monkeypatch.setattr(hr, "redis_client", fake)
    monkeypatch.setattr("app.services.healing_actions.redis_client", fake)

    assert await ha.try_acquire_healing_mute(int(org.id), "payment_failed_spike") is True
    assert await ha.try_acquire_healing_mute(int(org.id), "payment_failed_spike") is False
