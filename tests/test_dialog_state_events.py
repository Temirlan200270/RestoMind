import pytest
from sqlalchemy import select

from app.db.models import Organization, SystemEvent, User
from app.services.dialog_mgr import update_user_session_fields_in_db


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

    event = await db_session.scalar(
        select(SystemEvent).where(SystemEvent.event_type == "conversation_state_changed"),
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
        select(SystemEvent.id).where(SystemEvent.event_type == "conversation_state_changed"),
    )
    assert count is None
