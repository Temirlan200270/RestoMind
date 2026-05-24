from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.db.models import ChatLog, Location, Order, Organization, User
from app.services.bot_sla_status import chat_live_pulse, pulse_status_for_wait


class DummyRequest:
    def __init__(self, organization_id: int) -> None:
        self.session = {"admin_ok": True, "organization_id": organization_id}


class FakeRedis:
    def __init__(self) -> None:
        self.kv: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.kv.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.kv[key] = value

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self.kv[key] = value

    async def delete(self, key: str) -> None:
        self.kv.pop(key, None)


@pytest.mark.asyncio
async def test_chat_sidebar_exposes_g4_sla_status(db_session, monkeypatch):
    from app.api.admin import chats as chats_api

    org = Organization(name="SLA Org", slug="sla-org")
    db_session.add(org)
    await db_session.flush()
    user = User(organization_id=int(org.id), phone="+77005550101")
    db_session.add(user)
    await db_session.flush()
    db_session.add(
        ChatLog(
            organization_id=int(org.id),
            user_id=int(user.id),
            role="user",
            content="Где мой заказ?",
            created_at=datetime.now(timezone.utc) - timedelta(seconds=400),
        )
    )
    await db_session.flush()

    fake = FakeRedis()
    fake.kv[f"org:{int(org.id)}:slow_chats"] = "4"
    fake.kv[f"org:{int(org.id)}:slow_chat:{user.phone}"] = "1"
    monkeypatch.setattr(chats_api, "redis_client", fake)

    out = await chats_api.list_chats_sidebar(
        DummyRequest(int(org.id)),
        limit=50,
        cursor_at=None,
        cursor_id=None,
        mode="active",
        db=db_session,
    )

    assert out["bot_short_mode"] is True
    assert out["slow_chats"] == 4
    assert out["chats"][0]["pulse"] == "red"
    assert out["chats"][0]["sla_status"] == "red"
    assert out["chats"][0]["last_role"] == "user"
    assert out["chats"][0]["wait_seconds"] >= 400
    assert out["chats"][0]["bot_short_mode"] is True


@pytest.mark.asyncio
async def test_g5_live_pulse_green_when_guest_just_wrote(db_session, monkeypatch):
    from app.api.admin import chats as chats_api

    org = Organization(name="Pulse Green Org", slug="pulse-green-org")
    db_session.add(org)
    await db_session.flush()
    user = User(organization_id=int(org.id), phone="+77005550199")
    db_session.add(user)
    await db_session.flush()
    db_session.add(
        ChatLog(
            organization_id=int(org.id),
            user_id=int(user.id),
            role="user",
            content="Привет",
            created_at=datetime.now(timezone.utc) - timedelta(seconds=30),
        )
    )
    await db_session.flush()

    monkeypatch.setattr(chats_api, "redis_client", FakeRedis())

    out = await chats_api.list_chats_sidebar(
        DummyRequest(int(org.id)),
        limit=50,
        cursor_at=None,
        cursor_id=None,
        mode="active",
        db=db_session,
    )

    assert out["chats"][0]["pulse"] == "green"
    assert out["chats"][0]["wait_seconds"] < 120


def test_pulse_status_thresholds() -> None:
    assert pulse_status_for_wait(30) == "green"
    assert pulse_status_for_wait(150) == "amber"
    assert pulse_status_for_wait(360) == "red"
    assert pulse_status_for_wait(None) is None


def test_chat_live_pulse_not_waiting_after_bot_reply() -> None:
    now = datetime.now(timezone.utc)
    live = chat_live_pulse("assistant", now - timedelta(seconds=600), now=now)
    assert live["pulse"] == "green"
    assert live["wait_seconds"] is None


