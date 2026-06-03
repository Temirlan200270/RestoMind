import pytest
from sqlalchemy import select

from app.db.models import Organization, SystemEvent, User
from app.services.dialog_mgr import (
    clear_human_mode_ttl_meta,
    parse_human_mode_until,
    update_user_session_fields_in_db,
    with_human_mode_ttl_meta,
)


@pytest.mark.asyncio
async def test_state_change_writes_durable_system_event(db_session) -> None:
    org = Organization(name="FSM", slug="fsm")
    db_session.add(org)
    await db_session.flush()
    user = User(
        organization_id=int(org.id),
        phone="+77005554433",
        current_state="chatting",
    )
    db_session.add(user)
    await db_session.flush()

    await update_user_session_fields_in_db(
        db_session,
        phone=user.phone,
        organization_id=int(org.id),
        current_state="human_mode",
        transition_source="admin.chats",
        transition_reason="operator_takeover",
    )
    await db_session.flush()

    refreshed = await db_session.get(User, int(user.id))
    assert refreshed is not None
    assert refreshed.current_state == "human_mode"
    assert refreshed.session_version == 1

    event = await db_session.scalar(
        select(SystemEvent).where(SystemEvent.event_type == "conversation.state_changed"),
    )
    assert event is not None
    assert event.source == "admin.chats"
    assert event.payload_json["phone"] == user.phone
    assert event.payload_json["from_state"] == "chatting"
    assert event.payload_json["to_state"] == "human_mode"
    assert event.payload_json["reason"] == "operator_takeover"


@pytest.mark.asyncio
async def test_same_state_update_does_not_emit_transition_event(db_session) -> None:
    org = Organization(name="FSM2", slug="fsm2")
    db_session.add(org)
    await db_session.flush()
    user = User(
        organization_id=int(org.id),
        phone="+77005550000",
        current_state="chatting",
    )
    db_session.add(user)
    await db_session.flush()

    await update_user_session_fields_in_db(
        db_session,
        phone=user.phone,
        organization_id=int(org.id),
        current_state="chatting",
        transition_source="admin.test",
    )
    await db_session.flush()

    count = await db_session.scalar(
        select(SystemEvent.id).where(SystemEvent.event_type == "conversation.state_changed"),
    )
    assert count is None


@pytest.mark.asyncio
async def test_stale_state_update_is_skipped(db_session) -> None:
    org = Organization(name="FSM stale", slug="fsm-stale")
    db_session.add(org)
    await db_session.flush()
    user = User(
        organization_id=int(org.id),
        phone="+77005551111",
        current_state="human_mode",
    )
    db_session.add(user)
    await db_session.flush()

    applied = await update_user_session_fields_in_db(
        db_session,
        phone=user.phone,
        organization_id=int(org.id),
        current_state="chatting",
        transition_source="webhooks.process_message",
        expected_current_state="chatting",
    )
    await db_session.flush()

    refreshed = await db_session.get(User, int(user.id))
    assert applied is False
    assert refreshed is not None
    assert refreshed.current_state == "human_mode"
    assert refreshed.session_version == 0


@pytest.mark.asyncio
async def test_session_version_blocks_stale_llm_write(db_session) -> None:
    org = Organization(name="FSM version", slug="fsm-version")
    db_session.add(org)
    await db_session.flush()
    user = User(
        organization_id=int(org.id),
        phone="+77005552222",
        current_state="chatting",
    )
    db_session.add(user)
    await db_session.flush()

    applied = await update_user_session_fields_in_db(
        db_session,
        phone=user.phone,
        organization_id=int(org.id),
        current_state="confirming_order",
        current_pending_order_id=42,
        transition_source="webhooks.process_message",
        expected_current_state="chatting",
        expected_session_version=1,
    )
    await db_session.flush()

    refreshed = await db_session.get(User, int(user.id))
    assert applied is False
    assert refreshed is not None
    assert refreshed.current_state == "chatting"
    assert refreshed.current_pending_order_id is None
    assert refreshed.session_version == 0


def test_human_mode_ttl_meta_roundtrip() -> None:
    meta = with_human_mode_ttl_meta({"vip": True})
    assert meta is not None
    assert meta["vip"] is True
    assert parse_human_mode_until(meta) is not None

    cleared = clear_human_mode_ttl_meta(meta)
    assert cleared == {"vip": True}
