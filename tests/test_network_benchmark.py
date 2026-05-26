"""Tests for Network Benchmark (Owner Intelligence Stage 6)."""

from datetime import datetime, timezone

import pytest

from app.api.admin.owner_intelligence_analytics import (
    owner_intelligence_network_benchmark,
    owner_intelligence_network_weekly_report,
)
from app.db.models import (
    AiOrderAudit,
    DailyOrgStats,
    Order,
    OrderStatus,
    Organization,
    SystemEvent,
    Tenant,
    UpsellOfferEvent,
    User,
)
from app.services.network_benchmark import build_network_benchmark
from app.services.network_weekly_report import build_network_weekly_report


class DummyRequest:
    def __init__(self, organization_id: int) -> None:
        self.session = {"admin_ok": True, "organization_id": organization_id}


@pytest.mark.asyncio
async def test_network_benchmark_disabled_for_single_org(db_session) -> None:
    org = Organization(name="Solo Cafe", slug="solo-cafe")
    db_session.add(org)
    await db_session.flush()

    result = await build_network_benchmark(db_session, int(org.id), period="7d")

    assert result["enabled"] is False
    assert result["reason"] == "single_location"
    assert result["locations"] == []


@pytest.mark.asyncio
async def test_network_benchmark_disabled_when_not_network_flag(db_session) -> None:
    tenant = Tenant(name="Hold", is_network=False)
    db_session.add(tenant)
    await db_session.flush()
    org = Organization(name="Branch", slug="branch", tenant_id=int(tenant.id))
    db_session.add(org)
    await db_session.flush()

    result = await build_network_benchmark(db_session, int(org.id), period="7d")

    assert result["enabled"] is False
    assert result["reason"] == "single_location"


@pytest.mark.asyncio
async def test_network_benchmark_returns_ranking_for_network(db_session) -> None:
    now = datetime.now(tz=timezone.utc)
    today = now.date()
    tenant = Tenant(name="Food Chain", is_network=True)
    db_session.add(tenant)
    await db_session.flush()

    org_best = Organization(name="Best Branch", slug="best", tenant_id=int(tenant.id))
    org_worst = Organization(name="Weak Branch", slug="weak", tenant_id=int(tenant.id))
    db_session.add_all([org_best, org_worst])
    await db_session.flush()

    user_best = User(organization_id=int(org_best.id), phone="+77006660001", name="B")
    user_worst = User(organization_id=int(org_worst.id), phone="+77006660002", name="W")
    db_session.add_all([user_best, user_worst])
    await db_session.flush()

    db_session.add_all(
        [
            Order(
                organization_id=int(org_best.id),
                user_id=int(user_best.id),
                status=OrderStatus.CONFIRMED.value,
                total_price=20000.0,
                items_json={"items": [{"name": "Set", "quantity": 1, "item_total": 20000.0}]},
                created_at=now,
                updated_at=now,
            ),
            Order(
                organization_id=int(org_worst.id),
                user_id=int(user_worst.id),
                status=OrderStatus.CANCELLED.value,
                total_price=8000.0,
                items_json={"items": [{"name": "Set", "quantity": 1, "item_total": 8000.0}]},
                created_at=now,
                updated_at=now,
            ),
            DailyOrgStats(
                organization_id=int(org_best.id),
                day=today,
                recovered_kzt=5000.0,
            ),
            AiOrderAudit(
                organization_id=int(org_worst.id),
                risk_level="high",
                status="open",
                created_at=now,
            ),
        ],
    )
    await db_session.flush()

    result = await build_network_benchmark(db_session, int(org_best.id), period="7d")

    assert result["enabled"] is True
    assert len(result["locations"]) == 2
    assert result["best_location"]["organization_id"] == int(org_best.id)
    assert result["worst_location"]["organization_id"] == int(org_worst.id)
    assert result["locations"][0]["rank"] == 1
    assert float(result["locations"][0]["revenue"]) >= float(result["locations"][1]["revenue"])
    assert int(result["locations"][1]["cancellation_count"]) >= 1
    assert int(result["locations"][1]["qa_risk_count"]) >= 1
    assert result["recommended_actions"]


