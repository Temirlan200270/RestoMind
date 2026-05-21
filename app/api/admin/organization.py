"""Organization, staff, and payment-config admin API (E0.1 split from _monolith.py)."""

from __future__ import annotations

import logging
import secrets
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.passwords import hash_password
from app.db.models import Location, Organization, OrganizationPaymentConfig, StaffRole, StaffUser
from app.db.session import get_db
from app.services.tenant_scope import list_locations_for_org, tenant_org_ids_for_staff_home
from app.services.time_context import check_operational_status, parse_schedule_json
from app.services.timezones import normalize_timezone_name
from .deps import (
    admin_org_from_session,
    require_admin_session_active,
    require_staff_admin,
)
from app.services.org_iiko_office import (
    apply_iiko_office_config_patch,
    iiko_office_config_public,
)
from app.services.secrets_crypto import fernet_or_none

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/admin",
    tags=["Organization"],
    dependencies=[Depends(require_admin_session_active)],
)


# ─── Schemas ──────────────────────────────────────────────


class OrganizationPrefsPatchBody(BaseModel):
    """Настройки филиала (без секретов)."""

    prepayment_enforced: bool | None = Field(
        default=None,
        description="False — не требовать предоплату по порогу; оператор подтверждает оплату вручную",
    )
    auto_send_to_iiko_after_payment: bool | None = Field(
        default=None,
        description="После оплаты (вебхук) автоматически подтвердить заказ и отправить в iiko",
    )


class IikoOfficeConfigPatchBody(BaseModel):
    """Настройки iiko Office (складские остатки для SupplyMind)."""

    host: str | None = Field(default=None, max_length=512)
    login: str | None = Field(default=None, max_length=255)
    password: str | None = Field(
        default=None,
        max_length=255,
        description="Пустая строка — не менять; иначе сохранить (зашифровать при наличии Fernet)",
    )
    store_id: str | None = Field(default=None, max_length=120)
    department_id: str | None = Field(default=None, max_length=120)
    location_id: int | None = Field(
        default=None,
        ge=1,
        description="RestoMind location_id для store_id (одна точка)",
    )
    store_location_map: dict[str, int] | None = Field(
        default=None,
        description="Маппинг store_id (iiko) → location_id для сети с несколькими точками",
    )


class OrganizationProfilePatchBody(BaseModel):
    """Профиль ресторана/филиала (для UI, без секретов)."""

    name: str | None = Field(default=None, min_length=2, max_length=255)
    timezone: str | None = Field(default=None, max_length=64)
    currency: str | None = Field(default=None, max_length=8)
    whatsapp_phone_number_id: str | None = Field(default=None, max_length=100)
    telegram_ops_chat_id: str | None = Field(
        default=None,
        max_length=32,
        description="Telegram chat_id группы персонала для SOS-алертов (если пусто — TELEGRAM_ADMIN_CHAT_ID из Render)",
    )
    schedule_json: dict[str, object] | None = Field(
        default=None,
        description="Структурированный график работы по дням недели",
    )
    prepayment_legal_text: str | None = Field(
        default=None,
        max_length=8000,
        description="Доп. дисклеймер при предоплате по порогу суммы (показывается гостю в WhatsApp)",
    )
    review_url_2gis: str | None = Field(
        default=None,
        max_length=512,
        description="Ссылка на 2GIS для отзывов",
    )
    review_url_google: str | None = Field(
        default=None,
        max_length=512,
        description="Ссылка на Google Maps/Reviews для GuestCare External (meta_json.review_url_google)",
    )


def _org_meta_dict(org: Organization) -> dict:
    return dict(org.meta_json) if isinstance(org.meta_json, dict) else {}


def _review_url_google_from_org(org: Organization) -> str:
    meta = _org_meta_dict(org)
    return str(meta.get("review_url_google") or meta.get("guestcare_google_url") or "").strip()


_VALID_PAYMENT_PROVIDERS = frozenset({"freedom_pay", "kaspi", "cloudpayments"})


