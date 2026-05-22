"""Owner dashboard: прогноз, воронка, top_actions."""

from datetime import datetime, timedelta, timezone

import pytest

from app.api.admin.analytics import _linear_week_forecast, admin_funnel, dashboard_stats
from app.services.owner_dashboard import build_week_forecast
from app.db.models import (
    BusinessRecommendation,
    ChatLog,
    Order,
    OrderStatus,
    Organization,
    User,
)


class DummyRequest:
    def __init__(self, organization_id: int) -> None:
        self.session = {"admin_ok": True, "organization_id": organization_id}


@pytest.mark.asyncio
async def test_build_week_forecast_weekday_method():
    today = datetime.now(tz=timezone.utc).date()
    week_start = today - timedelta(days=today.weekday())
    rev: dict[str, float] = {}
    for i in range(28):
        d = today - timedelta(days=i)
        rev[d.isoformat()] = 1000.0 + (d.weekday() * 100)
    fc = build_week_forecast(rev, today=today)
    assert fc is not None
    assert fc.get("method") in ("weekday", "linear")
    assert fc["forecast_revenue"] >= fc["earned_so_far"]


@pytest.mark.asyncio
async def test_linear_week_forecast_extrapolates():
    today = datetime.now(tz=timezone.utc).date()
    week_start = today - timedelta(days=today.weekday())
    series = []
    for i in range((today - week_start).days + 1):
        d = week_start + timedelta(days=i)
        series.append({"date": d.isoformat(), "revenue": 1000.0, "orders": 5})
    fc = _linear_week_forecast(series, today=today)
    assert fc is not None
    assert fc["earned_so_far"] == pytest.approx(1000.0 * len(series))
    assert fc["forecast_revenue"] >= fc["earned_so_far"]
    assert fc["confidence"] in ("low", "medium", "high")


@pytest.mark.asyncio
async def test_dashboard_stats_week_forecast_and_bot_metrics(db_session):
    now = datetime.now(tz=timezone.utc)
    org = Organization(name="Dash Org", slug="dash-org")
    user = User(organization_id=1, phone="+77001110001", name="Guest")
    db_session.add(org)
    await db_session.flush()
    user.organization_id = int(org.id)
    db_session.add(user)
    await db_session.flush()

    db_session.add(
        Order(
            organization_id=int(org.id),
            user_id=int(user.id),
            status=OrderStatus.CONFIRMED.value,
            items_json={"items": [{"name": "Test", "qty": 1}]},
            total_price=5000,
            created_at=now,
            updated_at=now,
        ),
    )
    db_session.add(
        ChatLog(
            organization_id=int(org.id),
            user_id=int(user.id),
            role="assistant",
            content="Ответ бота",
            created_at=now,
        ),
    )
    db_session.add(
        BusinessRecommendation(
            organization_id=int(org.id),
            recommendation_type="product_boost",
            title="Продвигайте плов",
            body="Высокая конверсия",
            expected_impact_kzt=12000,
            confidence_pct=70,
            status="new",
        ),
    )
    await db_session.flush()

    req = DummyRequest(int(org.id))
    data = await dashboard_stats(req, db_session)

    assert "week_forecast" in data
    assert data["today_orders"] >= 1
    assert data.get("bot_handled_pct") is not None
    assert "escalations_today" in data
    assert "escalation_rate_pct" in data
    assert isinstance(data.get("top_actions"), list)
    assert len(data["top_actions"]) >= 1
    assert data["top_actions"][0]["impact_kzt"] == 12000
    assert data["top_actions"][0].get("target", {}).get("tab")

    peak = data.get("sales_peak_today")
    assert isinstance(peak, dict)
    assert "hours_local" in peak
    assert "label" in peak
    assert "hint" in peak
    assert isinstance(peak["hours_local"], list)


@pytest.mark.asyncio
async def test_admin_funnel_counts(db_session):
    now = datetime.now(tz=timezone.utc)
    org = Organization(name="Funnel Org", slug="funnel-org")
    user = User(organization_id=1, phone="+77002220002", name="F")
    db_session.add(org)
    await db_session.flush()
    user.organization_id = int(org.id)
    db_session.add(user)
    await db_session.flush()

    db_session.add(
        ChatLog(
            organization_id=int(org.id),
            user_id=int(user.id),
            role="user",
            content="Привет",
            created_at=now,
        ),
    )
    db_session.add(
        Order(
            organization_id=int(org.id),
            user_id=int(user.id),
            status=OrderStatus.CONFIRMED.value,
            items_json={"items": []},
            total_price=3000,
            created_at=now,
            updated_at=now,
        ),
    )
    await db_session.flush()

    req = DummyRequest(int(org.id))
    data = await admin_funnel(req, db=db_session, days=7)

    assert data["ok"] is True
    assert data["funnel"]["dialogs"] >= 1
    assert data["funnel"]["completed"] >= 1
    assert data["funnel"]["dialog_to_order_pct"] is not None
    assert "dialog_no_order" in data["funnel"]
    assert "losses" in data
