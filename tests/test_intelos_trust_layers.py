from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

from app.db.models import (
    CanonicalSalesOrder,
    DataQualityReport,
    DishSeasonalityProfile,
    IngredientSupplier,
    InventoryStockSnapshot,
    Organization,
    OrganizationMemoryEvent,
    RecommendationOutcome,
    SalesDailyAgg,
    SalesFactItem,
    SalesFactOrder,
    SalesHourlyDaily,
)
from app.services.copilot.engine import _select_tools, run_owner_copilot
from app.services.data_quality import (
    canonicalize_olap_sales_rows,
    record_source_snapshot,
    validate_olap_sales_rows,
    write_data_quality_report,
)
from app.services.forecasting import build_dish_category_forecast
from app.services.marketing import create_blast
from app.services.organization_memory import record_memory_event
from app.services.recommendation_outcomes import create_recommendation_outcome, measure_due_outcomes
from app.services.recommendation_outcomes import recommendation_outcome_public
from app.services.restaurant_graph import rebuild_restaurant_graph_profiles
from app.services.sales_anomaly_engine import detect_sales_anomalies


pytestmark = pytest.mark.asyncio


def _olap_row(order_id: str, dish_id: str, dish_name: str, day: date, revenue: float = 1000) -> dict:
    return {
        "UniqOrderId.Id": order_id,
        "OpenDate.Typed": day.isoformat(),
        "CloseTime": f"{day.isoformat()}T12:00:00+00:00",
        "DishId": dish_id,
        "DishName": dish_name,
        "DishCategory": "Food",
        "DishAmountInt": 1,
        "DishDiscountSumInt": revenue,
        "GuestNum": 2,
        "WaiterName": "A",
        "OrderType": "Dine-in",
        "OriginName": "Hall",
    }


async def test_data_quality_snapshot_report_and_canonicalization(db_session):
    db_session.add(Organization(id=1, name="Trust Org", slug="trust"))
    rows = [
        _olap_row("o1", "p1", "Plov", date(2026, 6, 1)),
        _olap_row("o1", "p1", "Plov", date(2026, 6, 1)),
        {"UniqOrderId.Id": "bad", "DishName": "", "DishDiscountSumInt": -5},
    ]
    snapshot = await record_source_snapshot(
        db_session,
        1,
        source="iiko_olap",
        entity_type="sales",
        rows=rows,
        date_from=date(2026, 6, 1),
        date_to=date(2026, 6, 1),
    )
    report = validate_olap_sales_rows(rows)
    assert report["status"] in {"partial", "low_confidence"}
    assert report["duplicate_count"] == 1
    quality = await write_data_quality_report(
        db_session,
        1,
        snapshot_id=snapshot.id,
        source="iiko_olap",
        entity_type="sales",
        report=report,
    )
    stats = await canonicalize_olap_sales_rows(
        db_session,
        1,
        snapshot_id=snapshot.id,
        rows=rows,
        confidence_score=float(quality.confidence_score),
    )
    assert stats["canonical_orders"] == 1
    assert await db_session.scalar(select(CanonicalSalesOrder).where(CanonicalSalesOrder.organization_id == 1))
    assert await db_session.scalar(select(DataQualityReport).where(DataQualityReport.organization_id == 1))


async def test_canonicalization_normalizes_iiko_naive_time_as_org_timezone(db_session):
    db_session.add(Organization(id=1, name="TZ Org", slug="tz", timezone="Etc/GMT-5"))
    day = date(2026, 6, 1)
    rows = [_olap_row("tz1", "p1", "Plov", day)]
    rows[0]["CloseTime"] = f"{day.isoformat()}T12:00:00"
    snapshot = await record_source_snapshot(
        db_session,
        1,
        source="iiko_olap",
        entity_type="sales",
        rows=rows,
        date_from=day,
        date_to=day,
    )
    stats = await canonicalize_olap_sales_rows(
        db_session,
        1,
        snapshot_id=int(snapshot.id),
        rows=rows,
        confidence_score=1.0,
        timezone_name="Etc/GMT-5",
    )
    assert stats["canonical_orders"] == 1
    order = await db_session.scalar(select(CanonicalSalesOrder).where(CanonicalSalesOrder.source_order_id == "tz1"))
    assert order is not None
    assert order.closed_at.hour == 7


