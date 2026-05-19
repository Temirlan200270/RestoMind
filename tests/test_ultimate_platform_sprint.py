"""Ultimate Platform 2026 — Sprint A/B smoke tests."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AIContextSnapshot,
    AuditLog,
    BusinessRecommendation,
    ExternalReview,
    KnowledgeItem,
    Organization,
)
from app.integrations.reviews_external import import_review_from_url
from app.services.owner_dashboard import build_stock_alerts_from_inventory, build_stock_alerts_stub


class TestStockAlertsStub:
    def test_returns_proxy_when_orders_present(self):
        rows = [{"date": "2026-05-19", "orders_confirmed": 10}]
        alerts = build_stock_alerts_stub(rows)
        assert len(alerts) == 1
        assert alerts[0]["source"] == "daily_org_stats.orders_confirmed"

    def test_real_inventory_alerts_win_over_proxy_shape(self):
        class Row:
            ingredient = "Рис"
            sku = "rice"
            quantity = 2
            unit = "кг"
            min_quantity = 5
            reorder_quantity = 10
            daily_usage_estimate = 2
            source = "manual"

        alerts = build_stock_alerts_from_inventory([Row()])
        assert len(alerts) == 1
        assert alerts[0]["ingredient"] == "Рис"
        assert alerts[0]["source"] == "inventory_stock_snapshots.manual"
        assert alerts[0]["severity"] == "critical"


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


@pytest.mark.asyncio
async def test_guestcare_external_uses_dedicated_table(asgi_memory_client) -> None:
    from app.core.passwords import hash_password
    from app.db.models import StaffUser

    client, session_factory = asgi_memory_client
    async with session_factory() as db:
        org = Organization(name="GuestCare Org", slug="guestcare-org")
        db.add(org)
        await db.flush()
        db.add(StaffUser(
            organization_id=org.id,
            email="guestcare@test.kz",
            password_hash=hash_password("secret123"),
            role="admin",
            is_active=True,
        ))
        await db.commit()

    login = await client.post(
        "/api/admin/auth/login",
        json={"username": "guestcare@test.kz", "password": "secret123"},
    )
    assert login.status_code == 200

    imported = await client.post(
        "/api/admin/intelligence/reviews/external/import",
        json={"url": "https://2gis.kz/almaty/firm/123", "note": "Отлично"},
    )
    assert imported.status_code == 200
    review_id = imported.json()["item"]["id"]

    draft = await client.post(f"/api/admin/intelligence/reviews/external/{review_id}/reply-draft")
    assert draft.status_code == 200
    assert draft.json()["reply_draft"]

    async with session_factory() as db:
        rows = (await db.execute(select(ExternalReview))).scalars().all()
        assert len(rows) == 1
        assert rows[0].reply_draft


@pytest.mark.asyncio
async def test_inventory_snapshots_feed_os_stock_alerts(asgi_memory_client) -> None:
    from app.core.passwords import hash_password
    from app.db.models import StaffUser

    client, session_factory = asgi_memory_client
    async with session_factory() as db:
        org = Organization(name="Inventory Org", slug="inventory-org")
        db.add(org)
        await db.flush()
        db.add(StaffUser(
            organization_id=org.id,
            email="inventory@test.kz",
            password_hash=hash_password("secret123"),
            role="admin",
            is_active=True,
        ))
        await db.commit()

    login = await client.post(
        "/api/admin/auth/login",
        json={"username": "inventory@test.kz", "password": "secret123"},
    )
    assert login.status_code == 200

    upsert = await client.post(
        "/api/admin/intelligence/inventory/snapshots/bulk",
        json={
            "items": [{
                "sku": "rice",
                "ingredient": "Рис",
                "quantity": 1,
                "unit": "кг",
                "min_quantity": 3,
                "daily_usage_estimate": 1,
                "source": "manual",
            }]
        },
    )
    assert upsert.status_code == 200
    assert upsert.json()["updated"] == 1

    dashboard = await client.get("/api/admin/intelligence/os-dashboard")
    assert dashboard.status_code == 200
    alerts = dashboard.json()["stock_alerts"]
    assert alerts[0]["ingredient"] == "Рис"
    assert alerts[0]["source"] == "inventory_stock_snapshots.manual"


@pytest.mark.asyncio
async def test_supplymind_creates_purchase_draft_from_stock_alerts(asgi_memory_client) -> None:
    from app.core.passwords import hash_password
    from app.db.models import StaffUser, SupplyPurchaseDraft

    client, session_factory = asgi_memory_client
    async with session_factory() as db:
        org = Organization(name="Supply Org", slug="supply-org")
        db.add(org)
        await db.flush()
        db.add(StaffUser(
            organization_id=org.id,
            email="supply@test.kz",
            password_hash=hash_password("secret123"),
            role="admin",
            is_active=True,
        ))
        await db.commit()

    await client.post("/api/admin/auth/login", json={"username": "supply@test.kz", "password": "secret123"})
    await client.post(
        "/api/admin/intelligence/inventory/snapshots/bulk",
        json={"items": [{
            "sku": "flour",
            "ingredient": "Мука",
            "quantity": 5,
            "unit": "кг",
            "min_quantity": 10,
            "daily_usage_estimate": 5,
        }]},
    )
    res = await client.post("/api/admin/intelligence/supplymind/drafts", json={"cover_days": 7})
    assert res.status_code == 200
    item = res.json()["item"]
    assert item["items"][0]["ingredient"] == "Мука"
    assert item["items"][0]["recommended_quantity"] == 30

    async with session_factory() as db:
        assert await db.scalar(select(SupplyPurchaseDraft.id)) is not None


@pytest.mark.asyncio
async def test_staffmind_answers_from_knowledge_base(asgi_memory_client) -> None:
    from app.core.passwords import hash_password
    from app.db.models import StaffUser

    client, session_factory = asgi_memory_client
    async with session_factory() as db:
        org = Organization(name="StaffMind Org", slug="staffmind-org")
        db.add(org)
        await db.flush()
        db.add(StaffUser(
            organization_id=org.id,
            email="staffmind@test.kz",
            password_hash=hash_password("secret123"),
            role="admin",
            is_active=True,
        ))
        db.add(KnowledgeItem(
            organization_id=org.id,
            knowledge_kind="staff",
            category="Касса",
            question="Как открыть смену?",
            answer="Откройте кассовую смену и проверьте терминал.",
            is_active=True,
        ))
        await db.commit()

    await client.post("/api/admin/auth/login", json={"username": "staffmind@test.kz", "password": "secret123"})
    start = await client.post(
        "/api/admin/intelligence/staffmind/onboarding",
        json={"phone": "+77005550000", "role": "cashier"},
    )
    assert start.status_code == 200
    sid = start.json()["item"]["id"]
    msg = await client.post(
        f"/api/admin/intelligence/staffmind/onboarding/{sid}/message",
        json={"question": "как открыть смену на кассе"},
    )
    assert msg.status_code == 200
    assert "кассовую смену" in msg.json()["answer"]


@pytest.mark.asyncio
async def test_daily_os_digest_preview_and_voice_config(asgi_memory_client) -> None:
    from app.core.passwords import hash_password
    from app.db.models import StaffUser

    client, session_factory = asgi_memory_client
    async with session_factory() as db:
        org = Organization(name="Digest Org", slug="digest-org")
        db.add(org)
        await db.flush()
        db.add(StaffUser(
            organization_id=org.id,
            email="digest@test.kz",
            password_hash=hash_password("secret123"),
            role="admin",
            is_active=True,
        ))
        db.add(AuditLog(
            organization_id=org.id,
            actor="system",
            action="integration.iiko.failed",
            entity_type="order",
            entity_id="1",
        ))
        await db.commit()

    await client.post("/api/admin/auth/login", json={"username": "digest@test.kz", "password": "secret123"})
    voice = await client.post("/api/admin/intelligence/voice/config", json={"enabled": True, "mode": "stt_fallback"})
    assert voice.status_code == 200
    assert voice.json()["item"]["enabled"] is True

    preview = await client.get("/api/admin/intelligence/daily-os-digest/preview")
    assert preview.status_code == 200
    assert "Daily OS Digest" in preview.json()["item"]["text"]


def test_ws_audit_payload_is_org_scoped():
    from app.api.admin.ws import _ws_event_allowed_for_org
    from app.services.admin_tokens import AdminWsClaims

    claims = AdminWsClaims(email="u", organization_id=7, staff_id=None)
    payload = '{"type":"os.audit","data":{"organization_id":7,"org_id":7}}'
    assert _ws_event_allowed_for_org(payload, claims) is True
