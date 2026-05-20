from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.db.models import ChatLog, Order, OrderStatus, Organization, User
from app.services.shift_control import build_shift_control


class DummyRequest:
    def __init__(self, organization_id: int) -> None:
        self.session = {"admin_ok": True, "organization_id": organization_id}


@pytest.mark.asyncio
async def test_build_shift_control_unifies_queue_and_focus(db_session) -> None:
    org = Organization(name="Shift Org", slug="shift-org")
    db_session.add(org)
    await db_session.flush()
    user = User(organization_id=int(org.id), phone="+77005556001", name="Shift Guest")
    db_session.add(user)
    await db_session.flush()
    db_session.add(
        Order(
            organization_id=int(org.id),
            user_id=int(user.id),
            status=OrderStatus.DRAFT.value,
            total_price=12000,
            items_json={"items": [{"name": "Сет", "quantity": 1, "item_total": 12000}]},
            updated_at=datetime.now(timezone.utc) - timedelta(minutes=55),
        )
    )
    db_session.add(
        ChatLog(
            organization_id=int(org.id),
            user_id=int(user.id),
            role="user",
            content="Жду счёт",
            created_at=datetime.now(timezone.utc) - timedelta(minutes=8),
        )
    )
    await db_session.flush()

    out = await build_shift_control(db_session, int(org.id))

    assert out["metrics"]["at_risk_kzt"] > 0
    assert len(out["queue"]) >= 1
    assert len(out["focus"]) >= 1
    assert out["focus"][0].get("do_now_action") is not None
    assert len(out["quick_actions"]) >= 2
    assert isinstance(out["system"], list)


@pytest.mark.asyncio
async def test_shift_control_api_endpoint(db_session) -> None:
    import importlib

    analytics = importlib.import_module("app.api.admin.analytics")

    org = Organization(name="Shift API Org", slug="shift-api-org")
    db_session.add(org)
    await db_session.flush()

    out = await analytics.shift_control(
        DummyRequest(int(org.id)),
        location_id=None,
        db=db_session,
    )

    assert out["ok"] is True
    assert "metrics" in out
    assert "queue" in out