async def test_sales_anomaly_has_confidence_evidence_and_drilldown(db_session):
    db_session.add(Organization(id=1, name="Trust Org", slug="trust"))
    current_day = date(2026, 6, 2)
    baseline_days = [current_day - timedelta(days=7 * idx) for idx in range(1, 5)]
    db_session.add(
        SalesDailyAgg(
            organization_id=1,
            date=current_day,
            source="iiko_olap",
            total_revenue=500,
            baseline_revenue=1000,
            delta_pct=-50,
            order_count=5,
            guest_count=8,
            avg_check=100,
        ),
    )
    for idx, baseline_day in enumerate(baseline_days):
        db_session.add(
            SalesDailyAgg(
                organization_id=1,
                date=baseline_day,
                source="iiko_olap",
                total_revenue=1000,
                order_count=10,
                guest_count=16,
                avg_check=100,
            ),
        )
        order = SalesFactOrder(
            organization_id=1,
            iiko_order_id=f"baseline-{idx}",
            order_date=baseline_day,
            revenue=1000,
            guest_count=4,
            data_source="iiko_olap",
        )
        db_session.add(order)
        await db_session.flush()
        db_session.add_all(
            [
                SalesFactItem(
                    organization_id=1,
                    order_id=order.id,
                    product_id="p1",
                    product_name="Plov",
                    category="Food",
                    quantity=8,
                    revenue=800,
                ),
                SalesFactItem(
                    organization_id=1,
                    order_id=order.id,
                    product_id="p2",
                    product_name="Tea",
                    category="Drinks",
                    quantity=4,
                    revenue=200,
                ),
                SalesHourlyDaily(
                    organization_id=1,
                    day=baseline_day,
                    hour=12,
                    source="iiko_olap",
                    orders_count=6,
                    revenue_kzt=700,
                ),
            ],
        )
    current_order = SalesFactOrder(
        organization_id=1,
        iiko_order_id="current",
        order_date=current_day,
        revenue=500,
        guest_count=8,
        data_source="iiko_olap",
    )
    db_session.add(current_order)
    await db_session.flush()
    db_session.add_all(
        [
            SalesFactItem(
                organization_id=1,
                order_id=current_order.id,
                product_id="p1",
                product_name="Plov",
                category="Food",
                quantity=4,
                revenue=400,
            ),
            SalesFactItem(
                organization_id=1,
                order_id=current_order.id,
                product_id="p2",
                product_name="Tea",
                category="Drinks",
                quantity=2,
                revenue=100,
            ),
            SalesHourlyDaily(
                organization_id=1,
                day=current_day,
                hour=12,
                source="iiko_olap",
                orders_count=2,
                revenue_kzt=300,
            ),
        ],
    )
    await db_session.flush()
    created = await detect_sales_anomalies(db_session, 1)
    assert len(created) == 1
    assert created[0].confidence_score is not None
    assert created[0].evidence_json["delta_pct"] == -50
    assert created[0].drilldown_json["contribution"]["orders"] == 5
    assert created[0].drilldown_json["contribution"]["baseline_orders"] == 10
    assert created[0].drilldown_json["path"][2]["items"][0]["baseline_revenue"] > 0
    assert created[0].drilldown_json["path"][2]["items"][0]["revenue_delta"] < 0
    assert created[0].evidence_json["weak_hours"][0]["baseline_revenue"] == 700


async def test_copilot_role_tools_and_memory(db_session):
    db_session.add(Organization(id=1, name="Trust Org", slug="trust"))
    await record_memory_event(
        db_session,
        1,
        event_type="price_change",
        event_date=date(2026, 4, 1),
        summary="Raised coffee price in April.",
        source="manual",
    )
    manager_tools = _select_tools("Кто лучший официант?", role="manager")
    owner_tools = _select_tools("Какие блюда дают выручку, но просаживают маржу?", role="owner")
    assert "get_waiter_kpi" in manager_tools
    assert "get_low_margin_high_revenue_dishes" in owner_tools
    result = await run_owner_copilot(db_session, org_id=1, question="Что было с ценой кофе?", role="owner")
    assert "find_related_memory_events" in result["data"]


