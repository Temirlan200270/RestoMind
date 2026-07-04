import pytest
from sqlalchemy import select
from unittest.mock import AsyncMock, patch

from app.db.models import AgentActionProposal, InsightDelivery, OperationalInsight, Organization
from app.services.agent_action_tokens import create_agent_action_confirm_token, parse_agent_action_confirm_token
from app.services.agent_actions import (
    build_action_chain,
    confirm_agent_action,
    preview_agent_action,
    propose_agent_action,
)
from app.services.insight_delivery import deliver_due_insights
from app.services.insight_proactive_actions import build_proactive_action_from_insight


@pytest.mark.asyncio
async def test_preview_required_before_confirm_force_close(db_session):
    org = Organization(id=1, name="Org 1")
    db_session.add(org)
    await db_session.flush()

    row = await propose_agent_action(
        db_session,
        organization_id=1,
        action_type="force_close",
        title="Пауза",
        summary="Тест",
        payload={"minutes": 15, "reason": "test"},
    )
    with pytest.raises(ValueError, match="preview_required"):
        await confirm_agent_action(db_session, proposal_id=row.id, organization_id=1)

    await preview_agent_action(db_session, proposal_id=row.id, organization_id=1)
    result = await confirm_agent_action(db_session, proposal_id=row.id, organization_id=1)
    await db_session.refresh(org)

    assert result["ok"] is True
    assert org.force_closed_until is not None


@pytest.mark.asyncio
async def test_action_chain_includes_insight_lineage(db_session):
    org = Organization(id=1, name="Org 1")
    db_session.add(org)
    insight = OperationalInsight(
        organization_id=1,
        insight_type="sales_revenue_drop",
        severity="critical",
        title="Просадка",
        summary="Выручка -20%",
    )
    db_session.add(insight)
    await db_session.flush()

    row = await propose_agent_action(
        db_session,
        organization_id=1,
        action_type="upsell_rule_create",
        title="Upsell",
        summary="Тест",
        payload={"trigger_category": "Супы", "suggest_category": "Напитки"},
        source_insight_id=int(insight.id),
        trace_id="trace-abc",
    )
    await db_session.flush()

    chain = await build_action_chain(db_session, proposal_id=row.id, organization_id=1)
    assert chain["ok"] is True
    assert chain["lineage"]["insight"]["id"] == insight.id
    assert chain["lineage"]["command"]["trace_id"] == "trace-abc"


@pytest.mark.asyncio
async def test_confirm_token_roundtrip():
    token = create_agent_action_confirm_token(proposal_id="abc-123", organization_id=42)
    claims = parse_agent_action_confirm_token(token)
    assert claims is not None
    assert claims.proposal_id == "abc-123"
    assert claims.organization_id == 42


@pytest.mark.asyncio
async def test_proactive_action_from_revenue_insight():
    insight = OperationalInsight(
        organization_id=1,
        insight_type="sales_revenue_drop",
        severity="critical",
        title="Просадка",
        summary="Выручка ниже baseline",
    )
    spec = build_proactive_action_from_insight(insight)
    assert spec is not None
    assert spec["action_type"] in {"upsell_rule_create", "iiko_write_staged"}


@pytest.mark.asyncio
async def test_agent_commands_registry_metadata():
    from app.services.agent_commands import supported_agent_commands

    names = {row["name"] for row in supported_agent_commands()}
    assert "ForceCloseRestaurantCommand" in names
    assert "StageIikoWriteCommand" in names
    for row in supported_agent_commands():
        assert "required_role" in row
        assert "requires_preview" in row


