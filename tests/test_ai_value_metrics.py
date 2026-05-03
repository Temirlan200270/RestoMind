"""GET /api/admin/ai-value — агрегаты вклад ИИ за период."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.api.admin import admin_ai_value
from app.db.models import ChatLog, Order, Organization, OrderStatus, User


class DummyRequest:
    def __init__(self, organization_id: int) -> None:
        self.session = {"admin_ok": True, "organization_id": organization_id}


class DummyResponse:
    headers: dict = {}

    def __init__(self) -> None:
        self.headers = {}


@pytest.mark.asyncio
async def test_ai_value_endpoint_orders_and_metrics(db_session) -> None:
    org = Organization(name="AI Value Org", slug="ai-value-test")
    db_session.add(org)
    await db_session.flush()
    oid = int(org.id)
    user = User(organization_id=oid, phone="+77001112299")
    db_session.add(user)
    await db_session.flush()

    db_session.add(
        ChatLog(
            organization_id=oid,
            user_id=int(user.id),
            role="assistant",
            content="Привет",
            meta_json={},
        ),
    )
    items_json = {
        "items": [{"name": "Плов", "quantity": 1, "item_total": 3000, "iiko_item_id": "x"}],
        "order_meta": {
            "recommendation_trace": [
                {
                    "offered": "Чай",
                    "offered_iiko_id": "tea-1",
                    "accepted": True,
                    "accepted_revenue_kzt": 500,
                },
            ],
        },
    }
    db_session.add(
        Order(
            organization_id=oid,
            user_id=int(user.id),
            status=OrderStatus.CONFIRMED.value,
            total_price=3500,
            items_json=items_json,
            created_at=datetime.now(tz=timezone.utc),
        ),
    )
    await db_session.flush()

    out = await admin_ai_value(
        DummyRequest(oid),
        DummyResponse(),
        period="30d",
        date_from=None,
        date_to=None,
        db=db_session,
    )

    assert out["period"] in ("7d", "30d", "90d", "custom")
    assert out["metrics"]["ai_revenue"] >= 500
    assert out["metrics"]["upsell_accepted"] >= 1
    assert out["metrics"]["ai_messages"] >= 1
    assert isinstance(out["daily_series"], list)
    assert len(out["daily_series"]) >= 1
    assert out["totals"]["orders"] >= 1
    assert "bot_orders" in out["totals"]
    assert out["escalations"]["count"] >= 0


@pytest.mark.asyncio
async def test_ai_value_separates_bot_and_operator_touch_orders(db_session) -> None:
    """Заказ без сообщения оператора до времени заказа считается bot_orders."""
    org = Organization(name="AI BotSep", slug="ai-bot-sep")
    db_session.add(org)
    await db_session.flush()
    oid = int(org.id)
    u1 = User(organization_id=oid, phone="+77001113344")
    u2 = User(organization_id=oid, phone="+77001113355")
    db_session.add_all([u1, u2])
    await db_session.flush()

    ts = datetime.now(tz=timezone.utc)
    db_session.add(
        Order(
            organization_id=oid,
            user_id=int(u1.id),
            status=OrderStatus.CONFIRMED.value,
            total_price=1000,
            items_json={"items": []},
            created_at=ts,
            updated_at=ts,
        ),
    )
    db_session.add(
        ChatLog(
            organization_id=oid,
            user_id=int(u2.id),
            role="operator",
            content="Я оператор",
            meta_json={},
            created_at=ts,
        ),
    )
    db_session.add(
        Order(
            organization_id=oid,
            user_id=int(u2.id),
            status=OrderStatus.CONFIRMED.value,
            total_price=2000,
            items_json={"items": []},
            created_at=ts,
            updated_at=ts,
        ),
    )
    await db_session.flush()

    out = await admin_ai_value(
        DummyRequest(oid),
        DummyResponse(),
        period="30d",
        date_from=None,
        date_to=None,
        db=db_session,
    )
    assert out["totals"]["orders"] == 2
    assert out["totals"]["bot_orders"] == 1
    assert out["totals"]["takeover_orders"] == 1
