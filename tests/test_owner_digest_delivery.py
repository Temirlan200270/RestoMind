"""Owner Intelligence weekly digest delivery — preview, send, dedupe, cron window."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.db.models import Organization, SystemEvent
from app.services.owner_digest_delivery import (
    cron_dedupe_key,
    is_cron_send_window,
    org_local_now,
    preview_weekly_digest,
    send_weekly_digest,
)
from app.services.owner_weekly_digest import maybe_send_weekly_digest_for_org


@pytest.mark.asyncio
async def test_preview_weekly_digest_returns_text_metrics_html(db_session) -> None:
    org = Organization(
        name="Preview Org",
        slug="preview-org",
        timezone="Asia/Almaty",
        currency="KZT",
        is_active=True,
    )
    db_session.add(org)
    await db_session.flush()

    payload = await preview_weekly_digest(db_session, int(org.id), period="prev_week")
    assert payload["ok"] is True
    assert payload["period"] == "prev_week"
    assert "text" in payload
    assert "metrics" in payload
    assert "html" in payload
    if payload.get("text"):
        assert "RestoMind" in payload["text"]
        assert "<b>RestoMind" in payload["html"]


@pytest.mark.asyncio
async def test_send_weekly_digest_admin_success_emits_event(db_session) -> None:
    org = Organization(name="Send Org", slug="send-org", timezone="UTC", is_active=True)
    db_session.add(org)
    await db_session.flush()
    org_id = int(org.id)

    mock_digest = AsyncMock(
        return_value={"text": "RestoMind — неделя\n• Net ROI: 0 ₸", "metrics": {"net_roi": 0}},
    )
    mock_send = AsyncMock()
    mock_redis = AsyncMock(return_value=True)

    with patch(
        "app.services.owner_digest_delivery._build_digest_payload",
        mock_digest,
    ), patch(
        "app.services.owner_digest_delivery.send_ops_notification_html",
        mock_send,
    ), patch(
        "app.services.owner_digest_delivery._redis_set_once",
        mock_redis,
    ), patch(
        "app.services.owner_digest_delivery.settings.telegram_bot_token",
        "test:owner-digest",
    ):
        result = await send_weekly_digest(
            db_session,
            org_id,
            force=False,
            triggered_by="admin",
        )

    assert result["ok"] is True
    assert result["sent"] is True
    mock_send.assert_awaited_once()

    rows = (
        await db_session.execute(
            SystemEvent.__table__.select().where(SystemEvent.organization_id == org_id),
        )
    ).mappings().all()
    assert any(r["event_type"] == "owner_digest.sent" for r in rows)


@pytest.mark.asyncio
async def test_send_weekly_digest_manual_cooldown_without_force(db_session) -> None:
    org = Organization(name="Cooldown Org", slug="cooldown-org", timezone="UTC", is_active=True)
    db_session.add(org)
    await db_session.flush()

    mock_redis = AsyncMock(return_value=False)

    with patch(
        "app.services.owner_digest_delivery._redis_set_once",
        mock_redis,
    ):
        result = await send_weekly_digest(
            db_session,
            int(org.id),
            force=False,
            triggered_by="admin",
        )

    assert result["skipped"] is True
    assert result["skip_reason"] == "manual_cooldown"


@pytest.mark.asyncio
async def test_send_weekly_digest_manual_force_bypasses_cooldown(db_session) -> None:
    org = Organization(name="Force Org", slug="force-org", timezone="UTC", is_active=True)
    db_session.add(org)
    await db_session.flush()

    mock_digest = AsyncMock(return_value={"text": "Digest text", "metrics": {}})
    mock_send = AsyncMock()
    mock_redis = AsyncMock(return_value=True)

    with patch(
        "app.services.owner_digest_delivery._build_digest_payload",
        mock_digest,
    ), patch(
        "app.services.owner_digest_delivery.send_ops_notification_html",
        mock_send,
    ), patch(
        "app.services.owner_digest_delivery._redis_set_once",
        mock_redis,
    ), patch(
        "app.services.owner_digest_delivery.settings.telegram_bot_token",
        "test:owner-digest",
    ):
        result = await send_weekly_digest(
            db_session,
            int(org.id),
            force=True,
            triggered_by="admin",
        )

    assert result["sent"] is True
    mock_send.assert_awaited_once()


@pytest.mark.asyncio
async def test_cron_dedupe_and_monday_window(db_session) -> None:
    org = Organization(name="Cron Org", slug="cron-org", timezone="UTC", is_active=True)
    db_session.add(org)
    await db_session.flush()

    mock_digest = AsyncMock(return_value={"text": "RestoMind — неделя\n• Net ROI: 0 ₸", "metrics": {}})
    mock_send = AsyncMock()
    mock_redis = AsyncMock(side_effect=[True, False])

    monday_10 = datetime(2026, 5, 25, 10, 5, tzinfo=timezone.utc)

    with patch(
        "app.services.owner_digest_delivery._build_digest_payload",
        mock_digest,
    ), patch(
        "app.services.owner_digest_delivery.send_ops_notification_html",
        mock_send,
    ), patch(
        "app.services.owner_digest_delivery._redis_set_once",
        mock_redis,
    ), patch(
        "app.services.owner_digest_delivery.settings.telegram_bot_token",
        "test:owner-digest",
    ), patch(
        "app.services.owner_digest_delivery.org_local_now",
        return_value=monday_10,
    ):
        first = await send_weekly_digest(db_session, int(org.id), triggered_by="cron")
        second = await send_weekly_digest(db_session, int(org.id), triggered_by="cron")

    assert first["sent"] is True
    assert second["skipped"] is True
    assert second["skip_reason"] == "already_sent"
    mock_send.assert_awaited_once()


@pytest.mark.asyncio
async def test_cron_skips_outside_monday_window(db_session) -> None:
    org = Organization(name="Skip Org", slug="skip-org", timezone="UTC", is_active=True)
    db_session.add(org)
    await db_session.flush()

    tuesday = datetime(2026, 5, 26, 10, 0, tzinfo=timezone.utc)

    with patch(
        "app.services.owner_digest_delivery.org_local_now",
        return_value=tuesday,
    ):
        result = await send_weekly_digest(db_session, int(org.id), triggered_by="cron")

    assert result["skipped"] is True
    assert result["skip_reason"] == "outside_window"


def test_monday_window_logic() -> None:
    monday = datetime(2026, 5, 25, 10, 30, tzinfo=timezone.utc)
    monday_late = datetime(2026, 5, 25, 10, 45, tzinfo=timezone.utc)
    tuesday = datetime(2026, 5, 26, 10, 0, tzinfo=timezone.utc)

    assert is_cron_send_window(monday) is True
    assert is_cron_send_window(monday_late) is False
    assert is_cron_send_window(tuesday) is False


def test_cron_dedupe_key_format() -> None:
    local = datetime(2026, 5, 25, 10, 0, tzinfo=timezone.utc)
    assert cron_dedupe_key(42, local) == "owner_weekly_digest:42:2026:W22"


@pytest.mark.asyncio
async def test_maybe_send_weekly_digest_for_org_delegates_to_delivery(db_session) -> None:
    org = Organization(name="Delegate Org", slug="delegate-org", timezone="UTC", is_active=True)
    db_session.add(org)
    await db_session.flush()

    mock_send = AsyncMock(return_value={"sent": True, "ok": True})
    mock_commit = AsyncMock()

    with patch(
        "app.services.owner_weekly_digest.send_weekly_digest",
        mock_send,
    ):
        db_session.commit = mock_commit  # type: ignore[method-assign]
        await maybe_send_weekly_digest_for_org(db_session, org)

    mock_send.assert_awaited_once()
    mock_commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_org_local_now_uses_org_timezone(db_session) -> None:
    org = Organization(name="TZ Org", slug="tz-org", timezone="Asia/Almaty", is_active=True)
    db_session.add(org)
    await db_session.flush()

    local = org_local_now(org)
    assert local.tzinfo is not None
    assert str(local.tzinfo) in ("Asia/Almaty", "UTC+06:00", "+06:00")