@pytest.mark.asyncio
async def test_revenue_leak_includes_menu_confusion(db_session):
    from app.services.revenue_leak import build_revenue_leak

    org = Organization(name="Leak Org", slug="leak-org")
    db_session.add(org)
    await db_session.flush()
    user = User(organization_id=int(org.id), phone="+77005550202")
    db_session.add(user)
    await db_session.flush()
    db_session.add(
        Order(
            organization_id=int(org.id),
            user_id=int(user.id),
            status="confirmed",
            total_price=6000,
            items_json={"items": [{"name": "Плов", "quantity": 1, "item_total": 6000}]},
        )
    )
    db_session.add(
        ChatLog(
            organization_id=int(org.id),
            user_id=int(user.id),
            role="assistant",
            content="Не нашёл в меню некоторые позиции. Уточните, пожалуйста.",
            created_at=datetime.now(timezone.utc),
        )
    )
    await db_session.flush()

    out = await build_revenue_leak(db_session, int(org.id))

    assert out["aov"] == 6000
    assert out["breakdown"]["menu_confusion_kzt"] == 3000
    assert out["total_leak_kzt"] >= 3000


@pytest.mark.asyncio
async def test_location_filter_scopes_chats_and_g4_sla(db_session, monkeypatch):
    from app.api.admin import chats as chats_api

    org = Organization(name="Location SLA Org", slug="location-sla-org")
    db_session.add(org)
    await db_session.flush()
    loc_a = Location(organization_id=int(org.id), name="A", slug="a", is_active=True)
    loc_b = Location(organization_id=int(org.id), name="B", slug="b", is_active=True)
    db_session.add_all([loc_a, loc_b])
    await db_session.flush()
    user_a = User(organization_id=int(org.id), phone="+77005550301")
    user_b = User(organization_id=int(org.id), phone="+77005550302")
    db_session.add_all([user_a, user_b])
    await db_session.flush()
    db_session.add_all([
        ChatLog(
            organization_id=int(org.id),
            location_id=int(loc_a.id),
            user_id=int(user_a.id),
            role="user",
            content="A chat",
            created_at=datetime.now(timezone.utc) - timedelta(seconds=400),
        ),
        ChatLog(organization_id=int(org.id), location_id=int(loc_b.id), user_id=int(user_b.id), role="user", content="B chat"),
    ])
    await db_session.flush()

    fake = FakeRedis()
    fake.kv[f"org:{int(org.id)}:loc:{int(loc_a.id)}:slow_chats"] = "4"
    fake.kv[f"org:{int(org.id)}:loc:{int(loc_a.id)}:slow_chat:{user_a.phone}"] = "1"
    monkeypatch.setattr(chats_api, "redis_client", fake)

    out = await chats_api.list_chats_sidebar(
        DummyRequest(int(org.id)),
        limit=50,
        cursor_at=None,
        cursor_id=None,
        mode="active",
        location_id=int(loc_a.id),
        db=db_session,
    )

    assert out["location_id"] == int(loc_a.id)
    assert out["bot_short_mode"] is True
    assert [c["phone"] for c in out["chats"]] == [user_a.phone]
    assert out["chats"][0]["sla_status"] == "red"
    assert out["chats"][0]["pulse"] == "red"
    assert out["chats"][0]["wait_seconds"] >= 400


