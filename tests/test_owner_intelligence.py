"""Owner Intelligence Summary — service + API tests."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from fastapi import HTTPException

from app.core.passwords import hash_password
from app.db.models import (
    DailyOrgStats,
    Location,
    Order,
    OrderStatus,
    Organization,
    StaffRole,
    StaffUser,
    User,
)
from app.services.owner_intelligence import build_owner_intelligence_summary


class DummyRequest:
    def __init__(self, organization_id: int, staff_id: int | None = None) -> None:
        self.session = {"admin_ok": True, "organization_id": organization_id}
        if staff_id is not None:
            self.session["staff_id"] = staff_id


_SUMMARY_KEYS = (
    "period",
    "accepted_revenue",
    "recovered_revenue",
    "upsell_revenue",
    "lost_revenue",
    "prevented_risk_value",
    "ai_cost",
    "net_roi",
    "top_losses",
    "top_actions",
    "stoplist_impact",
    "upsell_impact",
    "recovery_impact",
    "qa_risk_summary",
    "menu_profit_preview",
    "location_benchmark_preview",
)


@pytest.mark.asyncio
async def test_empty_data_returns_zeros_and_stubs(db_session) -> None:
    org = Organization(name="OI Empty", slug="oi-empty")
    db_session.add(org)
    await db_session.flush()

    summary = await build_owner_intelligence_summary(db_session, int(org.id), period="today")

    for key in _SUMMARY_KEYS:
        assert key in summary, f"missing key: {key}"
    assert summary["period"] == "today"
    assert summary["accepted_revenue"] == 0.0
    assert summary["recovered_revenue"] == 0.0
    assert summary["upsell_revenue"] == 0.0
    assert summary["lost_revenue"] == 0.0
    assert summary["prevented_risk_value"] == 0.0
    assert summary["ai_cost"] == 0.0
    assert summary["net_roi"] == 0.0
    assert summary["top_losses"] == []
    assert summary["top_actions"] == []
    assert summary["menu_profit_preview"]["promote_today"] == []
    assert summary["menu_profit_preview"]["price_increase_candidates"] == []
    assert summary["menu_profit_preview"]["price_recommendations"] == []
    assert summary["menu_profit_preview"]["promote_today_copilot"] == []
    assert summary["menu_profit_preview"]["missing_cost_checklist"]["total_items"] == 0
    assert summary["location_benchmark_preview"] == {"enabled": False}
    assert summary["qa_risk_summary"]["open_count"] == 0
    assert summary["qa_risk_summary"].get("closed_count", 0) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("period", ["today", "7d", "30d"])
async def test_summary_respects_period(db_session, period: str) -> None:
    org = Organization(name="OI Period", slug=f"oi-period-{period}", timezone="Asia/Almaty")
    db_session.add(org)
    await db_session.flush()
    org_id = int(org.id)

    summary = await build_owner_intelligence_summary(db_session, org_id, period=period)
    assert summary["period"] == period
    assert summary["from"]
    assert summary["to"]


@pytest.mark.asyncio
async def test_summary_aggregates_metrics_for_org(db_session) -> None:
    org = Organization(name="OI Metrics", slug="oi-metrics")
    db_session.add(org)
    await db_session.flush()
    org_id = int(org.id)
    user = User(organization_id=org_id, phone="+77001112233")
    db_session.add(user)
    await db_session.flush()
    now = datetime.now(timezone.utc)
    db_session.add(
        Order(
            organization_id=org_id,
            user_id=int(user.id),
            status=OrderStatus.CONFIRMED.value,
            total_price=5000,
            items_json={
                "items": [{"name": "Plov", "quantity": 1, "item_total": 5000}],
                "order_meta": {
                    "recommendation_trace": [
                        {
                            "offered": "Salad",
                            "accepted": True,
                            "accepted_revenue_kzt": 800,
                        },
                    ],
                },
            },
            created_at=now,
        ),
    )
    db_session.add(
        DailyOrgStats(
            organization_id=org_id,
            day=date.today(),
            recovered_kzt=1200,
            draft_recovery_sent=2,
            focus_completed_count=1,
        ),
    )
    await db_session.flush()

    summary = await build_owner_intelligence_summary(db_session, org_id, period="today")

    assert summary["accepted_revenue"] == 5000.0
    assert summary["upsell_revenue"] == 800.0
    assert summary["recovered_revenue"] == 1200.0
    assert summary["recovery_impact"]["draft_recovery_sent"] == 2
    assert summary["upsell_impact"]["accepted"] >= 1


@pytest.mark.asyncio
async def test_summary_is_tenant_scoped(db_session) -> None:
    org_a = Organization(name="OI Org A", slug="oi-org-a")
    org_b = Organization(name="OI Org B", slug="oi-org-b")
    db_session.add_all([org_a, org_b])
    await db_session.flush()
    user_b = User(organization_id=int(org_b.id), phone="+77009998877")
    db_session.add(user_b)
    await db_session.flush()
    now = datetime.now(timezone.utc)
    db_session.add(
        Order(
            organization_id=int(org_b.id),
            user_id=int(user_b.id),
            status=OrderStatus.CONFIRMED.value,
            total_price=99999,
            items_json={"items": [{"name": "Steak", "quantity": 1, "item_total": 99999}]},
            created_at=now,
        ),
    )
    await db_session.flush()

    summary_a = await build_owner_intelligence_summary(db_session, int(org_a.id), period="today")

    assert summary_a["accepted_revenue"] == 0.0
    assert summary_a["upsell_revenue"] == 0.0


@pytest.mark.asyncio
async def test_summary_filters_by_location(db_session) -> None:
    org = Organization(name="OI Loc", slug="oi-loc")
    db_session.add(org)
    await db_session.flush()
    org_id = int(org.id)
    loc_a = Location(organization_id=org_id, name="A", slug="a", is_active=True)
    loc_b = Location(organization_id=org_id, name="B", slug="b", is_active=True)
    db_session.add_all([loc_a, loc_b])
    await db_session.flush()
    user = User(organization_id=org_id, phone="+77005556677")
    db_session.add(user)
    await db_session.flush()
    now = datetime.now(timezone.utc)
    db_session.add_all(
        [
            Order(
                organization_id=org_id,
                user_id=int(user.id),
                location_id=int(loc_a.id),
                status=OrderStatus.CONFIRMED.value,
                total_price=1000,
                items_json={"items": [{"name": "A", "quantity": 1, "item_total": 1000}]},
                created_at=now,
            ),
            Order(
                organization_id=org_id,
                user_id=int(user.id),
                location_id=int(loc_b.id),
                status=OrderStatus.CONFIRMED.value,
                total_price=9000,
                items_json={"items": [{"name": "B", "quantity": 1, "item_total": 9000}]},
                created_at=now,
            ),
        ],
    )
    await db_session.flush()

    scoped = await build_owner_intelligence_summary(
        db_session,
        org_id,
        location_id=int(loc_a.id),
        period="today",
    )

    assert scoped["accepted_revenue"] == 1000.0
    assert scoped["location_id"] == int(loc_a.id)


@pytest.mark.asyncio
async def test_api_summary_forbids_unassigned_location(db_session) -> None:
    from app.api.admin.owner_intelligence import owner_intelligence_summary

    org = Organization(name="OI RBAC", slug="oi-rbac")
    db_session.add(org)
    await db_session.flush()
    org_id = int(org.id)
    loc_a = Location(organization_id=org_id, name="A", slug="rbac-a", is_active=True)
    loc_b = Location(organization_id=org_id, name="B", slug="rbac-b", is_active=True)
    db_session.add_all([loc_a, loc_b])
    await db_session.flush()
    staff = StaffUser(
        organization_id=org_id,
        email="op-oi@test.kz",
        password_hash=hash_password("x"),
        role=StaffRole.OPERATOR.value,
        is_active=True,
        meta_json={"assigned_location_ids": [int(loc_a.id)]},
    )
    db_session.add(staff)
    await db_session.flush()

    with pytest.raises(HTTPException) as exc:
        await owner_intelligence_summary(
            DummyRequest(org_id, int(staff.id)),
            db=db_session,
            period="today",
            location_id=int(loc_b.id),
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_api_summary_returns_contract(db_session) -> None:
    from app.api.admin.owner_intelligence import owner_intelligence_summary

    org = Organization(name="OI API", slug="oi-api")
    db_session.add(org)
    await db_session.flush()
    org_id = int(org.id)

    payload = await owner_intelligence_summary(
        DummyRequest(org_id),
        db=db_session,
        period="7d",
        location_id=None,
    )

    assert payload["ok"] is True
    assert payload["organization_id"] == org_id
    assert payload["period"] == "7d"
    assert payload["location_scope"]["source"] == "org"
    for key in _SUMMARY_KEYS:
        assert key in payload
