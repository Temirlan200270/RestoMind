"""Tests for menu cost CSV preview/apply."""

from __future__ import annotations

import pytest

from app.db.models import MenuItem, Organization
from app.services.menu_cost_import import import_menu_costs_from_csv, preview_menu_costs_from_csv


@pytest.mark.asyncio
async def test_preview_menu_costs_matches_by_iiko_id(db_session) -> None:
    org = Organization(name="Cost Org", slug="cost-org")
    db_session.add(org)
    await db_session.flush()
    db_session.add(
        MenuItem(
            organization_id=int(org.id),
            name="Плов",
            price=3000.0,
            cost_price=1000.0,
            iiko_id="uuid-plov",
        ),
    )
    await db_session.flush()

    csv_text = "iiko_id,cost_price\nuuid-plov,1500\n"
    preview = await preview_menu_costs_from_csv(db_session, int(org.id), csv_text)

    assert preview["ok"] is True
    assert preview["updated"] == 1
    assert len(preview["rows"]) == 1
    assert preview["rows"][0]["name"] == "Плов"
    assert preview["rows"][0]["new_cost_price"] == 1500.0
    assert preview["rows"][0]["changed"] is True


@pytest.mark.asyncio
async def test_import_menu_costs_applies_changes(db_session) -> None:
    org = Organization(name="Apply Org", slug="apply-org")
    db_session.add(org)
    await db_session.flush()
    item = MenuItem(
        organization_id=int(org.id),
        name="Суп",
        price=2000.0,
        cost_price=None,
        iiko_id="uuid-soup",
    )
    db_session.add(item)
    await db_session.flush()

    csv_text = "name,cost\nСуп,900\n"
    result = await import_menu_costs_from_csv(db_session, int(org.id), csv_text)
    await db_session.refresh(item)

    assert result["ok"] is True
    assert result["updated"] == 1
    assert float(item.cost_price) == 900.0


@pytest.mark.asyncio
async def test_preview_skips_unchanged_costs(db_session) -> None:
    org = Organization(name="Skip Org", slug="skip-org")
    db_session.add(org)
    await db_session.flush()
    db_session.add(
        MenuItem(
            organization_id=int(org.id),
            name="Чай",
            price=500.0,
            cost_price=100.0,
            iiko_id="uuid-tea",
        ),
    )
    await db_session.flush()

    csv_text = "iiko_id,cost_price\nuuid-tea,100\n"
    preview = await preview_menu_costs_from_csv(db_session, int(org.id), csv_text)

    assert preview["updated"] == 0
    assert preview["skipped"] == 1
    assert preview["rows"][0]["changed"] is False
