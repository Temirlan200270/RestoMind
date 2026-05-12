from __future__ import annotations

from fastapi import Response

import pytest

from app.api.admin import analytics
from app.api.admin import _monolith as admin_api
import importlib
analytics_module = importlib.import_module("app.api.admin.analytics")
from app.db.models import Order, Organization, User


class DummyRequest:
    def __init__(self, organization_id: int) -> None:
        self.session = {"admin_ok": True, "organization_id": organization_id}


@pytest.mark.asyncio
async def test_admin_analytics_runs_with_orders(db_session):
    org = Organization(name="Analytics Org", slug="analytics")
    db_session.add(org)
    await db_session.flush()
    user = User(organization_id=int(org.id), phone="+77001112233")
    db_session.add(user)
    await db_session.flush()
    db_session.add(
        Order(
            organization_id=int(org.id),
            user_id=int(user.id),
            status="confirmed",
            total_price=4200,
            items_json={
                "items": [
                    {
                        "name": "Plov",
                        "quantity": 2,
                        "item_total": 4200,
                        "iiko_item_id": "plov-1",
                    },
                ],
            },
        ),
    )
    db_session.add(
        Order(
            organization_id=None,
            user_id=int(user.id),
            status="confirmed",
            total_price=1000,
            items_json={"items": [{"name": "Tea", "quantity": 1, "item_total": 1000}]},
        ),
    )
    await db_session.flush()

    out = await analytics(
        DummyRequest(int(org.id)),
        Response(),
        period="week",
        db=db_session,
    )

    assert out["current"]["orders"] == 2
    assert out["current"]["revenue"] == 5200.0
    assert isinstance(out["menu_engineering"], list)
    assert isinstance(out["delivery_geo"], list)


@pytest.mark.asyncio
async def test_roi_today_returns_safe_payload_when_aggregation_fails(db_session, monkeypatch):
    org = Organization(name="ROI Org", slug="roi-org")
    db_session.add(org)
    await db_session.flush()

    async def _boom(*_args, **_kwargs):
        raise RuntimeError("broken aggregate")

    monkeypatch.setattr(analytics_module, "aggregate_org_window", _boom)

    out = await analytics_module.roi_today_summary(DummyRequest(int(org.id)), db_session)

    assert out["metrics"]["orders_count"] == 0
    assert out["metrics"]["revenue"] == 0
    assert out["achievements"] == []
    assert isinstance(out["narrative"], str)
