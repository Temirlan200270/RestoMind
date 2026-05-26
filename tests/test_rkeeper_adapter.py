"""Tests for r_keeper POS adapter (Wave 4 Phase 2)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.db.models import MenuItem, Organization
from app.integrations.rkeeper_client import RKeeperClient
from app.services.org_rkeeper import resolve_org_rkeeper_credentials
from app.services.pos.adapters.base import ADAPTER_REGISTRY, get_pos_adapter
from app.services.pos.adapters.rkeeper_adapter import RKeeperPOSAdapter


@pytest.mark.asyncio
async def test_rkeeper_client_stub_health_and_menu():
    client = RKeeperClient(server_url="http://rk.local", object_id="obj-1")

    health = await client.health()
    assert health["ok"] is True
    assert health["provider"] == "rkeeper"

    menu = await client.fetch_menu()
    assert menu["provider"] == "rkeeper"
    assert len(menu["items"]) >= 1

    stop = await client.fetch_stoplist()
    assert stop["stopped_ids"] == []


@pytest.mark.asyncio
async def test_rkeeper_adapter_registered(db_session):
    import app.services.pos.adapters  # noqa: F401

    assert "rkeeper" in ADAPTER_REGISTRY
    org = Organization(
        name="RK Org",
        slug="rk-org",
        pos_provider="rkeeper",
        meta_json={
            "rkeeper": {
                "server_url": "http://rk.local",
                "object_id": "42",
            }
        },
    )
    db_session.add(org)
    await db_session.flush()

    adapter = await get_pos_adapter(db_session, int(org.id))
    assert isinstance(adapter, RKeeperPOSAdapter)
    assert adapter.provider_slug == "rkeeper"


@pytest.mark.asyncio
async def test_resolve_org_rkeeper_credentials_from_meta(db_session):
    org = Organization(
        name="RK Creds",
        slug="rk-creds",
        meta_json={"rkeeper": {"server_url": "http://a", "object_id": "99"}},
    )
    db_session.add(org)
    await db_session.flush()

    creds = await resolve_org_rkeeper_credentials(db_session, int(org.id))
    assert creds is not None
    assert creds.server_url == "http://a"
    assert creds.object_id == "99"


@pytest.mark.asyncio
async def test_rkeeper_sync_menu_creates_items(db_session):
    org = Organization(
        name="RK Sync",
        slug="rk-sync",
        pos_provider="rkeeper",
        meta_json={"rkeeper": {"server_url": "http://rk", "object_id": "7"}},
    )
    db_session.add(org)
    await db_session.flush()

    adapter = RKeeperPOSAdapter()
    stats = await adapter.sync_menu(db_session, int(org.id))
    await db_session.flush()

    assert stats["created"] >= 1
    assert stats["provider"] == "rkeeper"

    rows = (await db_session.execute(select(MenuItem).where(MenuItem.organization_id == int(org.id)))).scalars().all()
    assert len(rows) >= 1


@pytest.mark.asyncio
async def test_rkeeper_sync_stoplist_applies_stops(db_session):
    org = Organization(
        name="RK Stop",
        slug="rk-stop",
        pos_provider="rkeeper",
        meta_json={"rkeeper": {"server_url": "http://rk", "object_id": "8"}},
    )
    db_session.add(org)
    await db_session.flush()
    item = MenuItem(
        organization_id=int(org.id),
        iiko_id="rk-8-demo-1",
        name="Demo",
        category="Main",
        price=100.0,
        is_available=True,
    )
    db_session.add(item)
    await db_session.flush()

    adapter = RKeeperPOSAdapter()
    with patch.object(
        RKeeperClient,
        "fetch_stoplist",
        new=AsyncMock(return_value={"stopped_ids": ["rk-8-demo-1"]}),
    ):
        stats = await adapter.sync_stoplist(db_session, int(org.id))

    await db_session.refresh(item)
    assert stats["stopped"] == 1
    assert item.is_available is False


@pytest.mark.asyncio
async def test_rkeeper_health_not_configured(db_session):
    org = Organization(name="RK No Creds", slug="rk-no", pos_provider="rkeeper")
    db_session.add(org)
    await db_session.flush()

    adapter = RKeeperPOSAdapter()
    out = await adapter.health(db_session, int(org.id))
    assert out["ok"] is False
    assert out["configured"] is False


@pytest.mark.asyncio
async def test_iiko_adapter_still_default(db_session):
    import app.services.pos.adapters  # noqa: F401
    from app.services.pos.adapters.iiko_adapter import IikoPOSAdapter

    org = Organization(name="IIKO Default", slug="iiko-def", pos_provider="iiko")
    db_session.add(org)
    await db_session.flush()

    adapter = await get_pos_adapter(db_session, int(org.id))
    assert isinstance(adapter, IikoPOSAdapter)


@pytest.mark.asyncio
async def test_rkeeper_send_order_stub(db_session):
    org = Organization(
        name="RK Order",
        slug="rk-order",
        pos_provider="rkeeper",
        meta_json={"rkeeper": {"server_url": "http://rk", "object_id": "1"}},
    )
    db_session.add(org)
    await db_session.flush()

    adapter = RKeeperPOSAdapter()
    out = await adapter.send_order(db_session, int(org.id), order_id=99)
    assert out["ok"] is False
    assert "not implemented" in out["error"]
