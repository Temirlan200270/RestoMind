"""Owner Intelligence weekly digest — build + Telegram send + dedupe."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.db.models import Organization
from app.services.owner_intelligence_digest import build_owner_intelligence_weekly_digest


@pytest.mark.asyncio
async def test_build_weekly_digest_includes_core_metrics(db_session) -> None:
    org = Organization(
        name="Digest Org",
        slug="digest-org",
        timezone="Asia/Almaty",
        currency="KZT",
        is_active=True,
    )
    db_session.add(org)
    await db_session.flush()

    payload = await build_owner_intelligence_weekly_digest(db_session, org)
    assert payload is not None
    text = payload["text"]
    assert "RestoMind — неделя" in text
    assert "Принято заказов" in text
    assert "Net ROI" in text

    metrics = payload["metrics"]
    assert "accepted_revenue" in metrics
    assert "upsell_revenue" in metrics
    assert "recovered_revenue" in metrics
    assert "lost_revenue" in metrics
    assert "qa_open" in metrics
    assert "kitchen_gate_blocked" in metrics
    assert "top_actions_count" in metrics

    summary = payload["summary"]
    assert summary["period"] == "prev_week"
    assert "qa_risk_summary" in summary
    assert "top_actions" in summary


@pytest.mark.asyncio
async def test_weekly_digest_send_dedupes_and_respects_monday_window(db_session) -> None:
    org = Organization(
        name="Cron Org",
        slug="cron-org",
        timezone="UTC",
        is_active=True,
    )
    db_session.add(org)
    await db_session.flush()

    mock_digest = AsyncMock(
        return_value={"text": "RestoMind — неделя\n• Net ROI: 0 ₸", "metrics": {}},
    )
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
        "app.services.owner_digest_delivery.org_local_now",
        return_value=monday_10,
    ):
        from app.services.owner_digest_delivery import send_weekly_digest

        await send_weekly_digest(db_session, int(org.id), triggered_by="cron")
        await send_weekly_digest(db_session, int(org.id), triggered_by="cron")

    mock_digest.assert_awaited_once()
    mock_send.assert_awaited_once()


@pytest.mark.asyncio
async def test_weekly_digest_skips_outside_monday_window(db_session) -> None:
    org = Organization(name="Skip Org", slug="skip-org", timezone="UTC", is_active=True)
    db_session.add(org)
    await db_session.flush()

    mock_digest = AsyncMock(return_value={"text": "x", "metrics": {}})
    tuesday = datetime(2026, 5, 26, 10, 0, tzinfo=timezone.utc)

    with patch(
        "app.services.owner_digest_delivery.org_local_now",
        return_value=tuesday,
    ):
        from app.services.owner_digest_delivery import send_weekly_digest

        result = await send_weekly_digest(db_session, int(org.id), triggered_by="cron")

    assert result["skipped"] is True
    mock_digest.assert_not_awaited()


@pytest.mark.asyncio
async def test_list_audits_risk_level_filter(db_session) -> None:
    from datetime import datetime, timezone

    from app.db.models import AiOrderAudit
    from app.services.order_ai_audit import list_order_ai_audits

    org = Organization(name="Audit Filter", slug="audit-filter")
    db_session.add(org)
    await db_session.flush()
    org_id = int(org.id)
    now = datetime.now(timezone.utc)

    db_session.add_all([
        AiOrderAudit(
            organization_id=org_id,
            order_id=1,
            risk_score=70,
            risk_level="critical",
            status="open",
            created_at=now,
        ),
        AiOrderAudit(
            organization_id=org_id,
            order_id=2,
            risk_score=30,
            risk_level="medium",
            status="open",
            created_at=now,
        ),
        AiOrderAudit(
            organization_id=org_id,
            order_id=3,
            risk_score=50,
            risk_level="high",
            status="open",
            created_at=now,
        ),
    ])
    await db_session.flush()

    rows = await list_order_ai_audits(
        db_session,
        org_id,
        status="open",
        period="today",
        risk_level="high,critical",
    )
    levels = {r.risk_level for r in rows}
    assert levels == {"high", "critical"}


@pytest.mark.asyncio
async def test_qa_summary_counts_open_and_closed(db_session) -> None:
    from datetime import datetime, timedelta, timezone

    from app.db.models import AiOrderAudit
    from app.services.order_ai_audit import build_qa_risk_summary

    org = Organization(name="QA Summary", slug="qa-summary")
    db_session.add(org)
    await db_session.flush()
    org_id = int(org.id)
    now = datetime.now(timezone.utc)
    lo = now - timedelta(hours=1)
    hi = now + timedelta(hours=1)

    db_session.add_all([
        AiOrderAudit(
            organization_id=org_id,
            order_id=10,
            risk_level="high",
            status="open",
            created_at=now,
        ),
        AiOrderAudit(
            organization_id=org_id,
            order_id=11,
            risk_level="critical",
            status="reviewed",
            created_at=now,
        ),
        AiOrderAudit(
            organization_id=org_id,
            order_id=12,
            risk_level="medium",
            status="dismissed",
            created_at=now,
        ),
    ])
    await db_session.flush()

    summary = await build_qa_risk_summary(
        db_session,
        org_id,
        ts_lo=lo,
        ts_hi=hi,
    )
    assert summary["open_count"] == 1
    assert summary["closed_count"] == 2
    assert summary["high_count"] == 1
    assert summary["critical_count"] == 1
