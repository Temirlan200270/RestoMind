from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import webhooks
from app.api.admin import LoginBody, admin_demo_login, admin_login, require_admin_session_active
from app.api.superadmin import RegistrationApproveBody, require_superadmin, superadmin_approve_registration_request
from app.core.passwords import hash_password
from app.db.models import Base, Organization, RegistrationRequest, StaffRole, StaffUser


class DummyRequest:
    def __init__(self, method: str = "GET") -> None:
        self.method = method
        self.session: dict = {}


def test_superadmin_passwords_use_acknowledged_modal() -> None:
    html = Path("app/templates/superadmin.html").read_text(encoding="utf-8")

    assert "passwordModal.open" in html
    assert "showPasswordModal" in html
    assert "copyPasswordModal" in html
    assert ":disabled=\"!passwordModal.copied\"" in html
    assert "generated_password ? `" not in html
    assert "Новый пароль: ${out.new_password}" not in html


@pytest.mark.asyncio
async def test_require_superadmin_blocks_regular_staff(db_session):
    org = Organization(name="Org A")
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


@pytest.mark.asyncio
async def test_approve_registration_request_creates_org_and_staff(db_session):
    owner_org = Organization(name="Owner Org")
    db_session.add(owner_org)
    await db_session.flush()
    super_staff = StaffUser(
        organization_id=int(owner_org.id),
        email="owner@restomind.test",
        password_hash=hash_password("supersecret123"),
        role=StaffRole.ADMIN.value,
        is_active=True,
        is_superadmin=True,
    )
    db_session.add(super_staff)
    req = RegistrationRequest(
        restaurant_name="Cafe Bravo",
        contact_name="Aigerim",
        phone="+77770001122",
        email="owner@cafebravo.test",
        status="pending",
    )
    db_session.add(req)
    await db_session.commit()
    await db_session.refresh(req)

    out = await superadmin_approve_registration_request(
        int(req.id),
        RegistrationApproveBody(),
        super_staff,
        db_session,
    )

    assert out["ok"] is True
    assert out["organization_id"] > 0
    approved = await db_session.get(RegistrationRequest, int(req.id))
    assert approved is not None
    assert approved.status == "approved"
    staff = await db_session.get(StaffUser, int(out["staff_id"]))
    assert staff is not None
    assert int(staff.organization_id) == int(out["organization_id"])


@pytest.mark.asyncio
async def test_login_blocked_for_inactive_organization(db_session):
    org = Organization(name="Blocked Cafe", is_active=False)
    db_session.add(org)
    await db_session.flush()
    staff = StaffUser(
        organization_id=int(org.id),
        email="blocked@cafe.test",
        password_hash=hash_password("pass12345"),
        role=StaffRole.ADMIN.value,
        is_active=True,
        is_superadmin=False,
    )
    db_session.add(staff)
    await db_session.commit()

    req = DummyRequest(method="POST")
    with pytest.raises(HTTPException) as exc:
        await admin_login(req, LoginBody(email=staff.email, password="pass12345"), db_session)
    assert exc.value.status_code == 403
    assert "Подписка приостановлена" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_demo_login_sets_readonly_guard(db_session):
    demo_org = Organization(name="Demo Cafe", slug="demo", is_demo=True, is_active=True)
    db_session.add(demo_org)
    await db_session.commit()

    req = DummyRequest(method="POST")
    out = await admin_demo_login(req, db_session)
    assert out["ok"] is True
    assert req.session.get("is_demo") is True

    req.method = "POST"
    with pytest.raises(HTTPException) as exc:
        await require_admin_session_active(req, db_session)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_process_with_retry_skips_inactive_org(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with session_factory() as db:
        org = Organization(name="WA Blocked", is_active=False, whatsapp_phone_number_id="pn_123")
        db.add(org)
        await db.commit()

    async def fake_resolve(_db, _value):
        return int(org.id)

    called = False

    async def fake_process_message(*args, **kwargs):
        nonlocal called
        called = True
        return None

    monkeypatch.setattr(webhooks, "async_session_factory", session_factory)
    monkeypatch.setattr(webhooks, "organization_id_for_whatsapp_value", fake_resolve)
    monkeypatch.setattr(webhooks, "process_message", fake_process_message)

    await webhooks.process_with_retry("77001112233", "hello", webhook_value={"metadata": {"phone_number_id": "pn_123"}})
    assert called is False
    await engine.dispose()
