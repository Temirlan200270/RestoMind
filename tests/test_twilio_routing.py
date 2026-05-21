"""Tests for Twilio Voice tenant routing."""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.db.models import Organization
from app.services.twilio_routing import normalize_e164, resolve_org_from_twilio_number


def test_normalize_e164() -> None:
    assert normalize_e164("+77051234567") == "+77051234567"
    assert normalize_e164("77051234567") == "+77051234567"
    assert normalize_e164("") == ""


@pytest.mark.asyncio
async def test_resolve_org_by_meta_twilio_number(db_session) -> None:
    db_session.add(
        Organization(
            id=1,
            name="Org A",
            slug="org-a",
            meta_json={"twilio_voice_number": "+15551110001"},
        )
    )
    db_session.add(
        Organization(
            id=2,
            name="Org B",
            slug="org-b",
            meta_json={"twilio_voice_number": "+15552220002"},
        )
    )
    await db_session.flush()

    org, oid = await resolve_org_from_twilio_number(db_session, "+15552220002")
    assert oid == 2
    assert org is not None
    assert org.name == "Org B"


@pytest.mark.asyncio
async def test_resolve_org_env_fallback(monkeypatch, db_session) -> None:
    monkeypatch.setattr(settings, "twilio_voice_number", "+15559990000")
    monkeypatch.setattr(settings, "default_organization_id", 1)
    db_session.add(Organization(id=1, name="Default", slug="default", meta_json={}))
    await db_session.flush()

    org, oid = await resolve_org_from_twilio_number(db_session, "+15559990000")
    assert oid == 1
    assert org is not None


@pytest.mark.asyncio
async def test_resolve_location_by_meta_twilio_number(db_session) -> None:
    from app.db.models import Location
    from app.services.twilio_routing import resolve_location_from_twilio_number

    org = Organization(id=10, name="Loc Org", slug="loc-org", meta_json={})
    db_session.add(org)
    await db_session.flush()
    db_session.add(
        Location(
            organization_id=10,
            name="Branch A",
            slug="branch-a",
            meta_json={"twilio_voice_number": "+15553334444"},
        )
    )
    db_session.add(
        Location(
            organization_id=10,
            name="Branch B",
            slug="branch-b",
            meta_json={"twilio_voice_number": "+15553335555"},
        )
    )
    await db_session.flush()

    loc_id = await resolve_location_from_twilio_number(db_session, 10, "+15553335555")
    assert loc_id is not None
    loc = await db_session.get(Location, int(loc_id))
    assert loc is not None
    assert loc.slug == "branch-b"


@pytest.mark.asyncio
async def test_resolve_org_default_when_no_match(monkeypatch, db_session) -> None:
    monkeypatch.setattr(settings, "default_organization_id", 7)
    db_session.add(Organization(id=7, name="Fallback", slug="fb", meta_json={}))
    await db_session.flush()

    org, oid = await resolve_org_from_twilio_number(db_session, "+19998887777")
    assert oid == 7
    assert org is not None
