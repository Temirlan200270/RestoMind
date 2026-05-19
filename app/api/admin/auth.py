"""
Публичные эндпоинты входа в админку (без активной сессии): login, demo, заявка на подключение,
logout, me, переключение филиала.

Часть пакета `app.api.admin` (E0.1 раскол монолита). Регистрирует ``auth_router`` под
префиксом ``/admin/auth``; импортируется из ``app.api.admin.__init__`` для side-эффекта
декораторов (FastAPI узнаёт про эндпоинты только после импорта модуля).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.passwords import verify_password
from app.db.models import Organization, RegistrationRequest, StaffRole, StaffUser, Tenant
from app.db.session import get_db
from app.services.admin_tokens import create_admin_ws_token
from app.services.tenant_scope import (
    available_organizations_for_admin_session,
    branding_placeholder_e21,
    organization_id_allowed_for_admin_session,
    resolve_tenant_summary_for_session,
)

from .deps import _credentials_ok, _superadmin_credentials_ok, admin_org_from_session

logger = logging.getLogger(__name__)


auth_router = APIRouter(prefix="/admin/auth", tags=["Admin Auth"])


async def _resolve_network_info(
    db: AsyncSession,
    org_id: int,
) -> tuple[bool, list[dict]]:
    """Один проход — возвращает (is_network, network_orgs).

    Делает: 1 db.get(Org) + 1 db.get(Tenant) + 1 SELECT orgs (только при is_network).
    Заменяет два отдельных вызова, каждый из которых повторял те же два db.get().
    """
    try:
        org = await db.get(Organization, org_id)
        if org is None or org.tenant_id is None:
            return False, []
        tenant = await db.get(Tenant, int(org.tenant_id))
        if tenant is None or not bool(getattr(tenant, "is_network", False)):
            return False, []
        rows = await db.execute(
            select(Organization.id, Organization.name)
            .where(
                Organization.tenant_id == org.tenant_id,
                Organization.is_active.is_(True),
            )
            .order_by(Organization.name)
        )
        return True, [{"id": int(r.id), "name": r.name} for r in rows]
    except Exception:
        return False, []


async def _resolve_is_network(
    db: AsyncSession,
    _staff: StaffUser | None,
    org_id: int,
) -> bool:
    """Совместимость с тестами Sprint G: только флаг сети для org."""
    is_network, _ = await _resolve_network_info(db, int(org_id))
    return is_network


async def _resolve_network_orgs(
    db: AsyncSession,
    _staff: StaffUser | None,
    org_id: int,
) -> list[dict[str, int | str]]:
    """Совместимость с тестами Sprint G: список филиалов сети."""
    _, orgs = await _resolve_network_info(db, int(org_id))
    return orgs


class LoginBody(BaseModel):
    """Данные формы входа: email staff или legacy username + пароль."""

    username: str = ""
    email: str = ""
    password: str = ""


class SignupBody(BaseModel):
    """Self-serve регистрация ресторана и первого администратора."""

    restaurant_name: str = Field(..., min_length=2, max_length=255)
    network_name: str = Field(default="", max_length=255, description="Опционально: название сети/холдинга")
    email: str = Field(..., min_length=3, max_length=255)
    password: str = Field(..., min_length=8, max_length=128)


class RequestAccessBody(BaseModel):
    restaurant_name: str = Field(..., min_length=2, max_length=255)
    contact_name: str = Field(default="", max_length=255)
    phone: str = Field(default="", max_length=32)
    email: str = Field(default="", max_length=255)
    has_iiko: bool = False
    note: str = Field(default="", max_length=4000)


class SelectOrgBody(BaseModel):
    """Смена активного филиала в сессии (владелец сети / суперадмин)."""

    organization_id: int = Field(..., ge=1)


@auth_router.post("/login")
async def admin_login(request: Request, body: LoginBody, db: AsyncSession = Depends(get_db)) -> dict:
    """Staff по email или legacy ADMIN_USERNAME / ADMIN_PASSWORD."""
    email_try = (body.email or body.username).strip().lower()
    password = body.password
    request.session.clear()

    if email_try and "@" in email_try:
        staff = await db.scalar(
            select(StaffUser).where(
                StaffUser.email == email_try,
                StaffUser.is_active.is_(True),
            ),
        )
        if staff and verify_password(password, staff.password_hash):
            if not bool(staff.is_superadmin):
                org_login = await db.get(Organization, int(staff.organization_id))
                if org_login is not None and not bool(org_login.is_active):
                    raise HTTPException(
                        status_code=403,
                        detail="Подписка приостановлена. Свяжитесь с администратором.",
                    )
            request.session["admin_ok"] = True
            request.session["admin_user"] = staff.email
            request.session["staff_id"] = int(staff.id)
            request.session["organization_id"] = int(staff.organization_id)
            request.session.pop("is_demo", None)
            ws_token = create_admin_ws_token(
                organization_id=int(staff.organization_id),
                email=staff.email,
                staff_id=int(staff.id),
            )
            return {
                "ok": True,
                "username": staff.email,
                "organization_id": int(staff.organization_id),
                "staff_role": (staff.role or StaffRole.ADMIN.value).strip().lower(),
                "is_superadmin": bool(staff.is_superadmin),
                "ws_token": ws_token,
            }

    if _credentials_ok(body.username.strip(), password):
        # Legacy-вход (ADMIN_USERNAME/PASSWORD): привязываем сессию к реальной организации,
        # иначе при миграциях/демо-данных id может быть не 1 и админка будет "пустой".
        oid_db = await db.scalar(select(Organization.id).order_by(Organization.id.asc()).limit(1))
        oid = int(oid_db) if oid_db is not None else int(settings.default_organization_id)
        org_login = await db.get(Organization, oid)
        if org_login is not None and not bool(org_login.is_active):
            raise HTTPException(status_code=403, detail="Подписка приостановлена. Свяжитесь с администратором.")
        request.session["admin_ok"] = True
        request.session["admin_user"] = body.username.strip()
        request.session["organization_id"] = oid
        request.session["staff_id"] = None
        request.session.pop("is_demo", None)
        ws_token = create_admin_ws_token(
            organization_id=oid,
            email=body.username.strip(),
            staff_id=None,
        )
        return {
            "ok": True,
            "username": body.username.strip(),
            "organization_id": oid,
            "staff_role": StaffRole.ADMIN.value,
            "is_superadmin": False,
            "ws_token": ws_token,
        }

    if _superadmin_credentials_ok(body.username.strip(), password):
        # Legacy superadmin (SUPERADMIN_USERNAME/PASSWORD): без StaffUser, но с правами superadmin.
        # organization_id нужен для tenant-scope в UI и фильтрации WS-событий; стартуем с первой org.
        oid_db = await db.scalar(select(Organization.id).order_by(Organization.id.asc()).limit(1))
        oid = int(oid_db) if oid_db is not None else int(settings.default_organization_id)
        request.session["admin_ok"] = True
        request.session["admin_user"] = body.username.strip()
        request.session["organization_id"] = oid
        request.session["staff_id"] = None
        request.session["superadmin_ok"] = True
        request.session.pop("is_demo", None)
        ws_token = create_admin_ws_token(
            organization_id=oid,
            email=body.username.strip(),
            staff_id=None,
        )
        return {
            "ok": True,
            "username": body.username.strip(),
            "organization_id": oid,
            "staff_role": StaffRole.ADMIN.value,
            "is_superadmin": True,
            "ws_token": ws_token,
        }

    raise HTTPException(status_code=401, detail="Неверный логин или пароль")


@auth_router.post("/signup")
async def admin_signup_disabled(request: Request, body: SignupBody) -> dict:
    """Self-serve регистрация отключена: только заявка на модерацию."""
    _ = request
    _ = body
    raise HTTPException(status_code=410, detail="Регистрация теперь по заявке на подключение")


@auth_router.post("/demo-login")
async def admin_demo_login(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    """Гостевой вход в демо-организацию (read-only)."""
    request.session.clear()
    demo_org = await db.scalar(
        select(Organization).where(
            Organization.is_demo.is_(True),
        ),
    )
    if demo_org is None:
        demo_org = await db.scalar(select(Organization).where(Organization.slug == "demo"))
    if demo_org is None:
        raise HTTPException(status_code=503, detail="Демо временно недоступно")
    if not bool(demo_org.is_active):
        raise HTTPException(status_code=503, detail="Демо временно отключено")

    request.session["admin_ok"] = True
    request.session["admin_user"] = "demo-guest"
    request.session["organization_id"] = int(demo_org.id)
    request.session["staff_id"] = None
    request.session["is_demo"] = True

    ws_token = create_admin_ws_token(
        organization_id=int(demo_org.id),
        email="demo-guest",
        staff_id=None,
    )
    return {
        "ok": True,
        "username": "demo-guest",
        "organization_id": int(demo_org.id),
        "staff_role": StaffRole.OPERATOR.value,
        "is_superadmin": False,
        "ws_token": ws_token,
    }


@auth_router.post("/request-access")
async def admin_request_access(body: RequestAccessBody, db: AsyncSession = Depends(get_db)) -> dict:
    """Создать заявку на модерацию подключения ресторана."""
    req = RegistrationRequest(
        restaurant_name=(body.restaurant_name or "").strip(),
        contact_name=(body.contact_name or "").strip(),
        phone=(body.phone or "").strip(),
        email=(body.email or "").strip().lower(),
        has_iiko=bool(body.has_iiko),
        note=(body.note or "").strip(),
        status="pending",
    )
    db.add(req)
    await db.commit()
    await db.refresh(req)

    token = (settings.telegram_bot_token or "").strip()
    chat_id = (settings.superadmin_telegram_chat_id or settings.telegram_admin_chat_id or "").strip()
    if token and chat_id:
        payload = {
            "chat_id": chat_id,
            "text": (
                "🆕 <b>Новая заявка на подключение</b>\n"
                f"Ресторан: <code>{req.restaurant_name}</code>\n"
                f"Контакт: <code>{req.contact_name or '—'}</code>\n"
                f"Телефон: <code>{req.phone or '—'}</code>\n"
                f"Email: <code>{req.email or '—'}</code>\n"
                f"iiko: <code>{'да' if req.has_iiko else 'нет'}</code>"
            ),
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(f"https://api.telegram.org/bot{token}/sendMessage", json=payload)
                resp.raise_for_status()
        except Exception:
            logger.warning("request-access: telegram notify failed", exc_info=True)

    return {"ok": True, "request_id": int(req.id)}


@auth_router.post("/logout")
async def admin_logout(request: Request) -> dict:
    """Завершить сессию."""
    request.session.clear()
    return {"ok": True}


# ── E2.1 ── Мультифилиальность: расширение GET /auth/me и POST /auth/select-org


async def _admin_auth_me_payload(request: Request, db: AsyncSession) -> dict[str, Any]:
    """Тело ответа GET /auth/me и успешного POST /auth/select-org (контракт PARALLEL_AI_PLAN §4)."""
    user = request.session.get("admin_user") or settings.admin_username
    oid = admin_org_from_session(request)
    # Если в сессии лежит несуществующий organization_id (после миграций/ресетов БД),
    # переведём админку на первую доступную организацию.
    exists_oid = await db.scalar(select(Organization.id).where(Organization.id == int(oid)))
    if exists_oid is None:
        oid_db = await db.scalar(select(Organization.id).order_by(Organization.id.asc()).limit(1))
        if oid_db is not None:
            oid = int(oid_db)
            request.session["organization_id"] = oid
    sid = request.session.get("staff_id")
    staff_role = StaffRole.ADMIN.value
    is_demo = bool(request.session.get("is_demo"))
    is_superadmin = bool(request.session.get("superadmin_ok"))
    staff_me: StaffUser | None = None
    if sid is not None:
        staff_me = await db.get(StaffUser, int(sid))
        if staff_me is not None:
            staff_role = (staff_me.role or StaffRole.ADMIN.value).strip().lower()
            is_superadmin = bool(staff_me.is_superadmin)
    if not is_superadmin:
        org_me = await db.get(Organization, int(oid))
        if org_me is not None and not bool(org_me.is_active):
            raise HTTPException(status_code=403, detail="Подписка приостановлена. Свяжитесь с администратором.")

    available = await available_organizations_for_admin_session(
        db,
        staff=staff_me,
        is_superadmin=is_superadmin,
        is_demo=is_demo,
        session_organization_id=int(oid),
    )
    tenant_payload = await resolve_tenant_summary_for_session(
        db,
        staff=staff_me,
        active_organization_id=int(oid),
    )
    branding = branding_placeholder_e21()

    staff_id_val: int | None = int(sid) if sid is not None else None
    email_out = str(user)
    if staff_me is not None:
        email_out = str(staff_me.email)

    result = {
        "authenticated": True,
        "username": user,
        "organization_id": int(oid),
        "staff_role": staff_role,
        "is_demo": is_demo,
        "is_superadmin": is_superadmin,
        "ws_token": create_admin_ws_token(
            organization_id=int(oid),
            email=email_out,
            staff_id=staff_id_val,
        ),
        "id": staff_id_val,
        "email": email_out,
        "role": staff_role,
        "tenant_owner_id": int(staff_me.tenant_owner_id)
        if staff_me is not None and staff_me.tenant_owner_id is not None
        else None,
        "active_organization_id": int(oid),
        "available_organizations": available,
        "tenant": tenant_payload,
        "branding": branding,
    }
    is_net, net_orgs = await _resolve_network_info(db, int(oid))
    result["is_network"] = is_net
    result["network_orgs"] = net_orgs
    return result


@auth_router.get("/me")
async def admin_me(request: Request, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Проверка сессии и перевыпуск ws_token для переподключения."""
    if not request.session.get("admin_ok"):
        return {"authenticated": False}
    return await _admin_auth_me_payload(request, db)


