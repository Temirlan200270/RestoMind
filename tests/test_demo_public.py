"""Tests for G10.8.2 public demo entry."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.main import app
from app.services.demo_shift_scene import (
    DEMO_SCENE_BOOKING_RESCUE_30S,
    DEMO_SCENE_MONEY_RESCUE_30S,
    build_demo_shift_state,
    list_demo_shift_scenes,
)
from app.services.demo_public import resolve_public_demo_scene_id


def test_list_demo_shift_scenes_includes_booking() -> None:
    ids = {s["id"] for s in list_demo_shift_scenes()}
    assert DEMO_SCENE_MONEY_RESCUE_30S in ids
    assert DEMO_SCENE_BOOKING_RESCUE_30S in ids


def test_booking_scene_hook_kind() -> None:
    payload = build_demo_shift_state(DEMO_SCENE_BOOKING_RESCUE_30S, "hook", org_id=1)
    assert payload["focus"]["kind"] == "booking_at_risk"
    assert "19:00" in payload["focus"]["title"]


def test_resolve_public_demo_scene_slugs() -> None:
    assert resolve_public_demo_scene_id("money") == DEMO_SCENE_MONEY_RESCUE_30S
    assert resolve_public_demo_scene_id("booking") == DEMO_SCENE_BOOKING_RESCUE_30S


@pytest.mark.asyncio
async def test_get_demo_redirect_when_debug(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "app_debug", True)
    monkeypatch.setattr(settings, "demo_public_enabled", False)

    async def _fake_session(request, db):  # noqa: ARG001
        request.session["admin_ok"] = True
        request.session["is_demo"] = True
        request.session["organization_id"] = 501
        return {"ok": True}

    monkeypatch.setattr("app.api.demo_public.establish_demo_session", _fake_session)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/demo/booking", follow_redirects=False)
    assert res.status_code == 302
    assert "demo=1" in res.headers.get("location", "")
    assert DEMO_SCENE_BOOKING_RESCUE_30S in res.headers.get("location", "")


@pytest.mark.asyncio
async def test_get_demo_404_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "app_debug", False)
    monkeypatch.setattr(settings, "demo_public_enabled", False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/demo")
    assert res.status_code == 404