class PaymentProviderToggleBody(BaseModel):
    provider: str = Field(..., min_length=2, max_length=64)
    enabled: bool


class ForceCloseBody(BaseModel):
    minutes: int = Field(..., ge=0, le=1440, description="0 = снять закрытие; >0 = закрыть на N минут")
    reason: str = Field(default="", max_length=255, description="Причина закрытия")


class StaffRoleMetadataBody(BaseModel):
    """Доп. метаданные роли для StaffMind / UI (хранятся в meta_json.role_metadata)."""

    title: str = Field(default="", max_length=120)
    department: str = Field(default="", max_length=120)


class StaffCreateBody(BaseModel):
    email: str = Field(..., min_length=3, max_length=255)
    role: str = Field(default=StaffRole.OPERATOR.value, description="admin | manager | operator")
    password: str = Field(default="", max_length=128, description="Опционально: если пусто — сгенерируем временный")
    assigned_org_ids: list[int] | None = Field(
        default=None,
        description="Филиалы для manager (подмножество сети); без списка — только домашний",
    )
    assigned_location_ids: list[int] | None = Field(
        default=None,
        description="Точки/залы для operator/manager (подмножество locations org)",
    )
    role_metadata: StaffRoleMetadataBody | None = Field(
        default=None,
        description="Отображаемая должность/отдел для StaffMind",
    )


class StaffUpdateBody(BaseModel):
    role: str | None = Field(default=None, description="admin | manager | operator")
    is_active: bool | None = None
    assigned_org_ids: list[int] | None = None
    assigned_location_ids: list[int] | None = None
    role_metadata: StaffRoleMetadataBody | None = None


def _staff_role_metadata_dict(meta: dict) -> dict[str, str]:
    raw = meta.get("role_metadata")
    if not isinstance(raw, dict):
        return {"title": "", "department": ""}
    return {
        "title": str(raw.get("title") or "").strip()[:120],
        "department": str(raw.get("department") or "").strip()[:120],
    }


def _staff_user_public(u: StaffUser) -> dict:
    meta = u.meta_json if isinstance(u.meta_json, dict) else {}
    assigned_orgs = meta.get("assigned_org_ids") if isinstance(meta.get("assigned_org_ids"), list) else None
    assigned_locs = meta.get("assigned_location_ids") if isinstance(meta.get("assigned_location_ids"), list) else None
    return {
        "id": int(u.id),
        "email": u.email,
        "role": u.role,
        "is_active": bool(u.is_active),
        "assigned_org_ids": assigned_orgs,
        "assigned_location_ids": assigned_locs,
        "role_metadata": _staff_role_metadata_dict(meta),
        "created_at": u.created_at.isoformat() if u.created_at else None,
    }


async def _valid_location_ids_for_org(db: AsyncSession, org_id: int) -> set[int]:
    locs = await list_locations_for_org(db, int(org_id))
    return {int(loc.id) for loc in locs}


def _merge_staff_meta(
    existing: dict | None,
    *,
    assigned_org_ids: list[int] | None = None,
    assigned_location_ids: list[int] | None = None,
    role_metadata: StaffRoleMetadataBody | None = None,
    clear_assigned_orgs: bool = False,
    clear_assigned_locations: bool = False,
) -> dict:
    meta = dict(existing) if isinstance(existing, dict) else {}
    if clear_assigned_orgs:
        meta.pop("assigned_org_ids", None)
    elif assigned_org_ids is not None:
        meta["assigned_org_ids"] = assigned_org_ids
    if clear_assigned_locations:
        meta.pop("assigned_location_ids", None)
    elif assigned_location_ids is not None:
        meta["assigned_location_ids"] = assigned_location_ids
    if role_metadata is not None:
        meta["role_metadata"] = {
            "title": (role_metadata.title or "").strip()[:120],
            "department": (role_metadata.department or "").strip()[:120],
        }
    return meta