@auth_router.post("/select-org")
async def admin_select_org(
    request: Request,
    body: SelectOrgBody,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Переключить активный филиал в cookie-сессии (проверка доступа по tenant / роли)."""
    if not request.session.get("admin_ok"):
        raise HTTPException(status_code=401, detail="Не авторизован")

    sid = request.session.get("staff_id")
    staff_me: StaffUser | None = None
    is_demo = bool(request.session.get("is_demo"))
    is_superadmin = bool(request.session.get("superadmin_ok"))
    if sid is not None:
        staff_me = await db.get(StaffUser, int(sid))
        if staff_me is not None:
            is_superadmin = bool(staff_me.is_superadmin)

    cur_oid = admin_org_from_session(request)
    ok_switch = await organization_id_allowed_for_admin_session(
        db,
        staff=staff_me,
        is_superadmin=is_superadmin,
        is_demo=is_demo,
        target_organization_id=int(body.organization_id),
        session_organization_id=int(cur_oid),
    )
    if not ok_switch:
        raise HTTPException(status_code=403, detail="Филиал недоступен для этой учётной записи")

    target = await db.get(Organization, int(body.organization_id))
    if target is None:
        raise HTTPException(status_code=404, detail="Организация не найдена")
    if not is_superadmin and not bool(target.is_active):
        raise HTTPException(status_code=403, detail="Подписка приостановлена. Свяжитесь с администратором.")

    request.session["organization_id"] = int(body.organization_id)
    return await _admin_auth_me_payload(request, db)
