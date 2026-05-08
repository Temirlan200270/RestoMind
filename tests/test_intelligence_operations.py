from datetime import datetime, timedelta, timezone

import pytest

from app.db.models import OperationalInsight, Order, OrderStatus, Organization, User
from app.services.intelligence import (
    SimulationInput,
    answer_intelligence_query,
    build_state_snapshot,
    list_insights,
    revenue_orders_summary,
    simulate_operator_capacity,
)


@pytest.mark.asyncio
async def test_revenue_orders_summary_is_tenant_scoped(db_session):
    db_session.add_all(
        [
            Organization(id=1, name="Org 1"),
            Organization(id=2, name="Org 2"),
            User(id=1, organization_id=1, phone="+77000000001"),
            User(id=2, organization_id=2, phone="+77000000002"),
        ]
    )
    now = datetime.now(timezone.utc)
    db_session.add_all(
        [
            Order(
                organization_id=1,
                user_id=1,
                status=OrderStatus.COMPLETED.value,
                total_price=5000,
                items_json={"items": [{"name": "Plov", "quantity": 1, "item_total": 5000}]},
                created_at=now,
            ),
            Order(
                organization_id=2,
                user_id=2,
                status=OrderStatus.COMPLETED.value,
                total_price=999999,
                items_json={"items": [{"name": "Other", "quantity": 1, "item_total": 999999}]},
                created_at=now,
            ),
            Order(
                organization_id=1,
                user_id=1,
                status=OrderStatus.COMPLETED.value,
                total_price=7000,
                items_json={"items": [{"name": "Lagman", "quantity": 1, "item_total": 7000}]},
                created_at=now - timedelta(days=1),
            ),
        ]
    )
    await db_session.flush()

    summary = await revenue_orders_summary(db_session, 1, "today")

    assert summary["current"]["orders"] == 1
    assert summary["current"]["revenue"] == 5000
    assert summary["previous"]["revenue"] == 7000
    assert summary["top_items"][0]["name"] == "Plov"


@pytest.mark.asyncio
async def test_intelligence_query_saves_conversation(db_session):
    db_session.add_all([Organization(id=1, name="Org 1"), User(id=1, organization_id=1, phone="+77000000001")])
    db_session.add(
        Order(
            organization_id=1,
            user_id=1,
            status=OrderStatus.COMPLETED.value,
            total_price=5000,
            items_json={"items": []},
            created_at=datetime.now(timezone.utc),
        )
    )
    await db_session.flush()

    result = await answer_intelligence_query(db_session, org_id=1, question="Почему сегодня мало заказов?")

    assert result["conversation_id"]
    assert "заказ" in result["answer"].lower()


@pytest.mark.asyncio
async def test_insights_and_digital_twin_snapshot(db_session):
    db_session.add_all([Organization(id=1, name="Org 1"), User(id=1, organization_id=1, phone="+77000000001")])
    db_session.add(
        Order(
            organization_id=1,
            user_id=1,
            status=OrderStatus.DRAFT.value,
            total_price=4000,
            items_json={"items": []},
            created_at=datetime.now(timezone.utc),
        )
    )
    await db_session.flush()

    insights = await list_insights(db_session, 1)
    snapshot = await build_state_snapshot(db_session, 1, persist=True)

    assert insights
    assert isinstance(insights[0], OperationalInsight)
    assert snapshot.active_orders == 1
    assert snapshot.queue_size >= 1


def test_operator_capacity_simulation_flags_overload():
    result = simulate_operator_capacity(SimulationInput(orders_per_hour=60, operators=1, avg_check=5000))

    assert result["severity"] in {"warning", "critical"}
    assert result["lost_revenue"] > 0
