"""E.164 phone canonicalization, legacy lookup, merge and queue-wait metrics."""

from __future__ import annotations

import asyncio
import time

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ChatLog, Organization, User
from app.db.session import InMemoryRedis
from app.services.intent_router import get_or_create_user
from app.services.phone_normalize import (
    canonical_user_phone,
    normalize_phone_e164,
    phone_digits_key,
    phone_lookup_variants,
)
from app.services.user_phone_merge import find_duplicate_phone_groups, merge_duplicate_users
from app.services.user_phone_resolve import find_user_by_phone
from app.services import wa_queue_metrics as wqm


def test_normalize_phone_e164_kz_variants() -> None:
    assert normalize_phone_e164("+77051310837") == "+77051310837"
    assert normalize_phone_e164("77051310837") == "+77051310837"
    assert normalize_phone_e164("7051310837") == "+77051310837"


def test_phone_lookup_variants_includes_legacy() -> None:
    variants = phone_lookup_variants("77051310837")
    assert "+77051310837" in variants
    assert "77051310837" in variants


@pytest.mark.asyncio
async def test_find_user_by_phone_legacy_format(db_session: AsyncSession) -> None:
    db_session.add(Organization(id=1, name="O", slug="o"))
    db_session.add(User(phone="77051310837", organization_id=1))
    await db_session.flush()

    user = await find_user_by_phone(db_session, 1, "+77051310837")
    assert user is not None
    assert user.phone == "77051310837"


@pytest.mark.asyncio
async def test_get_or_create_user_reuses_legacy_and_canonicalizes(db_session: AsyncSession) -> None:
    db_session.add(Organization(id=1, name="O", slug="o"))
    legacy = User(phone="77051310837", organization_id=1)
    db_session.add(legacy)
    await db_session.flush()
    legacy_id = legacy.id

    user = await get_or_create_user(db_session, "+77051310837", 1)
    assert user.id == legacy_id
    assert user.phone == "+77051310837"


@pytest.mark.asyncio
async def test_get_or_create_user_new_user_is_e164(db_session: AsyncSession) -> None:
    db_session.add(Organization(id=1, name="O", slug="o"))
    await db_session.flush()

    user = await get_or_create_user(db_session, "77051112233", 1)
    assert user.phone == "+77051112233"


@pytest.mark.asyncio
async def test_find_duplicate_phone_groups(db_session: AsyncSession) -> None:
    db_session.add(Organization(id=1, name="O", slug="o"))
    db_session.add(User(phone="77051310837", organization_id=1))
    db_session.add(User(phone="+77051310837", organization_id=1))
    await db_session.flush()

    groups = await find_duplicate_phone_groups(db_session, 1)
    assert len(groups) == 1
    assert groups[0].digits == phone_digits_key("+77051310837")
    assert len(groups[0].users) == 2


@pytest.mark.asyncio
async def test_merge_duplicate_users_moves_chat_logs(db_session: AsyncSession) -> None:
    db_session.add(Organization(id=1, name="O", slug="o"))
    keep = User(phone="+77051310837", organization_id=1)
    dup = User(phone="77051310837", organization_id=1)
    db_session.add_all([keep, dup])
    await db_session.flush()
    db_session.add(
        ChatLog(
            organization_id=1,
            user_id=dup.id,
            role="user",
            content="hello",
        ),
    )
    await db_session.flush()
    keep_id = int(keep.id)
    dup_id = int(dup.id)

    reports = await merge_duplicate_users(db_session, 1, dry_run=False)
    assert len(reports) == 1
    assert reports[0]["keep_user_id"] == keep_id
    assert reports[0]["moved"]["chat_logs"] == 1

    from sqlalchemy import select

    db_session.expire_all()
    logs = (
        await db_session.scalars(select(ChatLog).where(ChatLog.organization_id == 1))
    ).all()
    assert len(logs) == 1
    assert int(logs[0].user_id) == keep_id

    dup_after = await db_session.get(User, dup_id)
    assert dup_after is None


@pytest.mark.asyncio
async def test_wa_queue_metrics_mark_and_pop(monkeypatch) -> None:
    fake = InMemoryRedis()
    monkeypatch.setattr(wqm, "redis_client", fake)
    monkeypatch.setattr(wqm.settings, "redis_enabled", True)

    await wqm.mark_whatsapp_enqueued(trace_id="t1", whatsapp_message_id="wmid-abc")
    await asyncio.sleep(0.01)
    wait_ms = await wqm.pop_queue_wait_ms(trace_id="t1", whatsapp_message_id="wmid-abc")
    assert wait_ms is not None
    assert wait_ms >= 5.0

    again = await wqm.pop_queue_wait_ms(trace_id="t1", whatsapp_message_id="wmid-abc")
    assert again is None