async def test_forecast_and_roi_use_windows_and_quality(db_session):
    db_session.add(Organization(id=1, name="Trust Org", slug="trust"))
    today = datetime.now(tz=timezone.utc).date()
    for idx in range(14):
        day = today - timedelta(days=idx + 1)
        order = SalesFactOrder(
            organization_id=1,
            iiko_order_id=f"o{idx}",
            order_date=day,
            revenue=1000,
            guest_count=2,
            data_source="iiko_olap",
        )
        db_session.add(order)
        await db_session.flush()
        db_session.add(
            SalesFactItem(
                organization_id=1,
                order_id=order.id,
                product_id="p1",
                product_name="Plov",
                category="Food",
                quantity=2,
                revenue=1000,
                cost=400,
            ),
        )
        db_session.add(
            SalesDailyAgg(
                organization_id=1,
                date=day,
                source="iiko_olap",
                total_revenue=1000,
                order_count=1,
                guest_count=2,
                avg_check=1000,
            ),
        )
    await db_session.flush()
    future_weekday = (today + timedelta(days=1)).weekday()
    db_session.add(
        DishSeasonalityProfile(
            organization_id=1,
            entity_type="dish",
            entity_id="p1",
            entity_name="Plov",
            period_key=f"weekday:{future_weekday}",
            expected_quantity=9,
            expected_revenue=9000,
            confidence_score=0.88,
        ),
    )
    await db_session.flush()
    forecast = await build_dish_category_forecast(db_session, 1, days_ahead=7)
    assert forecast["dishes"]
    assert forecast["seasonality_profiles_used"] >= 1
    assert forecast["dirty_data_weighting"]["partial_data"] is True
    assert forecast["confidence_score"] > 0

    outcome = await create_recommendation_outcome(
        db_session,
        1,
        recommendation_type="menu_action",
        metric="revenue",
        baseline_window={
            "date_from": (today - timedelta(days=14)).isoformat(),
            "date_to": (today - timedelta(days=8)).isoformat(),
        },
        measurement_window={
            "date_from": (today - timedelta(days=7)).isoformat(),
            "date_to": (today - timedelta(days=1)).isoformat(),
        },
    )
    outcome.status = "applied"
    outcome.measure_after = datetime.now(tz=timezone.utc) - timedelta(seconds=1)
    measured = await measure_due_outcomes(db_session, 1)
    assert measured == 1
    refreshed = await db_session.get(RecommendationOutcome, outcome.id)
    assert refreshed.status == "measured"
    assert refreshed.measurement_window_json["measured_value"] == 7000
    public = recommendation_outcome_public(refreshed)
    assert public["chain"]["result"]["money"] == 0.0
    assert public["causality"] in {"measured_delta", "measured_delta_low_data_confidence", "measured_delta_unlinked_action"}
    memory = (
        await db_session.execute(select(OrganizationMemoryEvent).where(OrganizationMemoryEvent.organization_id == 1))
    ).scalars().all()
    assert any(row.event_type == "recommendation_measured" for row in memory)


async def test_supplier_and_campaign_memory_are_autogenerated(db_session):
    db_session.add(Organization(id=1, name="Memory Org", slug="memory"))
    db_session.add(
        InventoryStockSnapshot(
            organization_id=1,
            source="inventory_csv",
            sku="milk",
            ingredient="Milk",
            unit="l",
            quantity=12,
            payload_json={"supplier_name": "DairyCo", "lead_time_days": 2},
        ),
    )
    await db_session.flush()

    stats = await rebuild_restaurant_graph_profiles(db_session, 1)
    assert stats["ingredient_suppliers"] == 1
    assert await db_session.scalar(select(IngredientSupplier).where(IngredientSupplier.organization_id == 1))

    await create_blast(
        db_session,
        1,
        name="Breakfast promo",
        segment_type="all_active",
        message_text="Breakfast offer",
    )
    events = (
        await db_session.execute(select(OrganizationMemoryEvent).where(OrganizationMemoryEvent.organization_id == 1))
    ).scalars().all()
    event_types = {row.event_type for row in events}
    assert "supplier_change" in event_types
    assert "campaign" in event_types


async def test_ai_center_ui_wires_role_inbox_and_roi_surfaces():
    js = Path("app/static/js/admin-app.js").read_text(encoding="utf-8")
    template = Path("app/templates/screens/_tab_intelligence.html").read_text(encoding="utf-8")
    assert "/api/admin/intelligence/business-questions" in js
    assert "/api/admin/intelligence/insight-deliveries?limit=10" in js
    assert "/api/admin/intelligence/roi-outcomes?limit=8" in js
    assert "intelligenceBusinessQuestions" in template
    assert "Требует внимания" in template
    assert "ROI: совет" in template