@pytest.mark.asyncio
async def test_network_benchmark_location_decline_reasons(db_session) -> None:
    now = datetime.now(tz=timezone.utc)
    tenant = Tenant(name="Decline Chain", is_network=True)
    db_session.add(tenant)
    await db_session.flush()

    org_best = Organization(name="Leader", slug="leader", tenant_id=int(tenant.id))
    org_worst = Organization(name="Laggard", slug="laggard", tenant_id=int(tenant.id))
    db_session.add_all([org_best, org_worst])
    await db_session.flush()

    user_best = User(organization_id=int(org_best.id), phone="+77008880001", name="B")
    user_worst = User(organization_id=int(org_worst.id), phone="+77008880002", name="W")
    db_session.add_all([user_best, user_worst])
    await db_session.flush()

    db_session.add_all(
        [
            Order(
                organization_id=int(org_best.id),
                user_id=int(user_best.id),
                status=OrderStatus.CONFIRMED.value,
                total_price=30000.0,
                items_json={"items": [{"name": "Set", "quantity": 1, "item_total": 30000.0}]},
                created_at=now,
                updated_at=now,
            ),
            Order(
                organization_id=int(org_worst.id),
                user_id=int(user_worst.id),
                status=OrderStatus.CANCELLED.value,
                total_price=12000.0,
                items_json={"items": [{"name": "Set", "quantity": 1, "item_total": 12000.0}]},
                created_at=now,
                updated_at=now,
            ),
            AiOrderAudit(
                organization_id=int(org_worst.id),
                risk_level="high",
                status="open",
                created_at=now,
            ),
        ],
    )
    await db_session.flush()

    result = await build_network_benchmark(db_session, int(org_worst.id), period="7d")

    assert result["enabled"] is True
    assert result["network_averages"]["revenue"] > 0
    assert len(result["location_decline_reasons"]) >= 1
    worst = next(row for row in result["locations"] if int(row["organization_id"]) == int(org_worst.id))
    assert isinstance(worst.get("decline_reasons"), list)
    assert len(worst["decline_reasons"]) >= 1
    assert worst.get("vs_network") is not None


@pytest.mark.asyncio
async def test_network_benchmark_tenant_scope_isolation(db_session) -> None:
    now = datetime.now(tz=timezone.utc)
    tenant_a = Tenant(name="Net A", is_network=True)
    tenant_b = Tenant(name="Net B", is_network=True)
    db_session.add_all([tenant_a, tenant_b])
    await db_session.flush()

    org_a1 = Organization(name="A1", slug="a1", tenant_id=int(tenant_a.id))
    org_a2 = Organization(name="A2", slug="a2", tenant_id=int(tenant_a.id))
    org_b1 = Organization(name="B1", slug="b1", tenant_id=int(tenant_b.id))
    org_b2 = Organization(name="B2", slug="b2", tenant_id=int(tenant_b.id))
    db_session.add_all([org_a1, org_a2, org_b1, org_b2])
    await db_session.flush()

    user = User(organization_id=int(org_a1.id), phone="+77007770001", name="U")
    db_session.add(user)
    await db_session.flush()
    db_session.add(
        Order(
            organization_id=int(org_b1.id),
            user_id=int(user.id),
            status=OrderStatus.CONFIRMED.value,
            total_price=50000.0,
            items_json={"items": []},
            created_at=now,
            updated_at=now,
        ),
    )
    await db_session.flush()

    result = await build_network_benchmark(db_session, int(org_a1.id), period="7d")

    assert result["enabled"] is True
    org_ids = {row["organization_id"] for row in result["locations"]}
    assert org_ids == {int(org_a1.id), int(org_a2.id)}
    assert int(org_b1.id) not in org_ids


