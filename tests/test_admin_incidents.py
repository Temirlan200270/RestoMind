from datetime import datetime, timedelta, timezone

import pytest

from app.api.admin import admin_incidents
from app.db.models import (
    ChatLog,
    FailedTask,
    InventoryStockSnapshot,
    Order,
    Organization,
    OrganizationIntegrationSync,
    PaymentEvent,
    StaffRole,
    StaffUser,
    SupplyPurchaseDraft,
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
            OrganizationIntegrationSync(
                organization_id=101,
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
async def test_admin_incidents_surfaces_purchase_checklist_task(db_session):
    org = Organization(id=202, name="Supply Org", slug="supply-org")
    db_session.add(org)
    await db_session.flush()
    db_session.add_all(
        [
            SupplyPurchaseDraft(
                organization_id=202,
                status="draft",
                source="supplymind",
                title="Чеклист закупки на сегодня",
                items_json=[{"ingredient": "Milk", "recommended_quantity": 5}],
            ),
            InventoryStockSnapshot(
                organization_id=202,
                source="manual",
                sku="milk",
                ingredient="Milk",
                unit="l",
                quantity=1,
                min_quantity=3,
                reorder_quantity=8,
                daily_usage_estimate=1,
            ),
        ],
    )
    await db_session.flush()

    req = DummyRequest({"admin_ok": True, "organization_id": 202})
    data = await admin_incidents(req, db_session)
    group = next((g for g in data["groups"] if g["id"] == "purchase_checklist"), None)

    assert group is not None
    assert group["title"] == "Закупка требует подтверждения"
    assert group["action"]["tab"] == "ai_center"
    assert group["action"]["aiCenterTab"] == "final_mile"
    assert group["items"]
    draft_item = next((item for item in group["items"] if item["id"].startswith("supply_draft:")), None)
    assert draft_item is not None
    assert draft_item["kind"] == "supply_purchase_draft"
    assert draft_item["supply_draft_id"]
    assert draft_item["purchase_items"]
    assert draft_item["target"]["supplyDraftId"] == draft_item["supply_draft_id"]

    summary = await admin_incidents(req, db_session, "summary")
    action = next((a for a in summary["hero_actions"] if a["id"] == "purchase_checklist"), None)
    assert action is not None
    assert action["target"]["tab"] == "ai_center"
    assert action["target"]["aiCenterTab"] == "final_mile"


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


@pytest.mark.asyncio
async def test_admin_incidents_whatsapp_ok_with_org_phone_only(db_session, monkeypatch):
    """Phone Number ID в профиле филиала + токен в .env — без ложного «WhatsApp не настроен»."""
    from app.core.config import settings

    org = Organization(
        id=301,
        name="WA Org",
        slug="wa-org",
        whatsapp_phone_number_id="123456789012345",
    )
    db_session.add(org)
    await db_session.flush()

    monkeypatch.setattr(settings, "whatsapp_api_token", "test-token")
    monkeypatch.setattr(settings, "whatsapp_phone_number_id", "")
    monkeypatch.setattr(settings, "openai_api_key", "sk-test")
    monkeypatch.setattr(settings, "ai_provider", "openai")
    monkeypatch.setattr(settings, "iiko_api_login", "")
    monkeypatch.setattr(settings, "iiko_organization_id", "")

    req = DummyRequest({"admin_ok": True, "organization_id": 301})
    data = await admin_incidents(req, db_session)

    integ_group = next((g for g in data["groups"] if g["id"] == "integrations_degraded"), None)
    if integ_group is not None:
        titles = {it["title"] for it in integ_group.get("items") or []}
        assert "WhatsApp не настроен" not in titles
