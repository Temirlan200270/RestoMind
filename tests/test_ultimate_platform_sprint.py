"""Ultimate Platform 2026 — Sprint A/B smoke tests."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AIContextSnapshot, BusinessRecommendation, Organization
from app.integrations.reviews_external import import_review_from_url
from app.services.owner_dashboard import build_stock_alerts_stub


class TestStockAlertsStub:
    def test_returns_proxy_when_orders_present(self):
        rows = [{"date": "2026-05-19", "orders_confirmed": 10}]
        alerts = build_stock_alerts_stub(rows)
        assert len(alerts) == 1
        assert alerts[0]["source"] == "daily_org_stats.orders_confirmed"


class TestGuestCareImport:
    def test_import_review_from_url(self):
        item = import_review_from_url("https://2gis.kz/almaty/firm/123", note="Отлично")
        assert item["source"] == "2gis"
        assert item["id"]


@pytest.mark.asyncio
async def test_replay_uses_chat_history_slice(asgi_memory_client, monkeypatch) -> None:
    from app.db.models import StaffUser, User
    from app.core.passwords import hash_password
    from app.schemas.ai_schemas import AIBrainResponse

    client, session_factory = asgi_memory_client
    captured: dict[str, object] = {}

    async def fake_call_openai(**kwargs):
        captured["history"] = kwargs.get("history")
        return AIBrainResponse(intent="faq", reply_text="Replay OK")

    monkeypatch.setattr("app.services.ai_brain.call_openai", fake_call_openai)

    async with session_factory() as db:
        org = Organization(name="Replay Org", slug="replay-org")
        db.add(org)
        await db.flush()
        staff = StaffUser(
            organization_id=org.id,
            email="owner@test.kz",
            password_hash=hash_password("secret123"),
            role="admin",
            is_active=True,
        )
        db.add(staff)
        user = User(organization_id=org.id, phone="+77001112233")
        db.add(user)
        await db.flush()
        snap = AIContextSnapshot(
            id="snap-replay-1",
            organization_id=org.id,
            phone=user.phone,
            business_state={"menu_context_text": "Menu: test"},
            customer_state={
                "customer_ctx_snippet": "VIP",
                "chat_history_slice": [
                    {"role": "user", "content": "Привет"},
                    {"role": "assistant", "content": "Здравствуйте!"},
                ],
            },
            event_slice={},
        )
        db.add(snap)
        await db.commit()

    login = await client.post(
        "/api/admin/auth/login",
        json={"username": "owner@test.kz", "password": "secret123"},
    )
    assert login.status_code == 200

    replay = await client.post(
        "/api/admin/intelligence/snapshots/snap-replay-1/replay",
        params={"user_text": "Повтори"},
    )
    assert replay.status_code == 200
    assert captured["history"] == [
        {"role": "user", "content": "Привет"},
        {"role": "assistant", "content": "Здравствуйте!"},
    ]


@pytest.mark.asyncio
async def test_bulk_apply_pricing_endpoint_exists(asgi_memory_client) -> None:
    import pathlib

    src = pathlib.Path("app/api/admin/intelligence.py").read_text(encoding="utf-8")
    assert "/apply-pricing/bulk" in src
