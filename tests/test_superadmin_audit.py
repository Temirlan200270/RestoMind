from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api.superadmin import (
    OrganizationCredentialsBody,
    OrganizationStatusBody,
    require_superadmin,
    superadmin_audit_log,
    superadmin_set_organization_status,
    superadmin_update_organization_credentials,
)
from app.core.passwords import hash_password
from app.db.models import Organization, StaffRole, StaffUser, SuperadminAuditLog
from tests.test_superadmin_onboarding import DummyRequest


def test_superadmin_template_has_new_credential_fields() -> None:
    html = Path("app/templates/superadmin.html").read_text(encoding="utf-8")

    assert "iiko api login" in html
    assert "iiko terminal group id" in html
    assert "telegram ops chat id" in html
    assert "/api/superadmin/audit" in html
    assert "loadAuditLog" in html


@pytest.mark.asyncio
async def test_superadmin_audit_log_written_on_status_change(db_session):
    org = Organization(name="Audit Org", is_active=True)
    db_session.add(org)
    await db_session.flush()
    actor = StaffUser(
        organization_id=int(org.id),
        email="super@restomind.test",
        password_hash=hash_password("secret123"),
        role=StaffRole.ADMIN.value,
        is_active=True,
        is_superadmin=True,
    )
    db_session.add(actor)
    await db_session.commit()

    out = await superadmin_set_organization_status(
        int(org.id),
        OrganizationStatusBody(is_active=False),
        actor,
        db_session,
    )
    assert out["is_active"] is False

    rows = (await db_session.execute(select(SuperadminAuditLog))).scalars().all()
    assert len(rows) == 1
    assert rows[0].action == "organization.status_change"
    assert rows[0].actor_email == actor.email
    assert rows[0].organization_id == int(org.id)


@pytest.mark.asyncio
async def test_superadmin_credentials_audit_masks_secret_values(db_session):
    org = Organization(name="Creds Org")
    db_session.add(org)
    await db_session.flush()
    actor = StaffUser(
        organization_id=int(org.id),
        email="super@restomind.test",
        password_hash=hash_password("secret123"),
        role=StaffRole.ADMIN.value,
        is_active=True,
        is_superadmin=True,
    )
    db_session.add(actor)
    await db_session.commit()

    await superadmin_update_organization_credentials(
        int(org.id),
        OrganizationCredentialsBody(
            iiko_api_login="secret-login",
            telegram_ops_chat_id="-100123",
        ),
        actor,
        db_session,
    )

    row = (await db_session.execute(select(SuperadminAuditLog))).scalar_one()
    assert row.action == "organization.credentials_update"
    assert row.details_json is not None
    assert "secret-login" not in str(row.details_json)
    assert "iiko_api_login" in row.details_json.get("changed_fields", [])
    assert "telegram_ops_chat_id" in row.details_json.get("changed_fields", [])


@pytest.mark.asyncio
async def test_superadmin_audit_endpoint_returns_entries(db_session):
    org = Organization(name="Listed Org")
    db_session.add(org)
    await db_session.flush()
    actor = StaffUser(
        organization_id=int(org.id),
        email="super@restomind.test",
        password_hash=hash_password("secret123"),
        role=StaffRole.ADMIN.value,
        is_active=True,
        is_superadmin=True,
    )
    db_session.add(actor)
    await db_session.flush()
    db_session.add(
        SuperadminAuditLog(
            actor_staff_user_id=int(actor.id),
            actor_email=actor.email,
            action="organization.create",
            target_type="organization",
            target_id=str(org.id),
            organization_id=int(org.id),
            details_json={"organization_name": org.name},
        ),
    )
    await db_session.commit()

    out = await superadmin_audit_log(_staff=actor, db=db_session)
    assert out["total"] == 1
    assert len(out["items"]) == 1
    assert out["items"][0]["action"] == "organization.create"


@pytest.mark.asyncio
async def test_superadmin_audit_endpoint_blocks_regular_staff(db_session):
    org = Organization(name="Blocked Audit Org")
    db_session.add(org)
    await db_session.flush()
    user = StaffUser(
        organization_id=int(org.id),
        email="admin@orga.test",
        password_hash=hash_password("secret123"),
        role=StaffRole.ADMIN.value,
        is_active=True,
        is_superadmin=False,
    )
    db_session.add(user)
    await db_session.commit()

    req = DummyRequest()
    req.session.update({"admin_ok": True, "staff_id": int(user.id), "organization_id": int(org.id)})
    with pytest.raises(HTTPException) as exc:
        await require_superadmin(req, db_session)
    assert exc.value.status_code == 403
