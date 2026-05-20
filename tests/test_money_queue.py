from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.db.models import ChatLog, Order, OrderStatus, Organization, User
from app.services.money_queue import build_money_queue


class DummyRequest:
    def __init__(self, organization_id: int) -> None:
        self.session = {"admin_ok": True, "organization_id": organization_id}


@pytest.mark.asyncio
async def test_build_money_queue_merges_drafts_prepay_and_slow_chats(db_session) -> None:
    org = Organization(name="Money Queue Org", slug="money-queue-org")
    db_session.add(org)
    await db_session.flush()

    user_draft = User(organization_id=int(org.id), phone="+77005551001", name="Draft Guest")
    user_prepay = User(organization_id=int(org.id), phone="+77005551002", name="Prepay Guest")
    user_chat = User(organization_id=int(org.id), phone="+77005551003", name="Chat Guest")
    db_session.add_all([user_draft, user_prepay, user_chat])
    await db_session.flush()

    stale_at = datetime.now(timezone.utc) - timedelta(minutes=50)
    db_session.add(
        Order(
            organization_id=int(org.id),
            user_id=int(user_draft.id),
            status=OrderStatus.DRAFT.value,
            total_price=9000,
            items_json={"items": [{"name": "Плов", "quantity": 1, "item_total": 9000}]},
            updated_at=stale_at,
        )
    )
    db_session.add(
        Order(
            organization_id=int(org.id),
            user_id=int(user_prepay.id),
            status=OrderStatus.CONFIRMED.value,
            prepayment_status="pending",
            total_price=12000,
            items_json={"items": [{"name": "Сет", "quantity": 1, "item_total": 12000}]},
            updated_at=datetime.now(timezone.utc) - timedelta(minutes=70),
        )
    )
    db_session.add(
        ChatLog(
            organization_id=int(org.id),
            user_id=int(user_chat.id),
            role="user",
            content="Можно счёт?",
            created_at=datetime.now(timezone.utc) - timedelta(minutes=6),
        )
    )
    await db_session.flush()

    out = await build_money_queue(db_session, int(org.id))

    assert out["summary"]["total"] == 3
    assert out["summary"]["abandoned_drafts"] == 1
    assert out["summary"]["pending_prepay"] == 1
    assert out["summary"]["slow_chats"] == 1
    assert out["summary"]["money_at_risk_kzt"] == 21000.0
    assert out["summary"]["critical"] >= 2

    kinds = {item["kind"] for item in out["items"]}
    assert kinds == {"abandoned_draft", "pending_prepay", "slow_chat"}
    assert out["items"][0]["severity"] in {"critical", "warning"}


@pytest.mark.asyncio
async def test_build_money_queue_skips_fresh_draft_and_green_chat(db_session) -> None:
    org = Organization(name="Money Queue Org 2", slug="money-queue-org-2")
    db_session.add(org)
    await db_session.flush()

    user_draft = User(organization_id=int(org.id), phone="+77005552001")
    user_chat = User(organization_id=int(org.id), phone="+77005552002")
    db_session.add_all([user_draft, user_chat])
    await db_session.flush()

    db_session.add(
        Order(
            organization_id=int(org.id),
            user_id=int(user_draft.id),
            status=OrderStatus.DRAFT.value,
            total_price=5000,
            items_json={"items": [{"name": "Суп", "quantity": 1, "item_total": 5000}]},
            updated_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        )
    )
    db_session.add(
        ChatLog(
            organization_id=int(org.id),
            user_id=int(user_chat.id),
            role="assistant",
            content="Принято!",
            created_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
    )
    await db_session.flush()

    out = await build_money_queue(db_session, int(org.id))

    assert out["summary"]["total"] == 0
    assert out["items"] == []


@pytest.mark.asyncio
async def test_inbox_money_queue_api_endpoint(db_session) -> None:
    import importlib

    analytics_module = importlib.import_module("app.api.admin.analytics")

    org = Organization(name="Money Queue API Org", slug="money-queue-api-org")
    db_session.add(org)
    await db_session.flush()
    user = User(organization_id=int(org.id), phone="+77005553001", name="API Guest")
    db_session.add(user)
    await db_session.flush()
    db_session.add(
        Order(
            organization_id=int(org.id),
            user_id=int(user.id),
            status=OrderStatus.DRAFT.value,
            total_price=3000,
            items_json={"items": [{"name": "Чай", "quantity": 1, "item_total": 3000}]},
            updated_at=datetime.now(timezone.utc) - timedelta(minutes=40),
        )
    )
    await db_session.flush()

    out = await analytics_module.inbox_money_queue(
        DummyRequest(int(org.id)),
        location_id=None,
        db=db_session,
    )

    assert out["summary"]["abandoned_drafts"] == 1
    assert out["items"][0]["order_id"] is not None
    assert out["items"][0]["actions"]
