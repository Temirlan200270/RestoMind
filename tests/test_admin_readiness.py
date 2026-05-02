import pytest

from app.api.admin import admin_readiness
from app.db.models import ChatLog, Order, Organization, PaymentEvent, User


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
    assert any(e.get("kind") == "current_status" for e in data["events"])
