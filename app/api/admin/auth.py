"""
Публичные эндпоинты входа в админку (без активной сессии): login, demo, заявка на подключение,
logout, me, переключение филиала.

Часть пакета `app.api.admin` (E0.1 раскол монолита). Регистрирует ``auth_router`` под
префиксом ``/admin/auth``; импортируется из ``app.api.admin.__init__`` для side-эффекта
декораторов (FastAPI узнаёт про эндпоинты только после импорта модуля).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
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
from app.services.billing_guard import (
    billing_suspended_http_exception,
    load_tenant_for_organization,
    tenant_is_billing_suspended,
)
from app.services.db_pool_errors import POOL_EXHAUSTED_USER_MESSAGE, is_postgres_pool_exhausted
from app.services.demo_login_cache import resolve_demo_org_id_from_settings, set_cached_demo_org_id
from app.services.tenant_scope import (
    allowed_location_ids_for_staff,
    available_organizations_for_admin_session,
    ensure_default_location,
    list_locations_for_org,
    organization_id_allowed_for_admin_session,
    branding_empty_payload,
)

from .deps import _credentials_ok, _superadmin_credentials_ok, admin_org_from_session

logger = logging.getLogger(__name__)


auth_router = APIRouter(prefix="/admin/auth", tags=["Admin Auth"])


def _public_staff_role(role: str | None) -> str:
    """Collapse legacy manager/admin into the owner role exposed to the UI."""
    r = (role or StaffRole.ADMIN.value).strip().lower()
    return StaffRole.OPERATOR.value if r == StaffRole.OPERATOR.value else StaffRole.ADMIN.value


async def _resolve_network_info(
    db: AsyncSession,
    org_id: int,
    *,
    org: Organization | None = None,
) -> tuple[bool, list[dict]]:
    """Один проход — возвращает (is_network, network_orgs).

    Делает: 1 db.get(Org) + 1 db.get(Tenant) + 1 SELECT orgs (только при is_network).
    Заменяет два отдельных вызова, каждый из которых повторял те же два db.get().
    """
    try:
        if org is None:
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


class TourCompleteBody(BaseModel):
    """P15: завершение coach-marks в админке (синхронизация с StaffUser.meta_json)."""

    completed_at: str | None = Field(
        default=None,
        description="ISO timestamp; если пусто — сервер ставит now() UTC",
    )


def _staff_tour_completed_at(staff: StaffUser | None) -> str | None:
    if staff is None:
        return None
    meta = staff.meta_json if isinstance(staff.meta_json, dict) else {}
    raw = meta.get("tour_completed_at")
    return str(raw).strip() if raw else None


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
                tenant_st = await load_tenant_for_organization(db, int(staff.organization_id))
                if tenant_is_billing_suspended(tenant_st):
                    raise billing_suspended_http_exception()
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
            public_role = _public_staff_role(staff.role)
            return {
                "ok": True,
                "username": staff.email,
                "organization_id": int(staff.organization_id),
                "staff_role": public_role,
                "role": public_role,
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
        tenant_st = await load_tenant_for_organization(db, oid)
        if tenant_is_billing_suspended(tenant_st):
            raise billing_suspended_http_exception()
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
            "role": StaffRole.ADMIN.value,
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
            "role": StaffRole.ADMIN.value,
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
    try:
        return await establish_demo_session(request, db)
    except HTTPException:
        raise
    except Exception as exc:
        if is_postgres_pool_exhausted(exc):
            logger.warning("demo-login pool exhausted: %s", exc)
            raise HTTPException(status_code=503, detail=POOL_EXHAUSTED_USER_MESSAGE) from exc
        raise


async def establish_demo_session(request: Request, db: AsyncSession) -> dict:
    """Create read-only demo admin session (POST demo-login + GET /demo)."""
    request.session.clear()

    cached_org_id = resolve_demo_org_id_from_settings()
    if cached_org_id is not None:
        return _demo_login_session_response(request, cached_org_id)

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

    tenant_st = await load_tenant_for_organization(db, int(demo_org.id))
    if tenant_is_billing_suspended(tenant_st):
        raise HTTPException(status_code=503, detail="Демо временно недоступно")

    set_cached_demo_org_id(int(demo_org.id))
    return _demo_login_session_response(request, int(demo_org.id))


def _demo_login_session_response(request: Request, organization_id: int) -> dict:
    request.session["admin_ok"] = True
    request.session["admin_user"] = "demo-guest"
    request.session["organization_id"] = int(organization_id)
    request.session["staff_id"] = None
    request.session["is_demo"] = True

    ws_token = create_admin_ws_token(
        organization_id=int(organization_id),
        email="demo-guest",
        staff_id=None,
    )
    return {
        "ok": True,
        "username": "demo-guest",
        "organization_id": int(organization_id),
        "staff_role": StaffRole.OPERATOR.value,
        "role": StaffRole.OPERATOR.value,
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
            staff_role = _public_staff_role(staff_me.role)
            is_superadmin = bool(staff_me.is_superadmin)
    org_me = await db.get(Organization, int(oid))
    if not is_superadmin:
        if org_me is not None and not bool(org_me.is_active):
            raise HTTPException(status_code=403, detail="Подписка приостановлена. Свяжитесь с администратором.")

        tenant_me = None
        if org_me is not None and org_me.tenant_id is not None:
            tenant_me = await db.get(Tenant, int(org_me.tenant_id))
        if tenant_is_billing_suspended(tenant_me):
            raise billing_suspended_http_exception()

    available = await available_organizations_for_admin_session(
        db,
        staff=staff_me,
        is_superadmin=is_superadmin,
        is_demo=is_demo,
        session_organization_id=int(oid),
    )
    all_locations = await list_locations_for_org(db, int(oid))
    if not all_locations:
        all_locations = [await ensure_default_location(db, int(oid))]
    allowed_location_ids = await allowed_location_ids_for_staff(
        db,
        staff=staff_me,
        org_id=int(oid),
        is_superadmin=is_superadmin,
        locations=all_locations,
    )
    available_locations = [
        {"id": int(loc.id), "name": str(loc.name), "slug": str(loc.slug or "")}
        for loc in all_locations
        if allowed_location_ids is None or int(loc.id) in allowed_location_ids
    ]
    tenant_id: int | None = None
    if staff_me is not None and staff_me.tenant_owner_id is not None:
        tenant_id = int(staff_me.tenant_owner_id)
    elif org_me is not None and org_me.tenant_id is not None:
        tenant_id = int(org_me.tenant_id)
    tenant_payload = None
    branding = branding_empty_payload()
    if tenant_id is not None:
        tenant_row = await db.get(Tenant, tenant_id)
        if tenant_row is not None:
            tenant_payload = {
                "id": int(tenant_row.id),
                "name": str(tenant_row.name),
                "plan": str(tenant_row.plan),
                "plan_status": str(tenant_row.plan_status or "active"),
            }
            branding = {
                "brand_name": str(tenant_row.brand_name) if tenant_row.brand_name else None,
                "brand_color_hex": str(tenant_row.brand_color_hex) if tenant_row.brand_color_hex else None,
                "brand_logo_url": str(tenant_row.brand_logo_url) if tenant_row.brand_logo_url else None,
            }

    staff_id_val: int | None = int(sid) if sid is not None else None
    email_out = str(user)
    if staff_me is not None:
        email_out = str(staff_me.email)

    meta = org_me.meta_json if org_me is not None and isinstance(org_me.meta_json, dict) else {}
    hub_default = bool(settings.executive_hub_default_enabled)
    if meta.get("executive_hub_default_enabled") is False:
        hub_default = False
    elif meta.get("executive_hub_default_enabled") is True:
        hub_default = True

    result = {
        "authenticated": True,
        "username": user,
        "organization_id": int(oid),
        "staff_role": staff_role,
        "executive_hub_default_enabled": hub_default,
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
        "available_locations": available_locations,
        "tenant": tenant_payload,
        "branding": branding,
        "billing_blocked": False,
        "tour_completed_at": _staff_tour_completed_at(staff_me),
    }
    is_net, net_orgs = await _resolve_network_info(db, int(oid), org=org_me)
    result["is_network"] = is_net
    result["network_orgs"] = net_orgs
    return result


@auth_router.post("/tour-complete")
async def admin_tour_complete(
    request: Request,
    body: TourCompleteBody,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Сохранить завершение P15 coach-marks в StaffUser.meta_json.tour_completed_at."""
    if not request.session.get("admin_ok"):
        raise HTTPException(status_code=401, detail="Не авторизован")
    sid = request.session.get("staff_id")
    if sid is None:
        return {"ok": True, "tour_completed_at": None, "persisted": False}
    staff = await db.get(StaffUser, int(sid))
    if staff is None or not staff.is_active:
        raise HTTPException(status_code=403, detail="Недостаточно прав")
    completed_raw = (body.completed_at or "").strip()
    if completed_raw:
        completed_at = completed_raw
    else:
        completed_at = datetime.now(tz=timezone.utc).isoformat()
    meta = dict(staff.meta_json) if isinstance(staff.meta_json, dict) else {}
    meta["tour_completed_at"] = completed_at
    staff.meta_json = meta
    await db.commit()
    return {"ok": True, "tour_completed_at": completed_at, "persisted": True}


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

    if not is_superadmin:
        tenant_t = await load_tenant_for_organization(db, int(body.organization_id))
        if tenant_is_billing_suspended(tenant_t):
            raise billing_suspended_http_exception()

    request.session["organization_id"] = int(body.organization_id)
    return await _admin_auth_me_payload(request, db)