@pytest.mark.asyncio
async def test_network_benchmark_location_row_enriched_fields(db_session) -> None:
    now = datetime.now(tz=timezone.utc)
    today = now.date()
    tenant = Tenant(name="Enriched Chain", is_network=True)
    db_session.add(tenant)
    await db_session.flush()

    org_best = Organization(name="Alpha", slug="alpha", tenant_id=int(tenant.id))
    org_mid = Organization(name="Beta", slug="beta", tenant_id=int(tenant.id))
    org_worst = Organization(name="Gamma", slug="gamma", tenant_id=int(tenant.id))
    db_session.add_all([org_best, org_mid, org_worst])
    await db_session.flush()

    users = [
        User(organization_id=int(org_best.id), phone="+77009990001", name="A"),
        User(organization_id=int(org_mid.id), phone="+77009990002", name="B"),
        User(organization_id=int(org_worst.id), phone="+77009990003", name="G"),
    ]
    db_session.add_all(users)
    await db_session.flush()

    db_session.add_all(
        [
            Order(
                organization_id=int(org_best.id),
                user_id=int(users[0].id),
                status=OrderStatus.CONFIRMED.value,
                total_price=50000.0,
                items_json={"items": []},
                created_at=now,
                updated_at=now,
            ),
            Order(
                organization_id=int(org_mid.id),
                user_id=int(users[1].id),
                status=OrderStatus.CONFIRMED.value,
                total_price=25000.0,
                items_json={"items": []},
                created_at=now,
                updated_at=now,
            ),
            Order(
                organization_id=int(org_worst.id),
                user_id=int(users[2].id),
                status=OrderStatus.CANCELLED.value,
                total_price=15000.0,
                items_json={"items": []},
                created_at=now,
                updated_at=now,
            ),
            DailyOrgStats(organization_id=int(org_best.id), day=today, recovered_kzt=8000.0),
            DailyOrgStats(organization_id=int(org_worst.id), day=today, recovered_kzt=500.0),
            UpsellOfferEvent(
                organization_id=int(org_best.id),
                base_item_name="Burger",
                offered_item_name="Fries",
                status="accepted",
                added_revenue=6000.0,
                created_at=now,
            ),
            UpsellOfferEvent(
                organization_id=int(org_mid.id),
                base_item_name="Burger",
                offered_item_name="Drink",
                status="accepted",
                added_revenue=2000.0,
                created_at=now,
            ),
            SystemEvent(
                organization_id=int(org_worst.id),
                event_type="stoplist_update",
                created_at=now,
            ),
            SystemEvent(
                organization_id=int(org_worst.id),
                event_type="stoplist_update",
                created_at=now,
            ),
            AiOrderAudit(
                organization_id=int(org_worst.id),
                risk_level="high",
                status="open",
                created_at=now,
            ),
        ],
    )
    await db_session.flush()

    result = await build_network_benchmark(db_session, int(org_worst.id), period="7d")

    assert result["enabled"] is True
    assert len(result["locations"]) == 3
    assert result["network_avg_kzt"] > 0
    assert result.get("top_decline_reason")

    worst = next(row for row in result["locations"] if int(row["organization_id"]) == int(org_worst.id))
    best = next(row for row in result["locations"] if int(row["organization_id"]) == int(org_best.id))

    assert worst["org_revenue_kzt"] == 0.0
    assert worst["network_avg_kzt"] == result["network_avg_kzt"]
    assert worst["recovery"] == 500.0
    assert worst["rank"] == 3
    assert worst["delta_vs_avg"]["revenue"]["delta_kzt"] < 0
    assert worst["delta_vs_avg"]["revenue"]["delta_pct"] is not None
    assert worst["top_decline_reason"]
    assert int(worst["stoplist_incidents"]) >= 2
    assert int(worst["qa_risk_count"]) >= 1

    assert best["org_revenue_kzt"] == 50000.0
    assert best["delta_vs_avg"]["revenue"]["delta_kzt"] > 0
    assert float(best["upsell_revenue"]) >= 6000.0

    assert result["practice_transfers"]
    assert any("upsell" in str(t.get("metric") or "") for t in result["practice_transfers"])


