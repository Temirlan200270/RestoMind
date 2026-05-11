"""Регрессии для временной паузы ИИ."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.db.models import Organization, User
from app.services.ai_snooze import ai_snooze_is_active, clear_ai_snooze_if_expired


def test_ai_snooze_accepts_naive_sqlite_datetime_as_utc() -> None:
    user = User(phone="+77000000001", organization_id=1)
    user.ai_snoozed_until = (datetime.now(timezone.utc) + timedelta(minutes=10)).replace(tzinfo=None)

    assert ai_snooze_is_active(user, now=datetime.now(timezone.utc)) is True


@pytest.mark.asyncio
async def test_clear_ai_snooze_accepts_naive_sqlite_datetime(db_session) -> None:
    org = Organization(name="SnoozeOrg", slug="snooze-org")
    db_session.add(org)
    await db_session.flush()
    user = User(phone="+77000000002", organization_id=int(org.id))
    user.ai_snoozed_until = (datetime.now(timezone.utc) - timedelta(minutes=10)).replace(tzinfo=None)
    db_session.add(user)
    await db_session.flush()

    await clear_ai_snooze_if_expired(db_session, user, now=datetime.now(timezone.utc))

    assert user.ai_snoozed_until is None
