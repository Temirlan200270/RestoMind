import pytest
from sqlalchemy import select

from app.db.models import Organization, SystemEvent, UpsellRule
from app.services.agent_actions import (
    confirm_agent_action,
    detect_conversational_action_proposals,
    preview_agent_action,
    propose_agent_action,
)
from app.services.agent_commands import supported_agent_commands


@pytest.mark.asyncio
async def test_detect_force_close_proposal():
    specs = detect_conversational_action_proposals("Закрой ресторан на 45 минут из-за перегруза кухни")
    assert len(specs) == 1
    assert specs[0]["action_type"] == "force_close"
    assert specs[0]["payload"]["minutes"] == 45


@pytest.mark.asyncio
async def test_confirm_force_close_applies(db_session):
    org = Organization(id=1, name="Org 1")
    db_session.add(org)
    await db_session.flush()

    row = await propose_agent_action(
        db_session,
        organization_id=1,
        action_type="force_close",
        title="Пауза 30 мин",
        summary="Тест",
        payload={"minutes": 30, "reason": "test"},
    )
    await preview_agent_action(db_session, proposal_id=row.id, organization_id=1)
    result = await confirm_agent_action(
        db_session,
        proposal_id=row.id,
        organization_id=1,
        staff_user_id=None,
    )
    await db_session.refresh(org)

    assert result["ok"] is True
    assert org.force_closed_until is not None
    assert org.force_closed_reason == "test"

    events = (
        await db_session.execute(
            select(SystemEvent.event_type).where(SystemEvent.organization_id == 1).order_by(SystemEvent.id),
        )
    ).scalars().all()
    assert "agent_action.proposed" in events
    assert "agent_action.confirmed" in events
    assert "agent_action.applied" in events


@pytest.mark.asyncio
async def test_confirm_upsell_rule_create(db_session):
    org = Organization(id=1, name="Org 1")
    db_session.add(org)
    await db_session.flush()

    row = await propose_agent_action(
        db_session,
        organization_id=1,
        action_type="upsell_rule_create",
        title="Допродажа супов",
        summary="Тест",
        payload={
            "trigger_category": "Супы",
            "suggest_category": "Напитки",
        },
    )
    await preview_agent_action(db_session, proposal_id=row.id, organization_id=1)
    result = await confirm_agent_action(
        db_session,
        proposal_id=row.id,
        organization_id=1,
    )
    rules = (await db_session.execute(select(UpsellRule).where(UpsellRule.organization_id == 1))).scalars().all()

    assert result["ok"] is True
    assert len(rules) == 1
    assert rules[0].trigger_category == "Супы"
    assert rules[0].suggest_category == "Напитки"


@pytest.mark.asyncio
async def test_agent_action_command_metadata_and_validation(db_session):
    org = Organization(id=1, name="Org 1")
    db_session.add(org)
    await db_session.flush()

    commands = supported_agent_commands()
    assert {row["name"] for row in commands} >= {
        "ForceCloseRestaurantCommand",
        "CreateUpsellRuleCommand",
        "StageIikoWriteCommand",
    }

    row = await propose_agent_action(
        db_session,
        organization_id=1,
        action_type="iiko_write_staged",
        title="Смена цены",
        summary="Тест",
        payload={"operation": "menu_price_update", "items": [{"label": "Плов +200"}]},
    )
    assert row.payload_json["_command"]["name"] == "StageIikoWriteCommand"
    assert row.payload_json["_command"]["risk_level"] == "high"
    assert row.payload_json["_command"]["external_side_effect"] is True

    with pytest.raises(ValueError, match="iiko_write_items_required"):
        await propose_agent_action(
            db_session,
            organization_id=1,
            action_type="iiko_write_staged",
            title="Пустой iiko write",
            summary="Тест",
            payload={"items": []},
        )
