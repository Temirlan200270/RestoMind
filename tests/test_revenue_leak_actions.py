from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from app.db.models import ChatLog, Order, OrderStatus, Organization, User
from app.services.revenue_leak import build_leak_action_surfaces, build_revenue_leak


class DummyRequest:
    def __init__(self, organization_id: int) -> None:
        self.session = {"admin_ok": True, "organization_id": organization_id}


@pytest.mark.asyncio
async def test_build_revenue_leak_includes_action_surfaces(db_session) -> None:
    org = Organization(name="G8 Org", slug="g8-org")
    db_session.add(org)
    await db_session.flush()
    user = User(organization_id=int(org.id), phone="+77005554001", name="Guest")
    db_session.add(user)
    await db_session.flush()
    db_session.add(
        Order(
            organization_id=int(org.id),
            user_id=int(user.id),
            status=OrderStatus.DRAFT.value,
            total_price=8000,
            items_json={"items": [{"name": "Плов", "quantity": 1, "item_total": 8000}]},
            updated_at=datetime.now(timezone.utc) - timedelta(minutes=50),
        )
    )
    db_session.add(
        Order(
            organization_id=int(org.id),
            user_id=int(user.id),
            status=OrderStatus.CONFIRMED.value,
            prepayment_status="pending",
            total_price=5500,
            items_json={"items": [{"name": "Сет", "quantity": 1, "item_total": 5500}]},
            updated_at=datetime.now(timezone.utc) - timedelta(minutes=20),
        )
    )
    db_session.add(
        ChatLog(
            organization_id=int(org.id),
            user_id=int(user.id),
            role="user",
            content="Жду ответ",
            created_at=datetime.now(timezone.utc) - timedelta(minutes=7),
        )
    )
    await db_session.flush()

    out = await build_revenue_leak(db_session, int(org.id))

    assert isinstance(out.get("surfaces"), list)
    assert len(out["surfaces"]) >= 2
    ids = {s["id"] for s in out["surfaces"]}
    assert "lost_drafts" in ids
    assert "pending_prepay" in ids
    lost = next(s for s in out["surfaces"] if s["id"] == "lost_drafts")
    assert lost["count"] == 1
    assert lost["risk_kzt"] == 8000.0
    assert any(a["type"] == "api" for a in lost["actions"])
    assert out["action_risk_kzt"] > 0


@pytest.mark.asyncio
async def test_build_leak_action_surfaces_empty_when_clean(db_session) -> None:
    org = Organization(name="G8 Clean Org", slug="g8-clean-org")
    db_session.add(org)
    await db_session.flush()

    surfaces = await build_leak_action_surfaces(db_session, int(org.id), aov=0.0)

    assert surfaces == []


@pytest.mark.asyncio
async def test_recover_drafts_endpoint_reuses_draft_recovery(db_session, monkeypatch) -> None:
    import importlib

    intelligence = importlib.import_module("app.api.admin.intelligence")

    org = Organization(name="G8 Recover Org", slug="g8-recover-org", is_active=True)
    db_session.add(org)
    await db_session.flush()

    run_mock = AsyncMock(return_value=2)
    monkeypatch.setattr("app.services.draft_recovery.run_draft_recovery_for_org", run_mock)

    out = await intelligence.revenue_leak_recover_drafts(
        DummyRequest(int(org.id)),
        location_id=None,
        db=db_session,
    )

    assert out["ok"] is True
    assert out["sent"] == 2
    run_mock.assert_awaited_once_with(db_session, int(org.id))
