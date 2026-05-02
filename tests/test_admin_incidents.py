from datetime import datetime, timedelta, timezone

import pytest

from app.api.admin import admin_incidents
from app.db.models import (
    ChatLog,
    FailedTask,
    IntegrationHealth,
    Order,
    Organization,
    PaymentEvent,
    StaffRole,
    StaffUser,
    User,
)


class DummyRequest:
    def __init__(self, session: dict):
        self.session = session
        self.method = "GET"


@pytest.mark.asyncio
async def test_admin_incidents_aggregates_visible_groups(db_session):
    now = datetime.now(timezone.utc)
    org = Organization(id=101, name="Incident Org", slug="incident-org")
    user = User(organization_id=101, phone="+77001112233", name="Guest")
    staff = StaffUser(
        organization_id=101,
        email="owner@example.com",
        password_hash="hash",
        role=StaffRole.ADMIN.value,
        is_active=True,
        is_superadmin=True,
    )
    db_session.add_all([org, user, staff])
    await db_session.flush()

    iiko_order = Order(
        organization_id=101,
        user_id=user.id,
        status="confirmed",
        items_json={"items": [{"name": "Plov", "qty": 1}]},
        total_price=3200,
        iiko_last_error="terminal group mismatch",
        created_at=now - timedelta(minutes=30),
        updated_at=now - timedelta(minutes=10),
    )
    prepay_order = Order(
        organization_id=101,
        user_id=user.id,
        status="confirmed",
        items_json={"items": [{"name": "Lagman", "qty": 1}]},
        total_price=2500,
        prepayment_status="pending",
        created_at=now - timedelta(minutes=25),
        updated_at=now - timedelta(minutes=5),
    )
    db_session.add_all([iiko_order, prepay_order])
    await db_session.flush()

    db_session.add_all(
        [
            FailedTask(
                organization_id=101,
                phone=user.phone,
                message_text="need operator",
                error="retry exhausted",
                attempts=3,
                resolved=False,
                created_at=now - timedelta(minutes=4),
            ),
            ChatLog(
                organization_id=101,
                user_id=user.id,
                role="assistant",
                content="Ваш заказ принят",
                delivery_status="failed",
                error_details={"code": 131000},
                created_at=now - timedelta(minutes=3),
            ),
            PaymentEvent(
                order_id=prepay_order.id,
                event_type="webhook_failed",
                actor="webhook",
                amount=2500,
                note="kaspi:pay-1:org_mismatch:expected=101:got=999",
                created_at=now - timedelta(minutes=2),
            ),
            IntegrationHealth(
                id=1,
                last_menu_sync_at=now - timedelta(minutes=20),
                last_menu_sync_ok=False,
                last_menu_sync_error="iiko timeout",
                last_stoplist_at=now - timedelta(minutes=15),
                last_stoplist_ok=True,
                last_stoplist_error="",
            ),
        ],
    )
    await db_session.flush()

    req = DummyRequest({"admin_ok": True, "organization_id": 101, "staff_id": staff.id})
    data = await admin_incidents(req, db_session)

    group_ids = {g["id"] for g in data["groups"]}
    assert data["is_superadmin"] is True
    assert "iiko_failed" in group_ids
    assert "prepayment_pending" in group_ids
    assert "failed_tasks" in group_ids
    assert "whatsapp_failed" in group_ids
    assert "payment_webhook" in group_ids
    assert "integrations_degraded" in group_ids
    assert data["total_open"] >= 6
    assert isinstance(data.get("hero_actions"), list)


@pytest.mark.asyncio
async def test_admin_incidents_summary_mode_skips_sample_rows(db_session):
    """Режим summary: без выборки строк, есть счётчики и hero_actions."""
    now = datetime.now(timezone.utc)
    org = Organization(id=201, name="Summary Org", slug="summary-org")
    user = User(organization_id=201, phone="+77009998877", name="U")
    db_session.add_all([org, user])
    await db_session.flush()
    db_session.add(
        Order(
            organization_id=201,
            user_id=user.id,
            status="confirmed",
            items_json={"items": []},
            total_price=1000,
            iiko_last_error="boom",
            created_at=now - timedelta(minutes=5),
            updated_at=now - timedelta(minutes=1),
        ),
    )
    await db_session.flush()

    req = DummyRequest({"admin_ok": True, "organization_id": 201})
    data = await admin_incidents(req, db_session, "summary")

    assert data.get("mode") == "summary"
    assert "groups" not in data
    assert data["total_open"] >= 1
    assert isinstance(data.get("hero_actions"), list)
    assert any(a.get("id") == "iiko_failed" for a in data["hero_actions"])


@pytest.mark.asyncio
async def test_admin_incidents_hides_platform_risks_for_regular_admin(db_session):
    org = Organization(id=102, name="Regular Org", slug="regular-org")
    staff = StaffUser(
        organization_id=102,
        email="admin@example.com",
        password_hash="hash",
        role=StaffRole.ADMIN.value,
        is_active=True,
        is_superadmin=False,
    )
    db_session.add_all([org, staff])
    await db_session.flush()

    req = DummyRequest({"admin_ok": True, "organization_id": 102, "staff_id": staff.id})
    data = await admin_incidents(req, db_session)

    assert data["is_superadmin"] is False
    assert data["superadmin_only"] == []
    assert "platform_risks" not in {g["id"] for g in data["groups"]}