@pytest.mark.asyncio
async def test_digest_agent_actions_from_critical_insight(db_session, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.public_base_url", "https://digest.test")
    org = Organization(id=1, name="Digest Org", timezone="UTC")
    db_session.add(org)
    insight = OperationalInsight(
        organization_id=1,
        insight_type="cancellation_surge",
        severity="critical",
        title="Всплеск отмен",
        summary="Отмены выше нормы",
        status="new",
    )
    db_session.add(insight)
    await db_session.flush()

    from datetime import datetime, timedelta, timezone

    from app.services.digest_agent_actions import (
        append_actions_to_digest_html,
        build_digest_agent_actions,
        digest_actions_reply_markup,
    )

    now = datetime.now(tz=timezone.utc)
    actions = await build_digest_agent_actions(
        db_session,
        1,
        start=now - timedelta(hours=1),
        end=now + timedelta(hours=1),
        source="owner_weekly_digest",
        idempotency_prefix="digest:test:1",
    )
    assert len(actions) == 1
    assert actions[0]["action_type"] == "force_close"
    assert actions[0]["proposal_id"]
    assert actions[0]["confirm_url"]
    assert "/api/public/agent-actions/confirm?token=" in actions[0]["confirm_url"]

    html = append_actions_to_digest_html("<b>Digest</b>", actions)
    assert "Подтвердить" in html
    markup = digest_actions_reply_markup(actions)
    assert markup is not None
    assert markup["inline_keyboard"][0][0]["url"] == actions[0]["confirm_url"]

    proposal = await db_session.get(AgentActionProposal, actions[0]["proposal_id"])
    assert proposal is not None
    assert proposal.source == "owner_weekly_digest"
    assert proposal.source_insight_id == insight.id


@pytest.mark.asyncio
async def test_proactive_action_from_upsell_recommendation():
    from app.db.models import BusinessRecommendation
    from app.services.insight_proactive_actions import build_proactive_action_from_recommendation

    rec = BusinessRecommendation(
        organization_id=1,
        recommendation_type="upsell_pair",
        title="Пара суп + напиток",
        body="Конверсия пары 12%",
        status="new",
    )
    spec = build_proactive_action_from_recommendation(rec)
    assert spec is not None
    assert spec["action_type"] == "upsell_rule_create"


@pytest.mark.asyncio
async def test_send_weekly_digest_includes_agent_action_confirm(db_session, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.public_base_url", "https://digest.test")
    org = Organization(name="Weekly Org", slug="weekly-org", timezone="UTC", is_active=True)
    db_session.add(org)
    await db_session.flush()
    org_id = int(org.id)
    insight = OperationalInsight(
        organization_id=org_id,
        insight_type="ai_message_drop",
        severity="warning",
        title="Просадка AI",
        summary="Мало ответов бота",
        status="new",
    )
    db_session.add(insight)
    await db_session.flush()

    from datetime import datetime, timedelta, timezone
    from unittest.mock import AsyncMock, patch

    from app.services.owner_digest_delivery import send_weekly_digest

    now = datetime.now(tz=timezone.utc)
    mock_digest = AsyncMock(
        return_value={"text": "RestoMind — неделя\n• Net ROI: 0 ₸", "metrics": {"net_roi": 0}},
    )
    mock_send = AsyncMock()

    with patch(
        "app.services.owner_digest_delivery._build_digest_payload",
        mock_digest,
    ), patch(
        "app.services.owner_digest_delivery.send_ops_notification_html",
        mock_send,
    ), patch(
        "app.services.owner_digest_delivery._redis_set_once",
        AsyncMock(return_value=True),
    ), patch(
        "app.services.owner_digest_delivery.settings.telegram_bot_token",
        "test:owner-digest",
    ), patch(
        "app.services.owner_digest_delivery._previous_week_bounds_local",
        lambda _tz: (now - timedelta(days=7), now),
    ):
        result = await send_weekly_digest(
            db_session,
            org_id,
            force=True,
            triggered_by="admin",
        )

    assert result["ok"] is True
    assert result["sent"] is True
    assert len(result.get("agent_actions") or []) == 1
    mock_send.assert_awaited_once()
    sent_html = mock_send.await_args.kwargs.get("html") or mock_send.await_args.args[0]
    assert "Подтвердить" in sent_html
    reply_markup = mock_send.await_args.kwargs.get("reply_markup")
    assert reply_markup is not None


@pytest.mark.asyncio
async def test_insight_delivery_payload_includes_agent_action_proposal_id(db_session):
    org = Organization(id=1, name="Org 1", slug="insight-delivery-org", is_active=True)
    insight = OperationalInsight(
        organization_id=1,
        insight_type="sales_revenue_drop",
        severity="critical",
        title="Просадка",
        summary="Выручка -20%",
        status="new",
    )
    db_session.add(org)
    db_session.add(insight)
    await db_session.flush()

    mock_send = AsyncMock()
    with patch("app.services.insight_delivery.send_ops_notification_html", mock_send):
        sent = await deliver_due_insights(db_session, 1, channel="telegram_owner")

    assert sent == 1
    mock_send.assert_awaited_once()

    delivery = (
        await db_session.execute(
            select(InsightDelivery).where(InsightDelivery.organization_id == 1),
        )
    ).scalar_one()
    proposal_id = delivery.payload_json.get("agent_action_proposal_id")
    assert proposal_id is not None

    proposal = await db_session.get(AgentActionProposal, proposal_id)
    assert proposal is not None
    assert proposal.source == "insight_delivery"
    assert int(proposal.source_insight_id) == int(insight.id)


@pytest.mark.asyncio
async def test_public_confirm_endpoint_with_valid_token(asgi_memory_client):
    client, session_factory = asgi_memory_client
    async with session_factory() as db:
        org = Organization(name="Public Confirm Org", slug="public-confirm-org", is_active=True)
        db.add(org)
        await db.flush()
        org_id = int(org.id)

        row = await propose_agent_action(
            db,
            organization_id=org_id,
            action_type="force_close",
            title="Пауза",
            summary="Тест public confirm",
            payload={"minutes": 15, "reason": "public confirm test"},
        )
        await preview_agent_action(db, proposal_id=row.id, organization_id=org_id)
        proposal_id = row.id
        await db.commit()

    token = create_agent_action_confirm_token(proposal_id=proposal_id, organization_id=org_id)
    page = await client.get(f"/api/public/agent-actions/confirm?token={token}")

    assert page.status_code == 200
    assert "Подтвердите действие" in page.text
    assert "Подтвердить и применить" in page.text

    async with session_factory() as db:
        org = await db.get(Organization, org_id)
        assert org.force_closed_until is None

    resp = await client.post(f"/api/public/agent-actions/confirm?token={token}")

    assert resp.status_code == 200
    assert "Действие применено" in resp.text

    async with session_factory() as db:
        org = await db.get(Organization, org_id)
        assert org.force_closed_until is not None


@pytest.mark.asyncio
async def test_preview_gate_blocks_iiko_write_confirm_without_preview(db_session):
    org = Organization(id=1, name="Org 1", slug="preview-gate-org")
    db_session.add(org)
    await db_session.flush()

    row = await propose_agent_action(
        db_session,
        organization_id=1,
        action_type="iiko_write_staged",
        title="Смена цены",
        summary="Тест preview gate",
        payload={"operation": "menu_price_update", "items": [{"label": "Плов +200"}]},
    )
    with pytest.raises(ValueError, match="preview_required"):
        await confirm_agent_action(
            db_session,
            proposal_id=row.id,
            organization_id=1,
            staff_role="admin",
        )


@pytest.mark.asyncio
async def test_operator_cannot_confirm_iiko_write(asgi_memory_client):
    from app.core.passwords import hash_password
    from app.db.models import StaffUser

    client, session_factory = asgi_memory_client
    async with session_factory() as db:
        org = Organization(name="Iiko RBAC Org", slug="iiko-rbac-org", is_active=True)
        db.add(org)
        await db.flush()
        org_id = int(org.id)
        db.add(
            StaffUser(
                organization_id=org_id,
                email="admin-iiko@test.kz",
                password_hash=hash_password("secret123"),
                role="admin",
                is_active=True,
            ),
        )
        db.add(
            StaffUser(
                organization_id=org_id,
                email="op-iiko@test.kz",
                password_hash=hash_password("secret123"),
                role="operator",
                is_active=True,
            ),
        )

        row = await propose_agent_action(
            db,
            organization_id=org_id,
            action_type="iiko_write_staged",
            title="Смена цены",
            summary="Тест RBAC",
            payload={"operation": "menu_price_update", "items": [{"label": "Плов +200"}]},
        )
        await preview_agent_action(db, proposal_id=row.id, organization_id=org_id, staff_role="admin")
        proposal_id = row.id
        await db.commit()

    await client.post(
        "/api/admin/auth/login",
        json={"username": "op-iiko@test.kz", "password": "secret123"},
    )
    resp = await client.post(f"/api/admin/intelligence/agent-actions/{proposal_id}/confirm")

    assert resp.status_code == 403
    assert resp.json()["detail"] == "Role not allowed"