@pytest.mark.asyncio
async def test_revenue_leak_location_scope(db_session):
    from app.services.revenue_leak import build_revenue_leak

    org = Organization(name="Location Leak Org", slug="location-leak-org")
    db_session.add(org)
    await db_session.flush()
    loc_a = Location(organization_id=int(org.id), name="A", slug="a", is_active=True)
    loc_b = Location(organization_id=int(org.id), name="B", slug="b", is_active=True)
    db_session.add_all([loc_a, loc_b])
    await db_session.flush()
    user_a = User(organization_id=int(org.id), phone="+77005550401")
    user_b = User(organization_id=int(org.id), phone="+77005550402")
    db_session.add_all([user_a, user_b])
    await db_session.flush()
    db_session.add_all([
        Order(
            organization_id=int(org.id),
            location_id=int(loc_a.id),
            user_id=int(user_a.id),
            status="confirmed",
            total_price=6000,
            items_json={"items": [{"name": "A", "quantity": 1, "item_total": 6000}]},
        ),
        Order(
            organization_id=int(org.id),
            location_id=int(loc_b.id),
            user_id=int(user_b.id),
            status="confirmed",
            total_price=12000,
            items_json={"items": [{"name": "B", "quantity": 1, "item_total": 12000}]},
        ),
        ChatLog(
            organization_id=int(org.id),
            location_id=int(loc_a.id),
            user_id=int(user_a.id),
            role="assistant",
            content="Не нашёл в меню эту позицию.",
            created_at=datetime.now(timezone.utc),
        ),
        ChatLog(
            organization_id=int(org.id),
            location_id=int(loc_b.id),
            user_id=int(user_b.id),
            role="assistant",
            content="Не нашёл в меню эту позицию.",
            created_at=datetime.now(timezone.utc),
        ),
    ])
    await db_session.flush()

    out = await build_revenue_leak(
        db_session,
        int(org.id),
        location_id=int(loc_a.id),
        allowed_location_ids={int(loc_a.id), int(loc_b.id)},
    )

    assert out["location_id"] == int(loc_a.id)
    assert out["aov"] == 6000
    assert out["breakdown"]["menu_confusion_kzt"] == 3000


def test_visibility_ui_hooks_are_present():
    from pathlib import Path

    js = Path("app/static/js/admin-app.js").read_text(encoding="utf-8")
    chats = Path("app/templates/screens/_tab_chats.html").read_text(encoding="utf-8")
    dash = Path("app/templates/screens/_tab_dashboard.html").read_text(encoding="utf-8")
    sidebar = Path("app/templates/screens/_sidebar.html").read_text(encoding="utf-8")
    header = Path("app/templates/screens/_header.html").read_text(encoding="utf-8")

    assert "bot_sla_status" in js
    assert "onBotSlaStatus" in js
    assert "chatPulseStatus" in js
    assert "chatWaitSeconds" in js
    assert "lastRole" in js
    assert "chatSlaDotClass" in chats
    assert "Бот в кратком режиме. Помогите ему" in chats
    assert "revenueLeak?.total_leak_kzt" in sidebar
    assert "runRevenueLeakAction" in js
    assert "runShiftStateAction" in js
    assert "loadShiftState" in js
    assert "revenueLeakSurfaceClass" in js
    assert "Деньги под контролем" in dash
    shift = Path("app/templates/screens/_tab_shift_control.html").read_text(encoding="utf-8")
    assert "shiftState" in shift
    assert "Спасено действиями" in shift
    assert "selectedLocationId" in js
    assert "locationQueryParams" in js
    assert "available_locations" in js
    assert "Все точки" in header


@pytest.mark.asyncio
async def test_revenue_leak_http_with_location_id(asgi_memory_client) -> None:
    """GET /revenue-leak?location_id= must not 500 (PG GROUP BY + location scope)."""
    from app.core.passwords import hash_password
    from app.db.models import StaffUser

    client, session_factory = asgi_memory_client

    async with session_factory() as db:
        org = Organization(name="RL HTTP Org", slug="rl-http-org", integration_config_json={})
        db.add(org)
        await db.flush()
        loc = Location(organization_id=int(org.id), name="Main", slug="main", is_active=True)
        db.add(loc)
        await db.flush()
        db.add(
            StaffUser(
                organization_id=int(org.id),
                email="rl-admin@test.kz",
                password_hash=hash_password("secret123"),
                role="admin",
                is_active=True,
            ),
        )
        await db.commit()
        loc_id = int(loc.id)

    login = await client.post(
        "/api/admin/auth/login",
        json={"username": "rl-admin@test.kz", "password": "secret123"},
    )
    assert login.status_code == 200

    res = await client.get(f"/api/admin/intelligence/revenue-leak?location_id={loc_id}")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body.get("ok") is True
    assert body.get("location_id") == loc_id
    assert "total_leak_kzt" in body
