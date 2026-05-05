from __future__ import annotations

import pytest
from unittest.mock import AsyncMock

from app.api.admin import _monolith as admin_api
from app.db.models import ChatLog, MenuItem, Order, OrderStatus, Organization, User
from app.services.order_logic import compute_fee_lines


class DummyRequest:
    def __init__(self, session: dict):
        self.session = session


@pytest.mark.asyncio
async def test_chat_triage_close_and_filters(db_session, monkeypatch) -> None:
    monkeypatch.setattr(admin_api, "publish_event", AsyncMock())

    org = Organization(id=1, name="Org", slug="org")
    user = User(organization_id=1, phone="+77001112233", name="A")
    db_session.add_all([org, user])
    await db_session.flush()
    db_session.add(ChatLog(organization_id=1, user_id=user.id, role="user", content="hello"))
    await db_session.flush()

    req = DummyRequest({"organization_id": 1, "staff_id": 7})
    await admin_api.close_chat(req, "+77001112233", db_session)

    active = await admin_api.list_chats_sidebar(req, limit=50, cursor_at=None, cursor_id=None, mode="active", db=db_session)
    closed = await admin_api.list_chats_sidebar(req, limit=50, cursor_at=None, cursor_id=None, mode="closed", db=db_session)

    assert active["chats"] == []
    assert closed["chats"][0]["triageState"] == "closed"
    assert closed["chats"][0]["assignee"] == "staff:7"


@pytest.mark.asyncio
async def test_post_iiko_fulfillment_transition_records_event(db_session, monkeypatch) -> None:
    async def _noop_publish(*_args, **_kwargs):
        return None

    monkeypatch.setattr(admin_api, "publish_event", _noop_publish)

    org = Organization(id=2, name="Org", slug="org2")
    user = User(organization_id=2, phone="+77004445566")
    db_session.add_all([org, user])
    await db_session.flush()
    order = Order(
        organization_id=2,
        user_id=user.id,
        status=OrderStatus.SENT_TO_IIKO.value,
        total_price=1000,
        items_json={"items": [], "order_meta": {}},
    )
    db_session.add(order)
    await db_session.flush()

    req = DummyRequest({"organization_id": 2, "staff_id": 3})
    out = await admin_api.patch_order_status(
        req,
        int(order.id),
        admin_api.OrderPatchBody(status=OrderStatus.IN_TRANSIT.value, expected_version=1),
        db_session,
    )

    await db_session.refresh(order)
    assert out["status"] == OrderStatus.IN_TRANSIT.value
    assert order.items_json["order_meta"]["fulfillment_events"][-1]["to"] == OrderStatus.IN_TRANSIT.value


@pytest.mark.asyncio
async def test_upsell_feedback_creates_anti_rule_tag(db_session) -> None:
    org = Organization(id=3, name="Org", slug="org3")
    user = User(organization_id=3, phone="+77007778899")
    item = MenuItem(organization_id=3, name="Tea", category="Drinks", price=500, iiko_id="tea-1", tags="")
    db_session.add_all([org, user, item])
    await db_session.flush()
    order = Order(
        organization_id=3,
        user_id=user.id,
        status=OrderStatus.COMPLETED.value,
        total_price=1500,
        items_json={
            "items": [{"name": "Plov", "category": "Main"}],
            "order_meta": {"recommendation_trace": [{"item_iiko_id": "tea-1", "item_name": "Tea"}]},
        },
    )
    db_session.add(order)
    await db_session.flush()

    req = DummyRequest({"organization_id": 3, "staff_id": 9})
    out = await admin_api.create_upsell_rule_from_order_feedback(
        req,
        int(order.id),
        admin_api.UpsellFeedbackBody(mode="forbid"),
        db_session,
    )

    await db_session.refresh(item)
    assert out["feedback"]["kind"] == "anti_rule"
    assert "not_upsell" in item.tags


def test_packaging_order_and_category_scope() -> None:
    from app.db.models import PackagingRule

    category_rule = PackagingRule(
        organization_id=1,
        kind="drink_pack",
        name="Drink holder",
        price=50,
        keywords="tea",
        scope="category",
        category_match="Drinks",
        sort_order=10,
    )
    order_rule = PackagingRule(
        organization_id=1,
        kind="order_bag",
        name="Order bag",
        price=100,
        keywords="",
        scope="order",
        sort_order=1,
    )
    fee_lines, total = compute_fee_lines(
        [{"name": "Tea", "category": "Drinks", "quantity": 2}],
        1000,
        "pickup",
        packaging_rules=[category_rule, order_rule],
    )

    assert {x["kind"] for x in fee_lines} == {"packaging_drink_pack", "packaging_order_bag"}
    assert total == 200
