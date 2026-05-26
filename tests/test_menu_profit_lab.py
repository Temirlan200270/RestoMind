"""Tests for Menu Profit Lab (Owner Intelligence Stage 5)."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app.api.admin.owner_intelligence_analytics import owner_intelligence_menu_profit
from app.db.models import (
    Location,
    MenuItem,
    Order,
    OrderStatus,
    Organization,
    StaffRole,
    StaffUser,
    SystemEvent,
    UpsellOfferEvent,
    User,
)
from app.services.menu_profit_lab import build_menu_profit_report


class DummyRequest:
    def __init__(self, organization_id: int) -> None:
        self.session = {"admin_ok": True, "organization_id": organization_id}


@pytest.mark.asyncio
async def test_menu_profit_lite_mode_without_cost_price(db_session) -> None:
    now = datetime.now(tz=timezone.utc)
    org = Organization(name="Menu Org", slug="menu-org")
    user = User(organization_id=1, phone="+77003330001", name="Guest")
    db_session.add(org)
    await db_session.flush()
    user.organization_id = int(org.id)
    db_session.add(user)
    await db_session.flush()
    db_session.add(
        MenuItem(
            organization_id=int(org.id),
            name="Плов",
            category="Горячее",
            price=3000.0,
            is_available=True,
            iiko_id="uuid-plov",
        ),
    )
    db_session.add(
        Order(
            organization_id=int(org.id),
            user_id=int(user.id),
            status=OrderStatus.CONFIRMED.value,
            total_price=6000.0,
            items_json={
                "items": [
                    {"name": "Плов", "quantity": 2, "item_total": 6000.0, "iiko_id": "uuid-plov"},
                ],
            },
            created_at=now,
            updated_at=now,
        ),
    )
    await db_session.flush()

    report = await build_menu_profit_report(db_session, int(org.id), period="7d")

    assert report["lite_mode"] is True
    assert report["cost_data_available"] is False
    assert len(report["top_revenue_items"]) >= 1
    assert report["top_revenue_items"][0]["name"] == "Плов"
    assert report["low_margin_items"] == []
    assert len(report["unknown_cost_items"]) >= 1
    assert len(report["promote_today_candidates"]) >= 1


@pytest.mark.asyncio
async def test_menu_profit_with_cost_price_and_margin(db_session) -> None:
    now = datetime.now(tz=timezone.utc)
    org = Organization(name="Margin Org", slug="margin-org")
    user = User(organization_id=1, phone="+77003330002", name="Guest")
    db_session.add(org)
    await db_session.flush()
    user.organization_id = int(org.id)
    db_session.add(user)
    await db_session.flush()
    db_session.add_all(
        [
            MenuItem(
                organization_id=int(org.id),
                name="Суп",
                category="Первое",
                price=2000.0,
                cost_price=1500.0,
                is_available=True,
                iiko_id="uuid-soup",
            ),
            MenuItem(
                organization_id=int(org.id),
                name="Стейк",
                category="Горячее",
                price=10000.0,
                cost_price=3000.0,
                is_available=True,
                iiko_id="uuid-steak",
            ),
        ],
    )
    db_session.add(
        Order(
            organization_id=int(org.id),
            user_id=int(user.id),
            status=OrderStatus.CONFIRMED.value,
            total_price=12000.0,
            items_json={
                "items": [
                    {"name": "Суп", "quantity": 1, "item_total": 2000.0, "iiko_id": "uuid-soup"},
                    {"name": "Стейк", "quantity": 1, "item_total": 10000.0, "iiko_id": "uuid-steak"},
                ],
            },
            created_at=now,
            updated_at=now,
        ),
    )
    await db_session.flush()

    report = await build_menu_profit_report(db_session, int(org.id), period="7d")

    assert report["cost_data_available"] is True
    assert report["lite_mode"] is False
    assert any(x["name"] == "Суп" for x in report["low_margin_items"])
    assert all(x.get("margin_pct") is not None for x in report["top_revenue_items"] if x["name"] in {"Суп", "Стейк"})


@pytest.mark.asyncio
async def test_menu_profit_stoplist_and_upsell_candidates(db_session) -> None:
    now = datetime.now(tz=timezone.utc)
    org = Organization(name="Stop Org", slug="stop-org")
    user = User(organization_id=1, phone="+77003330003", name="Guest")
    db_session.add(org)
    await db_session.flush()
    user.organization_id = int(org.id)
    db_session.add(user)
    await db_session.flush()
    db_session.add(
        MenuItem(
            organization_id=int(org.id),
            name="Самса",
            category="Выпечка",
            price=800.0,
            is_available=False,
            iiko_id="uuid-samsa",
        ),
    )
    db_session.add_all(
        [
            SystemEvent(
                organization_id=int(org.id),
                event_type="stoplist_update",
                payload_json={"items_added_to_stop": ["Самса"]},
                created_at=now,
            ),
            SystemEvent(
                organization_id=int(org.id),
                event_type="stoplist_update",
                payload_json={"items_added_to_stop": ["Самса"]},
                created_at=now - timedelta(hours=2),
            ),
            UpsellOfferEvent(
                organization_id=int(org.id),
                offered_item_name="Чай",
                offered_item_id="uuid-tea",
                status="shown",
                offered_price=500.0,
                created_at=now,
            ),
            UpsellOfferEvent(
                organization_id=int(org.id),
                offered_item_name="Чай",
                offered_item_id="uuid-tea",
                status="shown",
                offered_price=500.0,
                created_at=now,
            ),
            UpsellOfferEvent(
                organization_id=int(org.id),
                offered_item_name="Чай",
                offered_item_id="uuid-tea",
                status="accepted",
                offered_price=500.0,
                added_revenue=500.0,
                created_at=now,
            ),
        ],
    )
    await db_session.flush()

    report = await build_menu_profit_report(db_session, int(org.id), period="7d")

    assert any(x["name"] == "Самса" for x in report["frequent_stoplist_items"])
    assert any(x["name"] == "Чай" for x in report["upsell_candidates"])


@pytest.mark.asyncio
async def test_menu_profit_tenant_scope_isolation(db_session) -> None:
    now = datetime.now(tz=timezone.utc)
    org_a = Organization(name="Org A", slug="org-a")
    org_b = Organization(name="Org B", slug="org-b")
    user_a = User(organization_id=1, phone="+77004440001", name="A")
    user_b = User(organization_id=1, phone="+77004440002", name="B")
    db_session.add_all([org_a, org_b])
    await db_session.flush()
    user_a.organization_id = int(org_a.id)
    user_b.organization_id = int(org_b.id)
    db_session.add_all([user_a, user_b])
    await db_session.flush()
    db_session.add_all(
        [
            MenuItem(organization_id=int(org_a.id), name="Блюдо A", price=1000.0, iiko_id="a1"),
            MenuItem(organization_id=int(org_b.id), name="Блюдо B", price=2000.0, iiko_id="b1"),
        ],
    )
    db_session.add(
        Order(
            organization_id=int(org_a.id),
            user_id=int(user_a.id),
            status=OrderStatus.CONFIRMED.value,
            total_price=1000.0,
            items_json={"items": [{"name": "Блюдо A", "quantity": 1, "item_total": 1000.0, "iiko_id": "a1"}]},
            created_at=now,
            updated_at=now,
        ),
    )
    await db_session.flush()

    report_a = await build_menu_profit_report(db_session, int(org_a.id), period="7d")
    report_b = await build_menu_profit_report(db_session, int(org_b.id), period="7d")

    assert report_a["top_revenue_items"][0]["name"] == "Блюдо A"
    assert report_b["top_revenue_items"] == []


@pytest.mark.asyncio
async def test_menu_profit_location_scope(db_session) -> None:
    now = datetime.now(tz=timezone.utc)
    org = Organization(name="Loc Org", slug="loc-org")
    user = User(organization_id=1, phone="+77005550001", name="Guest")
    db_session.add(org)
    await db_session.flush()
    user.organization_id = int(org.id)
    loc = Location(organization_id=int(org.id), name="Зал", slug="hall")
    db_session.add_all([user, loc])
    await db_session.flush()
    db_session.add(
        MenuItem(organization_id=int(org.id), name="Лагман", price=2500.0, iiko_id="uuid-lagman"),
    )
    db_session.add_all(
        [
            Order(
                organization_id=int(org.id),
                user_id=int(user.id),
                location_id=int(loc.id),
                status=OrderStatus.CONFIRMED.value,
                total_price=2500.0,
                items_json={"items": [{"name": "Лагман", "quantity": 1, "item_total": 2500.0, "iiko_id": "uuid-lagman"}]},
                created_at=now,
                updated_at=now,
            ),
        ],
    )
    await db_session.flush()

    scoped = await build_menu_profit_report(
        db_session,
        int(org.id),
        location_id=int(loc.id),
        period="7d",
    )
    assert scoped["location_id"] == int(loc.id)
    assert scoped["top_revenue_items"][0]["quantity_sold"] == 1


@pytest.mark.asyncio
async def test_menu_profit_api_location_rbac_403(db_session) -> None:
    org = Organization(name="RBAC Org", slug="rbac-org")
    db_session.add(org)
    await db_session.flush()
    org_id = int(org.id)
    loc_a = Location(organization_id=org_id, name="Зал A", slug="hall-a")
    loc_b = Location(organization_id=org_id, name="Зал B", slug="hall-b")
    db_session.add_all([loc_a, loc_b])
    await db_session.flush()
    staff = StaffUser(
        organization_id=org_id,
        email="mgr@example.com",
        password_hash="x",
        role=StaffRole.OPERATOR.value,
        is_active=True,
        meta_json={"assigned_location_ids": [int(loc_a.id)]},
    )
    db_session.add(staff)
    await db_session.flush()

    req = DummyRequest(org_id)
    req.session["staff_id"] = int(staff.id)

    with pytest.raises(HTTPException) as exc:
        await owner_intelligence_menu_profit(
            req,
            period="7d",
            location_id=int(loc_b.id),
            db=db_session,
        )
    assert exc.value.status_code == 403
