"""Мультитенантные границы админки: фильтр WebSocket и переотправка failed-сообщений."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.api.admin import _ws_event_allowed_for_org, resend_failed_chat_message
from app.db.models import ChatLog, Organization, User
from app.services.admin_tokens import AdminWsClaims


def test_ws_event_allowed_same_org() -> None:
    claims = AdminWsClaims(email="op@test.local", organization_id=7, staff_id=3)
    payload = '{"type":"new_message","data":{"phone":"+1","organization_id":7}}'
    assert _ws_event_allowed_for_org(payload, claims) is True


def test_ws_event_blocked_other_org() -> None:
    claims = AdminWsClaims(email="op@test.local", organization_id=7, staff_id=3)
    payload = '{"type":"order_updated","data":{"organization_id":99}}'
    assert _ws_event_allowed_for_org(payload, claims) is False


def test_ws_event_blocked_without_org_in_payload() -> None:
    """Без organization_id событие не показываем — иначе общий Redis-канал утекает между арендаторами."""
    claims = AdminWsClaims(email="op@test.local", organization_id=7, staff_id=3)
    assert _ws_event_allowed_for_org('{"type":"legacy","data":{}}', claims) is False


def test_ws_malformed_json_blocked() -> None:
    claims = AdminWsClaims(email="op@test.local", organization_id=7, staff_id=3)
    assert _ws_event_allowed_for_org("not-json", claims) is False


def test_ws_event_blocked_invalid_org_value() -> None:
    claims = AdminWsClaims(email="op@test.local", organization_id=7, staff_id=3)
    payload = '{"type":"x","data":{"organization_id":null}}'
    assert _ws_event_allowed_for_org(payload, claims) is False


@pytest.mark.asyncio
async def test_resend_failed_operator_message(db_session, monkeypatch) -> None:
    db_session.add(Organization(id=1, name="Org", slug="org"))
    user = User(organization_id=1, phone="+77001112233")
    db_session.add(user)
    await db_session.flush()
    log = ChatLog(
        organization_id=1,
        user_id=user.id,
        role="operator",
        content="Привет",
        delivery_status="failed",
    )
    db_session.add(log)
    await db_session.flush()

    class WaResult:
        ok = True
        message_id = "wamid.test"
        error = None

    monkeypatch.setattr("app.api.admin.send_message", AsyncMock(return_value=WaResult()))

    req = MagicMock()
    req.session = {"organization_id": 1}

    out = await resend_failed_chat_message(req, "+77001112233", log.id, db=db_session)
    assert out["status"] == "sent"
    assert out["chat_log_id"] == log.id

    await db_session.refresh(log)
    assert log.delivery_status == "sent"
    assert log.provider_message_id == "wamid.test"


@pytest.mark.asyncio
async def test_resend_wrong_org_404(db_session, monkeypatch) -> None:
    db_session.add(Organization(id=1, name="A", slug="a"))
    db_session.add(Organization(id=2, name="B", slug="b"))
    user = User(organization_id=1, phone="+77001112233")
    db_session.add(user)
    await db_session.flush()
    log = ChatLog(
        organization_id=1,
        user_id=user.id,
        role="operator",
        content="x",
        delivery_status="failed",
    )
    db_session.add(log)
    await db_session.flush()

    monkeypatch.setattr("app.api.admin.send_message", AsyncMock())

    req = MagicMock()
    req.session = {"organization_id": 2}

    with pytest.raises(HTTPException) as exc_info:
        await resend_failed_chat_message(req, "+77001112233", log.id, db=db_session)
    assert exc_info.value.status_code == 404
