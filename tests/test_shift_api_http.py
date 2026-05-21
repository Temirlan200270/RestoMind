"""HTTP smoke: GET /shift/state, POST /shift/action (G10 admin API)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.core.passwords import hash_password
from app.db.models import Order, OrderStatus, Organization, StaffUser, User
from app.db.session import InMemoryRedis


def test_shift_ui_exposes_reset_skips_cta() -> None:
    html = Path("app/templates/screens/_tab_shift_control.html").read_text(encoding="utf-8")
    assert "reset_skips" in html
    assert "Показать пропущенные снова" in html
    assert "shift_empty_focus_while_risk_positive" in html


async def _seed_shift_org(session_factory) -> tuple[int, str]:
    async with session_factory() as db:
        org = Organization(name="Shift HTTP Org", slug="shift-http-org")
        db.add(org)
        await db.flush()
        user = User(organization_id=int(org.id), phone="+77009998877", name="Guest")
        db.add(user)
        await db.flush()
        db.add(
            Order(
                organization_id=int(org.id),
                user_id=int(user.id),
                status=OrderStatus.DRAFT.value,
                total_price=15000,
                items_json={"items": [{"name": "Сет", "quantity": 1, "item_total": 15000}]},
                updated_at=datetime.now(timezone.utc) - timedelta(minutes=50),
            )
        )
        db.add(
            StaffUser(
                organization_id=int(org.id),
                email="shift-http@test.kz",
                password_hash=hash_password("secret123"),
                role="operator",
                is_active=True,
            ),
        )
        await db.commit()
        return int(org.id), "shift-http@test.kz"


@pytest.mark.asyncio
async def test_shift_state_http_smoke(asgi_memory_client, monkeypatch) -> None:
    from app.services import shift_state_engine as sse

    client, session_factory = asgi_memory_client
    monkeypatch.setattr(sse, "redis_client", InMemoryRedis())
    _, username = await _seed_shift_org(session_factory)

    login = await client.post(
        "/api/admin/auth/login",
        json={"username": username, "password": "secret123"},
    )
    assert login.status_code == 200

    res = await client.get("/api/admin/shift/state")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["state"] in {"S0", "S1", "S2", "S3", "S4", "S5"}
    assert isinstance(body.get("actions"), list)
    assert "presentation" in body
    focus = body.get("focus")
    assert focus is None or isinstance(focus, dict)


@pytest.mark.asyncio
async def test_shift_action_skip_returns_fresh_state(asgi_memory_client, monkeypatch) -> None:
    from app.services import shift_state_engine as sse

    client, session_factory = asgi_memory_client
    monkeypatch.setattr(sse, "redis_client", InMemoryRedis())
    _, username = await _seed_shift_org(session_factory)

    assert (await client.post(
        "/api/admin/auth/login",
        json={"username": username, "password": "secret123"},
    )).status_code == 200

    state = (await client.get("/api/admin/shift/state")).json()
    assert state.get("focus") is not None
    focus_id = state["focus"]["id"]
    state_before = state["state"]

    action = await client.post(
        "/api/admin/shift/action",
        json={"subtype": "skip", "focus_id": focus_id},
    )
    assert action.status_code == 200
    after = action.json()
    assert after["ok"] is True
    assert after["state"] == state_before
    assert after.get("focus") is None or after["focus"]["id"] != focus_id


@pytest.mark.asyncio
async def test_shift_action_reset_skips_restores_skipped_focus(asgi_memory_client, monkeypatch) -> None:
    from app.services import shift_state_engine as sse

    client, session_factory = asgi_memory_client
    monkeypatch.setattr(sse, "redis_client", InMemoryRedis())
    _, username = await _seed_shift_org(session_factory)

    assert (await client.post(
        "/api/admin/auth/login",
        json={"username": username, "password": "secret123"},
    )).status_code == 200

    state = (await client.get("/api/admin/shift/state")).json()
    focus_id = state["focus"]["id"]
    skipped = await client.post(
        "/api/admin/shift/action",
        json={"subtype": "skip", "focus_id": focus_id},
    )
    assert skipped.status_code == 200
    assert skipped.json()["metrics"]["shift_empty_focus_while_risk_positive"] == 1

    reset = await client.post(
        "/api/admin/shift/action",
        json={"subtype": "reset_skips", "focus_id": None},
    )
    assert reset.status_code == 200
    body = reset.json()
    assert body["focus"] is not None
    assert body["focus"]["id"] == focus_id


@pytest.mark.asyncio
async def test_shift_action_complete_emits_once(asgi_memory_client, monkeypatch) -> None:
    from unittest.mock import AsyncMock

    from app.services import shift_state_engine as sse

    client, session_factory = asgi_memory_client
    monkeypatch.setattr(sse, "redis_client", InMemoryRedis())
    emit = AsyncMock()
    monkeypatch.setattr(sse, "emit_event", emit)
    _, username = await _seed_shift_org(session_factory)

    assert (await client.post(
        "/api/admin/auth/login",
        json={"username": username, "password": "secret123"},
    )).status_code == 200

    focus_id = (await client.get("/api/admin/shift/state")).json()["focus"]["id"]
    first = await client.post(
        "/api/admin/shift/action",
        json={"subtype": "complete", "focus_id": focus_id},
    )
    second = await client.post(
        "/api/admin/shift/action",
        json={"subtype": "complete", "focus_id": focus_id},
    )
    assert first.status_code == 200
    assert second.status_code == 200
    emit.assert_awaited_once()


@pytest.mark.asyncio
async def test_shift_state_requires_auth(asgi_memory_client) -> None:
    client, _ = asgi_memory_client
    res = await client.get("/api/admin/shift/state")
    assert res.status_code in {401, 403, 422}


@pytest.mark.asyncio
async def test_legacy_shift_control_endpoint_removed(asgi_memory_client, monkeypatch) -> None:
    from app.services import shift_state_engine as sse

    client, session_factory = asgi_memory_client
    monkeypatch.setattr(sse, "redis_client", InMemoryRedis())
    _, username = await _seed_shift_org(session_factory)

    assert (await client.post(
        "/api/admin/auth/login",
        json={"username": username, "password": "secret123"},
    )).status_code == 200

    res = await client.get("/api/admin/shift-control")
    assert res.status_code == 404
