"""iiko customer import for marketing segments."""

import pytest

from app.services.iiko_customer_sync import collect_phones_from_iiko_deliveries, sync_iiko_customers_for_org


def test_collect_phones_from_iiko_deliveries_nested_shape() -> None:
    payload = {
        "ordersByOrganizations": [
            {
                "organizationId": "org-1",
                "orders": [
                    {"order": {"customer": {"phone": "+77001234567"}}},
                    {"order": {"phone": "87001112233"}},
                ],
            }
        ]
    }
    phones = collect_phones_from_iiko_deliveries(payload)
    assert "+77001234567" in phones
    assert "+87001112233" in phones


@pytest.mark.asyncio
async def test_sync_iiko_customers_creates_users(db_session, monkeypatch) -> None:
    from app.db.models import Organization, User
    from sqlalchemy import select

    org = Organization(name="Sync Test", slug="sync-test", timezone="Asia/Almaty")
    db_session.add(org)
    await db_session.flush()

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def fetch_deliveries_by_date_and_status(self, **kwargs):
            return {
                "ordersByOrganizations": [
                    {"orders": [{"order": {"customer": {"phone": "+77009998877"}}}]},
                ]
            }

    monkeypatch.setattr("app.services.iiko_customer_sync.IikoClient", FakeClient)
    async def fake_resolve(_db, _oid):
        return type("C", (), {
            "api_login": "login",
            "iiko_organization_id": "uuid",
            "terminal_group_id": "",
        })()

    monkeypatch.setattr(
        "app.services.iiko_customer_sync.resolve_org_iiko_credentials",
        fake_resolve,
    )

    result = await sync_iiko_customers_for_org(db_session, int(org.id), days=30)
    assert result["ok"] is True
    assert result["users_created"] == 1

    user = await db_session.scalar(
        select(User).where(User.organization_id == org.id, User.phone == "+77009998877"),
    )
    assert user is not None


@pytest.mark.asyncio
async def test_sync_iiko_customers_without_credentials(db_session, monkeypatch) -> None:
    from app.db.models import Organization

    async def fake_resolve(_db, _oid):
        return None

    monkeypatch.setattr(
        "app.services.iiko_customer_sync.resolve_org_iiko_credentials",
        fake_resolve,
    )

    org = Organization(name="No iiko", slug="no-iiko", timezone="Asia/Almaty")
    db_session.add(org)
    await db_session.flush()

    result = await sync_iiko_customers_for_org(db_session, int(org.id))
    assert result["ok"] is False
    assert result["error"] == "iiko_not_configured"


@pytest.mark.asyncio
async def test_sync_iiko_customers_http_smoke(asgi_memory_client, monkeypatch) -> None:
    """POST /marketing/sync-iiko-customers responds without 5xx when iiko is not configured."""
    from app.core.passwords import hash_password
    from app.db.models import Organization, StaffUser

    client, session_factory = asgi_memory_client

    async with session_factory() as db:
        org = Organization(name="Mkt Sync Org", slug="mkt-sync-org", timezone="Asia/Almaty")
        db.add(org)
        await db.flush()
        db.add(
            StaffUser(
                organization_id=org.id,
                email="mkt-admin@test.kz",
                password_hash=hash_password("secret123"),
                role="admin",
                is_active=True,
            ),
        )
        await db.commit()

    async def fake_resolve(_db, _oid):
        return None

    monkeypatch.setattr(
        "app.services.iiko_customer_sync.resolve_org_iiko_credentials",
        fake_resolve,
    )

    login = await client.post(
        "/api/admin/auth/login",
        json={"username": "mkt-admin@test.kz", "password": "secret123"},
    )
    assert login.status_code == 200

    res = await client.post("/api/admin/marketing/sync-iiko-customers")
    assert res.status_code == 400
    body = res.json()
    assert "iiko" in str(body.get("detail", "")).lower()
