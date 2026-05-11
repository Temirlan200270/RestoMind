"""
P0 «Operator outbound»: ChatLog фиксируется до отправки в WhatsApp.

Контракт: даже если внешний WhatsApp-вызов упадёт, запись `chat_logs` уже
лежит в БД со статусом `sending`/`failed`. Это исключает «потерю» сообщения
оператора, когда транзакция бы откатилась после внешнего I/O.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select

from app.api.admin.chats import admin_send_message
from app.api.admin.schemas import TextRequest
from app.db.models import ChatLog, Organization, User


@pytest.mark.asyncio
async def test_admin_send_message_persists_log_before_send_on_success(db_session, monkeypatch) -> None:
    """Успешная отправка: запись ChatLog существует до WhatsApp и финализируется в `sent`."""
    db_session.add(Organization(id=1, name="Org", slug="org"))
    await db_session.flush()

    captured_log_state: dict[str, str | None] = {}

    class WaResult:
        ok = True
        message_id = "wamid.persist"
        error = None

    async def fake_send_message(phone: str, text: str) -> WaResult:
        rows = await db_session.execute(select(ChatLog))
        row = rows.scalars().first()
        captured_log_state["delivery_status"] = row.delivery_status if row else None
        captured_log_state["provider_message_id"] = row.provider_message_id if row else None
        return WaResult()

    monkeypatch.setattr("app.api.admin.chats.send_message", AsyncMock(side_effect=fake_send_message))

    req = MagicMock()
    req.session = {"organization_id": 1}

    out = await admin_send_message(req, "+77001112233", TextRequest(text="Здравствуйте"), db=db_session)
    assert out["status"] == "sent"
    assert out["chat_log_id"]

    assert captured_log_state["delivery_status"] == "sending"
    assert captured_log_state["provider_message_id"] is None

    rows = await db_session.execute(select(ChatLog))
    final_log = rows.scalars().first()
    assert final_log is not None
    assert final_log.delivery_status == "sent"
    assert final_log.provider_message_id == "wamid.persist"

    user = (await db_session.execute(select(User))).scalars().first()
    assert user is not None
    assert user.phone == "+77001112233"
    assert int(user.organization_id) == 1


@pytest.mark.asyncio
async def test_admin_send_message_records_failed_on_provider_error(db_session, monkeypatch) -> None:
    """Если WhatsApp ответил неуспехом — лог остаётся в БД и помечается `failed`."""
    db_session.add(Organization(id=1, name="Org", slug="org"))
    await db_session.flush()

    class WaResult:
        ok = False
        message_id = None
        error = {"code": 131000, "message": "boom"}

    monkeypatch.setattr(
        "app.api.admin.chats.send_message",
        AsyncMock(return_value=WaResult()),
    )

    req = MagicMock()
    req.session = {"organization_id": 1}

    out = await admin_send_message(req, "+77001112233", TextRequest(text="Привет"), db=db_session)
    assert out["status"] == "failed"

    rows = await db_session.execute(select(ChatLog))
    log = rows.scalars().first()
    assert log is not None
    assert log.delivery_status == "failed"
    assert log.error_details == {"code": 131000, "message": "boom"}