@pytest.mark.asyncio
async def test_network_weekly_report_narratives(db_session) -> None:
    now = datetime.now(tz=timezone.utc)
    tenant = Tenant(name="Weekly Chain", is_network=True)
    db_session.add(tenant)
    await db_session.flush()

    org_leader = Organization(name="Upsell King", slug="upsell-king", tenant_id=int(tenant.id))
    org_laggard = Organization(name="Slow Branch", slug="slow", tenant_id=int(tenant.id))
    db_session.add_all([org_leader, org_laggard])
    await db_session.flush()

    user_l = User(organization_id=int(org_leader.id), phone="+77001110001", name="L")
    user_g = User(organization_id=int(org_laggard.id), phone="+77001110002", name="G")
    db_session.add_all([user_l, user_g])
    await db_session.flush()

    db_session.add_all(
        [
            Order(
                organization_id=int(org_leader.id),
                user_id=int(user_l.id),
                status=OrderStatus.CONFIRMED.value,
                total_price=40000.0,
                items_json={"items": []},
                created_at=now,
                updated_at=now,
            ),
            Order(
                organization_id=int(org_laggard.id),
                user_id=int(user_g.id),
                status=OrderStatus.CANCELLED.value,
                total_price=10000.0,
                items_json={"items": []},
                created_at=now,
                updated_at=now,
            ),
            UpsellOfferEvent(
                organization_id=int(org_leader.id),
                base_item_name="Pizza",
                offered_item_name="Cola",
                status="accepted",
                added_revenue=12000.0,
                created_at=now,
            ),
            AiOrderAudit(
                organization_id=int(org_laggard.id),
                risk_level="critical",
                status="open",
                created_at=now,
            ),
        ],
    )
    await db_session.flush()

    report = await build_network_weekly_report(db_session, int(org_laggard.id), period="7d")

    assert report["enabled"] is True
    assert report["headline"]
    assert report.get("top_decline_reason")
    assert len(report["narratives"]) >= 1
    assert any("лидер по upsell" in line.lower() for line in report["narratives"])
    assert any("просела" in line.lower() for line in report["narratives"])
    assert report["practice_transfers"] or report["recommended_actions"]


@pytest.mark.asyncio
async def test_network_benchmark_weekly_api_payload(db_session) -> None:
    now = datetime.now(tz=timezone.utc)
    tenant = Tenant(name="API Net", is_network=True)
    db_session.add(tenant)
    await db_session.flush()

    org_a = Organization(name="A", slug="api-a", tenant_id=int(tenant.id))
    org_b = Organization(name="B", slug="api-b", tenant_id=int(tenant.id))
    db_session.add_all([org_a, org_b])
    await db_session.flush()

    user_a = User(organization_id=int(org_a.id), phone="+77002220001", name="A")
    db_session.add(user_a)
    await db_session.flush()
    db_session.add(
        Order(
            organization_id=int(org_a.id),
            user_id=int(user_a.id),
            status=OrderStatus.CONFIRMED.value,
            total_price=10000.0,
            items_json={"items": []},
            created_at=now,
            updated_at=now,
        ),
    )
    await db_session.flush()

    req = DummyRequest(int(org_a.id))
    payload = await owner_intelligence_network_weekly_report(req, period="7d", db=db_session)

    assert payload["ok"] is True
    assert payload["enabled"] is True
    assert "headline" in payload
    assert "narratives" in payload
    assert "practice_transfers" in payload
    assert payload.get("top_decline_reason") is not None or payload.get("decline_reasons") is not None
    assert len(payload.get("locations") or []) == 2


@pytest.mark.asyncio
async def test_network_benchmark_api_disabled_payload(db_session) -> None:
    org = Organization(name="API Solo", slug="api-solo")
    db_session.add(org)
    await db_session.flush()

    req = DummyRequest(int(org.id))
    payload = await owner_intelligence_network_benchmark(req, period="7d", db=db_session)

    assert payload["ok"] is True
    assert payload["enabled"] is False
    assert payload["reason"] == "single_location"
