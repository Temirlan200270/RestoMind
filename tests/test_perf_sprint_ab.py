"""Sprint A/B performance: snapshot scheduling, model routing, admin ETag."""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.passwords import hash_password
from app.db.models import Organization, StaffUser
from app.schemas.ai_schemas import AIBrainResponse
from app.services.ai_brain import call_openai, resolve_model_tier
from app.services.context_engine import AIReadContext, schedule_save_ai_context_snapshot


def test_resolve_model_tier_faq_short_text() -> None:
    assert resolve_model_tier("Когда вы работаете?") == "fast"


def test_resolve_model_tier_draft_is_strong() -> None:
    assert resolve_model_tier("Привет", has_draft=True) == "strong"


def test_resolve_model_tier_order_keyword_is_strong() -> None:
    assert resolve_model_tier("Хочу оформить доставку") == "strong"


def test_resolve_model_tier_long_text_is_strong() -> None:
    long_text = "расскажите " * 30
    assert resolve_model_tier(long_text) == "strong"


@pytest.mark.asyncio
async def test_schedule_save_ai_context_snapshot_returns_uuid_immediately() -> None:
    ctx = AIReadContext(
        menu_items=[],
        user=None,
        org=None,
        kb_context="",
        draft_row=None,
        customer_ctx="",
        user_preferences=None,
        tenant=None,
    )

    async def slow_save(*_a, **_k):
        await asyncio.sleep(5)

    with patch("app.services.context_engine.save_ai_context_snapshot", side_effect=slow_save):
        t0 = time.perf_counter()
        snap_id = schedule_save_ai_context_snapshot("+7700", 1, ctx)
        elapsed = time.perf_counter() - t0

    assert isinstance(snap_id, str) and len(snap_id) == 36
    assert elapsed < 0.05


@pytest.mark.asyncio
async def test_call_openai_fast_reruns_on_order_intent() -> None:
    fast_parsed = AIBrainResponse.model_validate_json(
        '{"intent":"order","reply_text":"Ок","items":[{"name":"Плов","iiko_item_id":"x","quantity":1,"modifiers_ids":[],"exclude_ingredients":[]}],"booking_details":null}',
    )
    strong_parsed = AIBrainResponse.model_validate_json(
        '{"intent":"order","reply_text":"Записал заказ","items":[{"name":"Плов","iiko_item_id":"x","quantity":1,"modifiers_ids":[],"exclude_ingredients":[]}],"booking_details":null}',
    )
    mock_provider = MagicMock()
    mock_provider.generate_response = AsyncMock(side_effect=[fast_parsed, strong_parsed])

    with patch("app.services.ai_brain.get_ai_client", return_value=mock_provider), patch(
        "app.services.ai_brain.settings.ai_model_routing_enabled",
        True,
    ):
        result = await call_openai([], "Когда вы работаете?")

    assert result.reply_text == "Записал заказ"
    assert mock_provider.generate_response.await_count == 2
    assert mock_provider.generate_response.await_args_list[0].kwargs.get("model_tier") == "fast"
    assert mock_provider.generate_response.await_args_list[1].kwargs.get("model_tier") == "strong"


@pytest.mark.asyncio
async def test_organization_profile_etag_304(asgi_memory_client) -> None:
    client, session_factory = asgi_memory_client
    async with session_factory() as db:
        org = Organization(name="ETag Org", slug="etag-org")
        db.add(org)
        await db.flush()
        db.add(
            StaffUser(
                organization_id=int(org.id),
                email="etag@test.kz",
                password_hash=hash_password("secret123"),
                role="admin",
                is_active=True,
            )
        )
        await db.commit()

    login = await client.post(
        "/api/admin/auth/login",
        json={"username": "etag@test.kz", "password": "secret123"},
    )
    assert login.status_code == 200

    first = await client.get("/api/admin/organization/profile")
    assert first.status_code == 200
    etag = first.headers.get("etag")
    assert etag

    second = await client.get(
        "/api/admin/organization/profile",
        headers={"If-None-Match": etag},
    )
    assert second.status_code == 304


@pytest.mark.asyncio
async def test_integrations_status_etag_304(asgi_memory_client) -> None:
    client, session_factory = asgi_memory_client
    async with session_factory() as db:
        org = Organization(name="Integ ETag Org", slug="integ-etag-org")
        db.add(org)
        await db.flush()
        db.add(
            StaffUser(
                organization_id=int(org.id),
                email="integ-etag@test.kz",
                password_hash=hash_password("secret123"),
                role="admin",
                is_active=True,
            )
        )
        await db.commit()

    login = await client.post(
        "/api/admin/auth/login",
        json={"username": "integ-etag@test.kz", "password": "secret123"},
    )
    assert login.status_code == 200

    first = await client.get("/api/admin/integrations/status")
    assert first.status_code == 200
    etag = first.headers.get("etag")
    assert etag

    second = await client.get(
        "/api/admin/integrations/status",
        headers={"If-None-Match": etag},
    )
    assert second.status_code == 304
