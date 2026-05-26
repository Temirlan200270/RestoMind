"""Tests for Menu Profit Lab price recommendations and onboarding checklist."""

from datetime import datetime, timezone

import pytest

from app.api.admin.owner_intelligence_analytics import owner_intelligence_menu_profit
from app.db.models import MenuItem, Order, OrderStatus, Organization, User
from app.services.menu_profit_lab import (
    build_menu_profit_report,
    build_missing_cost_checklist,
    build_price_recommendations,
    promote_today_for_copilot,
)


class DummyRequest:
    def __init__(self, organization_id: int) -> None:
        self.session = {"admin_ok": True, "organization_id": organization_id}


@pytest.mark.asyncio
async def test_build_price_recommendations_fields() -> None:
    items = [
        {
            "menu_item_id": 1,
            "name": "Стейк",
            "price": 10000.0,
            "cost_price": 3000.0,
            "margin_pct": 70.0,
            "quantity_sold": 5,
            "revenue": 50000.0,
            "is_available": True,
        },
    ]
    recs = build_price_recommendations(items)

    assert len(recs) == 1
    rec = recs[0]
    assert rec["current_price"] == 10000.0
    assert rec["cost_price"] == 3000.0
    assert rec["margin_pct"] == 70.0
    assert rec["recommended_increase_pct"] == 5.0
    assert rec["recommended_price"] == 10500.0
    assert rec["expected_margin_lift"] == round((10500.0 - 3000.0 - (10000.0 - 3000.0)) * 5, 2)


@pytest.mark.asyncio
async def test_build_missing_cost_checklist(db_session) -> None:
    org = Organization(name="Cost Org", slug="cost-org")
    db_session.add(org)
    await db_session.flush()
    db_session.add_all(
        [
            MenuItem(organization_id=int(org.id), name="A", price=1000.0, cost_price=400.0),
            MenuItem(organization_id=int(org.id), name="B", price=2000.0),
            MenuItem(organization_id=int(org.id), name="C", price=500.0),
        ],
    )
    await db_session.flush()

    checklist = await build_missing_cost_checklist(db_session, int(org.id))

    assert checklist["total_items"] == 3
    assert checklist["missing_count"] == 2
    assert checklist["missing_pct"] == pytest.approx(66.7, abs=0.1)
    assert checklist["has_cost_count"] == 1
    assert checklist["onboarding_complete"] is False
    assert len(checklist["top_missing"]) == 2
    assert checklist["top_missing"][0]["name"] == "B"


@pytest.mark.asyncio
async def test_promote_today_for_copilot_export() -> None:
    raw = [
        {
            "menu_item_id": 10,
            "iiko_id": "uuid-x",
            "name": "Плов",
            "category": "Горячее",
            "reason": "promote_high_margin",
            "score": 42.5,
            "margin_pct": 55.0,
            "revenue": 12000.0,
            "quantity_sold": 4,
        },
    ]
    exported = promote_today_for_copilot(raw)

    assert len(exported) == 1
    assert exported[0]["name"] == "Плов"
    assert exported[0]["reason"] == "promote_high_margin"
    assert exported[0]["score"] == 42.5
    assert exported[0]["iiko_id"] == "uuid-x"


@pytest.mark.asyncio
async def test_menu_profit_report_enriched_candidates(db_session) -> None:
    now = datetime.now(tz=timezone.utc)
    org = Organization(name="Rec Org", slug="rec-org")
    user = User(organization_id=1, phone="+77008880001", name="Guest")
    db_session.add(org)
    await db_session.flush()
    user.organization_id = int(org.id)
    db_session.add(user)
    await db_session.flush()
    db_session.add(
        MenuItem(
            organization_id=int(org.id),
            name="Бургер",
            category="Горячее",
            price=4000.0,
            cost_price=1200.0,
            is_available=True,
            iiko_id="uuid-burger",
        ),
    )
    db_session.add(
        Order(
            organization_id=int(org.id),
            user_id=int(user.id),
            status=OrderStatus.CONFIRMED.value,
            total_price=24000.0,
            items_json={
                "items": [
                    {
                        "name": "Бургер",
                        "quantity": 6,
                        "item_total": 24000.0,
                        "iiko_id": "uuid-burger",
                    },
                ],
            },
            created_at=now,
            updated_at=now,
        ),
    )
    await db_session.flush()

    report = await build_menu_profit_report(db_session, int(org.id), period="7d")

    assert "price_recommendations" in report
    assert "missing_cost_checklist" in report
    assert "promote_today_copilot" in report
    assert report["missing_cost_checklist"]["onboarding_complete"] is True

    assert len(report["price_increase_candidates"]) >= 1
    cand = report["price_increase_candidates"][0]
    assert "recommended_price" in cand
    assert "expected_margin_lift" in cand
    assert cand["current_price"] == 4000.0

    assert len(report["promote_today_copilot"]) >= 1
    assert report["promote_today_copilot"][0]["reason"]


@pytest.mark.asyncio
async def test_menu_profit_api_returns_enriched_fields(db_session) -> None:
    now = datetime.now(tz=timezone.utc)
    org = Organization(name="API Org", slug="api-org")
    user = User(organization_id=1, phone="+77008880002", name="Guest")
    db_session.add(org)
    await db_session.flush()
    user.organization_id = int(org.id)
    db_session.add(user)
    await db_session.flush()
    db_session.add(
        MenuItem(
            organization_id=int(org.id),
            name="Салат",
            price=1500.0,
            is_available=True,
            iiko_id="uuid-salad",
        ),
    )
    db_session.add(
        Order(
            organization_id=int(org.id),
            user_id=int(user.id),
            status=OrderStatus.CONFIRMED.value,
            total_price=4500.0,
            items_json={
                "items": [
                    {"name": "Салат", "quantity": 3, "item_total": 4500.0, "iiko_id": "uuid-salad"},
                ],
            },
            created_at=now,
            updated_at=now,
        ),
    )
    await db_session.flush()

    payload = await owner_intelligence_menu_profit(
        DummyRequest(int(org.id)),
        period="7d",
        location_id=None,
        db=db_session,
    )

    assert payload["ok"] is True
    assert "price_recommendations" in payload
    assert "missing_cost_checklist" in payload
    assert "promote_today_copilot" in payload
    assert payload["missing_cost_checklist"]["missing_count"] >= 1