_SUPPORTED_PAYMENT_PROVIDERS = {"freedom_pay", "kaspi", "cloudpayments"}


class PaymentConfigUpsertBody(BaseModel):
    provider: str = Field(..., description="freedom_pay | kaspi | cloudpayments")
    is_enabled: bool = Field(default=False)
    is_primary: bool = Field(default=False)
    merchant_id: str = Field(default="", max_length=200)
    api_key: str = Field(default="", description="Будет зашифрован. Пустая строка = не менять.")
    secret_key: str = Field(default="", description="Будет зашифрован. Пустая строка = не менять.")
    public_key: str = Field(default="", description="Будет зашифрован. Пустая строка = не менять.")
    environment: str = Field(default="production", description="production | test")
    callback_url: str = Field(default="", max_length=512)
    success_url: str = Field(default="", max_length=512)


# ─── Routes ──────────────────────────────────────────────


@router.get("/organization/prefs")
async def get_organization_prefs(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    org_id = admin_org_from_session(request)
    org = await db.get(Organization, org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    return {
        "id": org.id,
        "name": org.name,
        "organization_id": org.id,
        "prepayment_enforced": bool(getattr(org, "prepayment_enforced", True)),
        "auto_send_to_iiko_after_payment": bool(getattr(org, "auto_send_to_iiko_after_payment", False)),
    }


@router.patch("/organization/payment-providers")
async def patch_payment_provider_toggle(
    request: Request,
    body: PaymentProviderToggleBody,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Включить / отключить платёжный провайдер для этого заведения."""
    slug = (body.provider or "").strip().lower()
    if slug not in _VALID_PAYMENT_PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unknown provider '{slug}'")
    org_id = admin_org_from_session(request)
    org = await db.get(Organization, org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    pcj: dict = dict(org.payment_config_json) if isinstance(org.payment_config_json, dict) else {}
    prov_cfg: dict = dict(pcj.get(slug, {}))
    prov_cfg["enabled"] = bool(body.enabled)
    pcj[slug] = prov_cfg
    org.payment_config_json = pcj
    await db.commit()
    return {"ok": True, "provider": slug, "enabled": bool(body.enabled)}


@router.patch("/organization/prefs")
async def patch_organization_prefs(
    request: Request,
    body: OrganizationPrefsPatchBody,
    db: AsyncSession = Depends(get_db),
) -> dict:
    org_id = admin_org_from_session(request)
    org = await db.get(Organization, org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    if body.prepayment_enforced is not None:
        org.prepayment_enforced = bool(body.prepayment_enforced)
    if body.auto_send_to_iiko_after_payment is not None:
        org.auto_send_to_iiko_after_payment = bool(body.auto_send_to_iiko_after_payment)
    await db.commit()
    await db.refresh(org)
    return {
        "ok": True,
        "prepayment_enforced": bool(getattr(org, "prepayment_enforced", True)),
        "auto_send_to_iiko_after_payment": bool(getattr(org, "auto_send_to_iiko_after_payment", False)),
    }


@router.get("/organization/profile")
async def get_organization_profile(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    """Данные «Мой ресторан» для текущей админ-сессии."""
    org_id = admin_org_from_session(request)
    org = await db.get(Organization, org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    fc_until = getattr(org, "force_closed_until", None)
    fc_reason = getattr(org, "force_closed_reason", "") or ""
    op = check_operational_status(
        org.timezone,
        getattr(org, "schedule_json", None),
        force_closed_until=fc_until,
        force_closed_reason=fc_reason,
    )
    return {
        "id": int(org.id),
        "organization_id": int(org.id),
        "name": org.name,
        "timezone": org.timezone,
        "currency": org.currency,
        "whatsapp_phone_number_id": (org.whatsapp_phone_number_id or "").strip(),
        "telegram_ops_chat_id": (getattr(org, "telegram_ops_chat_id", None) or "").strip(),
        "prepayment_legal_text": (getattr(org, "prepayment_legal_text", None) or "").strip(),
        "review_url_2gis": (getattr(org, "review_url_2gis", None) or "").strip(),
        "review_url_google": _review_url_google_from_org(org),
        "schedule_json": parse_schedule_json(getattr(org, "schedule_json", None)).model_dump(mode="json"),
        "operational_label": op.human_label,
        "is_business_open": op.is_business_open,
        "is_kitchen_open": op.is_kitchen_open,
        "force_closed": fc_until is not None,
        "force_closed_until": fc_until.isoformat() if fc_until else None,
        "force_closed_reason": fc_reason,
    }


@router.get("/organization/iiko-office")
async def get_organization_iiko_office(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Конфиг iiko Office для SupplyMind (без пароля)."""
    org_id = admin_org_from_session(request)
    org = await db.get(Organization, org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    return {"ok": True, **iiko_office_config_public(org)}


@router.patch("/organization/iiko-office")
async def patch_organization_iiko_office(
    request: Request,
    body: IikoOfficeConfigPatchBody,
    _perm: None = Depends(require_staff_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Сохранить учётные данные iiko Office в integration_config_json.iiko_office."""
    org_id = admin_org_from_session(request)
    org = await db.get(Organization, org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")

    host = body.host
    login = body.login
    if host is not None and not (host or "").strip():
        raise HTTPException(status_code=400, detail="host не должен быть пустым")
    if login is not None and not (login or "").strip():
        raise HTTPException(status_code=400, detail="login не должен быть пустым")
    if body.store_id is not None and not (body.store_id or "").strip():
        raise HTTPException(status_code=400, detail="store_id не должен быть пустым")

    if body.location_id is not None:
        loc = await db.scalar(
            select(Location.id).where(
                Location.id == int(body.location_id),
                Location.organization_id == int(org_id),
                Location.is_active.is_(True),
            )
        )
        if loc is None:
            raise HTTPException(status_code=400, detail="location_id не найден в этом филиале")

    encrypt_pw = fernet_or_none() is not None
    apply_iiko_office_config_patch(
        org,
        host=host,
        login=login,
        password_plain=body.password if body.password is not None else None,
        store_id=body.store_id,
        department_id=body.department_id,
        location_id=body.location_id,
        store_location_map=body.store_location_map,
        encrypt_password=encrypt_pw,
    )
    await db.commit()
    await db.refresh(org)
    return {"ok": True, **iiko_office_config_public(org)}


@router.patch("/organization/profile")
async def patch_organization_profile(
    request: Request,
    body: OrganizationProfilePatchBody,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Обновить профиль ресторана/филиала."""
    org_id = admin_org_from_session(request)
    org = await db.get(Organization, org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    if body.name is not None:
        nm = (body.name or "").strip()
        if not nm:
            raise HTTPException(status_code=400, detail="Название не должно быть пустым")
        org.name = nm
    if body.timezone is not None:
        tz_in = (body.timezone or "").strip()
        if tz_in:
            org.timezone = normalize_timezone_name(tz_in, default=(org.timezone or "Etc/GMT-5"))
    if body.currency is not None:
        org.currency = (body.currency or "").strip().upper() or org.currency
    if body.whatsapp_phone_number_id is not None:
        org.whatsapp_phone_number_id = (body.whatsapp_phone_number_id or "").strip()
    if body.telegram_ops_chat_id is not None:
        org.telegram_ops_chat_id = (body.telegram_ops_chat_id or "").strip()
    if body.schedule_json is not None:
        org.schedule_json = parse_schedule_json(body.schedule_json).model_dump(mode="json")
    if body.prepayment_legal_text is not None:
        raw = (body.prepayment_legal_text or "").strip()
        org.prepayment_legal_text = raw if raw else None
    if body.review_url_2gis is not None:
        raw_url = (body.review_url_2gis or "").strip()
        org.review_url_2gis = raw_url if raw_url else None
    if body.review_url_google is not None:
        raw_google = (body.review_url_google or "").strip()
        meta = _org_meta_dict(org)
        if raw_google:
            meta["review_url_google"] = raw_google
        else:
            meta.pop("review_url_google", None)
            meta.pop("guestcare_google_url", None)
        org.meta_json = meta or None
    await db.commit()
    await db.refresh(org)
    op = check_operational_status(
        org.timezone,
        getattr(org, "schedule_json", None),
        force_closed_until=getattr(org, "force_closed_until", None),
        force_closed_reason=getattr(org, "force_closed_reason", ""),
    )
    return {
        "ok": True,
        "organization_id": int(org.id),
        "name": org.name,
        "timezone": org.timezone,
        "currency": org.currency,
        "whatsapp_phone_number_id": (org.whatsapp_phone_number_id or "").strip(),
        "telegram_ops_chat_id": (getattr(org, "telegram_ops_chat_id", None) or "").strip(),
        "prepayment_legal_text": (getattr(org, "prepayment_legal_text", None) or "").strip(),
        "review_url_2gis": (getattr(org, "review_url_2gis", None) or "").strip(),
        "review_url_google": _review_url_google_from_org(org),
        "schedule_json": parse_schedule_json(getattr(org, "schedule_json", None)).model_dump(mode="json"),
        "operational_label": op.human_label,
        "is_business_open": op.is_business_open,
        "is_kitchen_open": op.is_kitchen_open,
    }


@router.post("/organization/force-close")
async def organization_force_close(
    request: Request,
    body: ForceCloseBody,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Экстренное закрытие/открытие заведения на N минут."""
    from datetime import timezone as _tz
    org_id = admin_org_from_session(request)
    org = await db.get(Organization, org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    if body.minutes == 0:
        org.force_closed_until = None
        org.force_closed_reason = ""
    else:
        from datetime import timedelta
        org.force_closed_until = datetime.now(_tz.utc) + timedelta(minutes=body.minutes)
        org.force_closed_reason = (body.reason or "").strip()
    await db.commit()
    await db.refresh(org)
    fc_until = getattr(org, "force_closed_until", None)
    op = check_operational_status(
        org.timezone,
        getattr(org, "schedule_json", None),
        force_closed_until=fc_until,
        force_closed_reason=getattr(org, "force_closed_reason", ""),
    )
    return {
        "ok": True,
        "force_closed": fc_until is not None,
        "force_closed_until": fc_until.isoformat() if fc_until else None,
        "force_closed_reason": getattr(org, "force_closed_reason", ""),
        "operational_label": op.human_label,
        "is_business_open": op.is_business_open,
        "is_kitchen_open": op.is_kitchen_open,
    }


@router.get("/staff")
async def list_staff(
    request: Request,
    _perm: None = Depends(require_staff_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    org_id = admin_org_from_session(request)
    rows = (
        await db.execute(
            select(StaffUser)
            .where(StaffUser.organization_id == org_id)
            .order_by(StaffUser.created_at.desc(), StaffUser.id.desc())
        )
    ).scalars().all()
    return {"ok": True, "users": [_staff_user_public(u) for u in rows]}


@router.post("/staff")
async def create_staff(
    request: Request,
    body: StaffCreateBody,
    _perm: None = Depends(require_staff_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    org_id = admin_org_from_session(request)
    email = (body.email or "").strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Укажите корректный email")
    role = (body.role or "").strip().lower() or StaffRole.OPERATOR.value
    if role not in (
        StaffRole.ADMIN.value,
        StaffRole.MANAGER.value,
        StaffRole.OPERATOR.value,
    ):
        raise HTTPException(status_code=400, detail="Некорректная роль")
    pwd = (body.password or "").strip()
    if not pwd:
        # временный пароль: покажем в UI один раз
        pwd = secrets.token_urlsafe(9)
    if len(pwd) < 8:
        raise HTTPException(status_code=400, detail="Пароль должен быть минимум 8 символов")

    exists_id = await db.scalar(select(StaffUser.id).where(StaffUser.email == email))
    if exists_id is not None:
        raise HTTPException(status_code=409, detail="Пользователь с таким email уже существует")

    valid_locs = await _valid_location_ids_for_org(db, org_id)
    meta_json: dict | None = None
    assigned_orgs: list[int] | None = None
    if role == StaffRole.MANAGER.value and body.assigned_org_ids is not None:
        allowed = await tenant_org_ids_for_staff_home(db, org_id)
        assigned_orgs = [int(x) for x in body.assigned_org_ids if int(x) in allowed]
        if not assigned_orgs:
            raise HTTPException(
                status_code=400,
                detail="assigned_org_ids должны быть активными филиалами вашей сети",
            )
    assigned_locs: list[int] | None = None
    if body.assigned_location_ids is not None:
        assigned_locs = [int(x) for x in body.assigned_location_ids if int(x) in valid_locs]
        if not assigned_locs:
            raise HTTPException(status_code=400, detail="assigned_location_ids должны быть точками этого филиала")
    if assigned_orgs is not None or assigned_locs is not None or body.role_metadata is not None:
        meta_json = _merge_staff_meta(
            None,
            assigned_org_ids=assigned_orgs,
            assigned_location_ids=assigned_locs,
            role_metadata=body.role_metadata,
        )

    u = StaffUser(
        organization_id=org_id,
        email=email,
        password_hash=hash_password(pwd),
        role=role,
        is_active=True,
        meta_json=meta_json,
    )
    db.add(u)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Не удалось создать пользователя") from None
    return {
        "ok": True,
        "user": _staff_user_public(u),
        "temp_password": pwd,
    }


@router.patch("/staff/{staff_id}")
async def update_staff(
    request: Request,
    staff_id: int,
    body: StaffUpdateBody,
    _perm: None = Depends(require_staff_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    org_id = admin_org_from_session(request)
    u = await db.get(StaffUser, int(staff_id))
    if u is None or int(u.organization_id) != int(org_id):
        raise HTTPException(status_code=404, detail="Сотрудник не найден")

    if body.role is not None:
        role = (body.role or "").strip().lower()
        if role not in (
            StaffRole.ADMIN.value,
            StaffRole.MANAGER.value,
            StaffRole.OPERATOR.value,
        ):
            raise HTTPException(status_code=400, detail="Некорректная роль")
        u.role = role

    if body.is_active is not None:
        u.is_active = bool(body.is_active)

    valid_locs = await _valid_location_ids_for_org(db, org_id)
    role_now = (u.role or "").strip().lower()
    clear_orgs = False
    clear_locs = False
    assigned_orgs_val: list[int] | None = None
    assigned_locs_val: list[int] | None = None

    if body.assigned_org_ids is not None:
        if role_now != StaffRole.MANAGER.value:
            clear_orgs = True
        else:
            allowed = await tenant_org_ids_for_staff_home(db, org_id)
            assigned_orgs_val = [int(x) for x in body.assigned_org_ids if int(x) in allowed]
            if not assigned_orgs_val:
                raise HTTPException(
                    status_code=400,
                    detail="assigned_org_ids должны быть активными филиалами вашей сети",
                )

    if body.assigned_location_ids is not None:
        if role_now not in (StaffRole.MANAGER.value, StaffRole.OPERATOR.value):
            clear_locs = True
        else:
            assigned_locs_val = [int(x) for x in body.assigned_location_ids if int(x) in valid_locs]
            if not assigned_locs_val:
                raise HTTPException(
                    status_code=400,
                    detail="assigned_location_ids должны быть точками этого филиала",
                )

    u.meta_json = _merge_staff_meta(
        u.meta_json if isinstance(u.meta_json, dict) else None,
        assigned_org_ids=assigned_orgs_val,
        assigned_location_ids=assigned_locs_val,
        role_metadata=body.role_metadata,
        clear_assigned_orgs=clear_orgs,
        clear_assigned_locations=clear_locs,
    )
    if not u.meta_json:
        u.meta_json = None

    await db.commit()
    await db.refresh(u)
    return {"ok": True, "user": _staff_user_public(u)}


# ─── Payment Provider Config ─────────────────────────────────────────────────


def _payment_config_dict(cfg: OrganizationPaymentConfig) -> dict:
    """Безопасное представление конфига — без расшифрованных секретов."""
    return {
        "id": cfg.id,
        "provider": cfg.provider,
        "is_enabled": cfg.is_enabled,
        "is_primary": cfg.is_primary,
        "merchant_id": cfg.merchant_id or "",
        "has_api_key": bool(cfg.encrypted_api_key),
        "has_secret_key": bool(cfg.encrypted_secret_key),
        "has_public_key": bool(cfg.encrypted_public_key),
        "extra_config": cfg.extra_config_json or {},
    }


@router.get("/organization/payment-config")
async def list_payment_configs(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Список платёжных провайдеров организации (без секретов)."""
    org_id = admin_org_from_session(request)
    rows = await db.scalars(
        select(OrganizationPaymentConfig).where(
            OrganizationPaymentConfig.organization_id == org_id
        )
    )
    return {"items": [_payment_config_dict(r) for r in rows]}


@router.put("/organization/payment-config/{provider}")
async def upsert_payment_config(
    request: Request,
    provider: str,
    body: PaymentConfigUpsertBody,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Создать или обновить конфигурацию платёжного провайдера."""
    from app.services.secrets_crypto import encrypt_secret

    org_id = admin_org_from_session(request)
    prov = (provider or "").strip().lower()
    if prov not in _SUPPORTED_PAYMENT_PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Провайдер '{prov}' не поддерживается")

    cfg = await db.scalar(
        select(OrganizationPaymentConfig).where(
            OrganizationPaymentConfig.organization_id == org_id,
            OrganizationPaymentConfig.provider == prov,
        )
    )
    if cfg is None:
        cfg = OrganizationPaymentConfig(organization_id=org_id, provider=prov)
        db.add(cfg)

    cfg.is_enabled = body.is_enabled
    cfg.merchant_id = (body.merchant_id or "").strip() or None

    # Шифруем только если передано новое значение
    if body.api_key.strip():
        cfg.encrypted_api_key = encrypt_secret(body.api_key.strip())
    if body.secret_key.strip():
        cfg.encrypted_secret_key = encrypt_secret(body.secret_key.strip())
    if body.public_key.strip():
        cfg.encrypted_public_key = encrypt_secret(body.public_key.strip())

    cfg.extra_config_json = {
        "environment": body.environment or "production",
        "callback_url": body.callback_url or "",
        "success_url": body.success_url or "",
    }

    # Если is_primary=True — снимаем флаг у остальных провайдеров этой org
    if body.is_primary:
        others = await db.scalars(
            select(OrganizationPaymentConfig).where(
                OrganizationPaymentConfig.organization_id == org_id,
                OrganizationPaymentConfig.provider != prov,
                OrganizationPaymentConfig.is_primary.is_(True),
            )
        )
        for other in others:
            other.is_primary = False
        cfg.is_primary = True
    else:
        cfg.is_primary = False

    await db.commit()
    await db.refresh(cfg)
    return {"ok": True, "item": _payment_config_dict(cfg)}


@router.delete("/organization/payment-config/{provider}")
async def delete_payment_config(
    request: Request,
    provider: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Удалить конфигурацию платёжного провайдера."""
    org_id = admin_org_from_session(request)
    prov = (provider or "").strip().lower()
    cfg = await db.scalar(
        select(OrganizationPaymentConfig).where(
            OrganizationPaymentConfig.organization_id == org_id,
            OrganizationPaymentConfig.provider == prov,
        )
    )
    if cfg is None:
        raise HTTPException(status_code=404, detail="Конфигурация не найдена")
    await db.delete(cfg)
    await db.commit()
    return {"ok": True}
