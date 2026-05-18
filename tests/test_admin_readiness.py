import pytest

from app.api.admin import admin_readiness
from app.db.models import ChatLog, Order, Organization, PaymentEvent, SystemEvent, User


class DummyRequest:
    def __init__(self, organization_id: int) -> None:
        self.session = {"admin_ok": True, "organization_id": organization_id}


@pytest.mark.asyncio
async def test_admin_readiness_smoke(db_session):
    org = Organization(name="R", slug="r")
    db_session.add(org)
    await db_session.flush()
    req = DummyRequest(int(org.id))
    data = await admin_readiness(req, db_session)
    assert data.get("ok") is not None
    assert isinstance(data.get("checks"), list)
    assert len(data["checks"]) >= 1
    assert "links" in data
    assert "payment_webhook_url" in data["links"] or data["links"].get("payment_webhook_url") is None


@pytest.mark.asyncio
async def test_admin_order_timeline_sorts_and_includes_payment(db_session):
    from app.api.admin import admin_order_timeline

    org = Organization(name="T", slug="t")
    db_session.add(org)
    await db_session.flush()
    user = User(organization_id=int(org.id), phone="+77001112233")
    db_session.add(user)
    await db_session.flush()
    order = Order(
        organization_id=int(org.id),
        user_id=int(user.id),
        status="confirmed",
        total_price=100,
        items_json={"items": []},
        prepayment_status="paid",
    )
    db_session.add(order)
    await db_session.flush()
    db_session.add(
        PaymentEvent(
            order_id=int(order.id),
            event_type="webhook_paid",
            actor="webhook",
            amount=100,
            note="tx-1",
        ),
    )
    db_session.add(
        ChatLog(
            organization_id=int(org.id),
            user_id=int(user.id),
            role="user",
            content="Привет",
        ),
    )
    await db_session.flush()

    req = DummyRequest(int(org.id))
    data = await admin_order_timeline(req, int(order.id), db_session)
    kinds = [e.get("kind") for e in data["events"]]
    assert "order_created" in kinds
    assert "payment" in kinds
    assert "chat" in kinds


@pytest.mark.asyncio
async def test_admin_order_timeline_includes_trace_metadata_and_system_events(db_session):
    from app.api.admin import admin_order_timeline

    org = Organization(name="TT", slug="tt")
    db_session.add(org)
    await db_session.flush()
    user = User(organization_id=int(org.id), phone="+77009998877")
    db_session.add(user)
    await db_session.flush()
    order = Order(
        organization_id=int(org.id),
        user_id=int(user.id),
        status="draft",
        total_price=500,
        items_json={"items": []},
    )
    db_session.add(order)
    await db_session.flush()
    db_session.add(
        ChatLog(
            organization_id=int(org.id),
            user_id=int(user.id),
            role="assistant",
            content="trace msg",
            meta_json={"trace_id": "trace-1", "conversation_id": "conv-1"},
        ),
    )
    db_session.add(
        SystemEvent(
            organization_id=int(org.id),
            event_type="conversation_state_changed",
            source="webhooks.process_message",
            payload_json={
                "phone": user.phone,
                "from_state": "chatting",
                "to_state": "confirming_order",
                "trace_id": "trace-1",
                "conversation_id": "conv-1",
            },
        ),
    )
    await db_session.flush()

    req = DummyRequest(int(org.id))
    data = await admin_order_timeline(req, int(order.id), db_session)
    chat_event = next(e for e in data["events"] if e.get("kind") == "chat")
    assert chat_event["meta"]["trace_id"] == "trace-1"
    assert chat_event["meta"]["conversation_id"] == "conv-1"
    # conversation_state_changed events are excluded from the timeline (UI noise)
