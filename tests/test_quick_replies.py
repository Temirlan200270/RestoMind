"""Тесты quick_replies (LLM bypass)."""

import pytest

from app.db.models import Organization
from app.services.dialog_mgr import UserState
from app.services.quick_replies import (
    is_plain_greeting,
    try_quick_reply,
)


@pytest.mark.asyncio
async def test_greeting_plain() -> None:
    hit = await try_quick_reply(
        phone="+77001112233",
        organization_id=1,
        message_text="привет",
        state=UserState.CHATTING,
        has_open_draft=False,
    )
    assert hit is not None
    assert hit.template_id == "greeting_plain"
    assert "Чем могу помочь" in hit.reply_text


@pytest.mark.asyncio
async def test_thanks() -> None:
    hit = await try_quick_reply(
        phone="+77001112233",
        organization_id=1,
        message_text="спасибо",
        state=UserState.CHATTING,
        has_open_draft=False,
    )
    assert hit is not None
    assert hit.template_id == "thanks"


@pytest.mark.asyncio
async def test_operator_request() -> None:
    hit = await try_quick_reply(
        phone="+77001112233",
        organization_id=1,
        message_text="оператор",
        state=UserState.CHATTING,
        has_open_draft=False,
    )
    assert hit is not None
    assert hit.template_id == "operator_request"
    assert hit.set_human_mode is True
    assert "alert_operator_telegram" in hit.side_effects


@pytest.mark.asyncio
async def test_cancel_without_draft_returns_none() -> None:
    hit = await try_quick_reply(
        phone="+77001112233",
        organization_id=1,
        message_text="отмена",
        state=UserState.CHATTING,
        has_open_draft=False,
    )
    assert hit is None


@pytest.mark.asyncio
async def test_cancel_with_draft() -> None:
    hit = await try_quick_reply(
        phone="+77001112233",
        organization_id=1,
        message_text="отмена",
        state=UserState.CHATTING,
        has_open_draft=True,
    )
    assert hit is not None
    assert hit.template_id == "cancel_order"
    assert "cancel_open_draft" in hit.side_effects


@pytest.mark.asyncio
async def test_long_message_no_match() -> None:
    long_text = "отмените пожалуйста тот плов и добавьте ещё манты три порции"
    hit = await try_quick_reply(
        phone="+77001112233",
        organization_id=1,
        message_text=long_text,
        state=UserState.CHATTING,
        has_open_draft=True,
    )
    assert hit is None


@pytest.mark.asyncio
async def test_working_hours_needs_org() -> None:
    org = Organization(id=1, name="T", slug="t", timezone="Asia/Almaty")
    hit = await try_quick_reply(
        phone="+77001112233",
        organization_id=1,
        message_text="время работы",
        state=UserState.CHATTING,
        has_open_draft=False,
        org=org,
    )
    assert hit is not None
    assert hit.template_id == "working_hours"
    assert "Сегодня" in hit.reply_text or "выходной" in hit.reply_text.lower()


def test_is_plain_greeting_rejects_menu_intent() -> None:
    assert is_plain_greeting("привет, меню") is False
