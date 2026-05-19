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
from app.db.models import Organization, OrganizationPaymentConfig, StaffRole, StaffUser
from app.db.session import get_db
from app.services.tenant_scope import tenant_org_ids_for_staff_home
from app.services.time_context import check_operational_status, parse_schedule_json
from app.services.timezones import normalize_timezone_name
from .deps import admin_org_from_session, require_admin_session_active, require_staff_admin

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


_VALID_PAYMENT_PROVIDERS = frozenset({"freedom_pay", "kaspi", "cloudpayments"})


class PaymentProviderToggleBody(BaseModel):
    provider: str = Field(..., min_length=2, max_length=64)
    enabled: bool


class ForceCloseBody(BaseModel):
    minutes: int = Field(..., ge=0, le=1440, description="0 = снять закрытие; >0 = закрыть на N минут")
    reason: str = Field(default="", max_length=255, description="Причина закрытия")


class StaffCreateBody(BaseModel):
    email: str = Field(..., min_length=3, max_length=255)
    role: str = Field(default=StaffRole.OPERATOR.value, description="admin | manager | operator")
    password: str = Field(default="", max_length=128, description="Опционально: если пусто — сгенерируем временный")
    assigned_org_ids: list[int] | None = Field(
        default=None,
        description="Филиалы для manager (подмножество сети); без списка — только домашний",
    )


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
        "schedule_json": parse_schedule_json(getattr(org, "schedule_json", None)).model_dump(mode="json"),
        "operational_label": op.human_label,
        "is_business_open": op.is_business_open,
        "is_kitchen_open": op.is_kitchen_open,
        "force_closed": fc_until is not None,
        "force_closed_until": fc_until.isoformat() if fc_until else None,
        "force_closed_reason": fc_reason,
    }


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
    out: list[dict] = []
    for u in rows:
        meta = u.meta_json if isinstance(u.meta_json, dict) else {}
        assigned = meta.get("assigned_org_ids") if isinstance(meta.get("assigned_org_ids"), list) else None
        out.append(
            {
                "id": int(u.id),
                "email": u.email,
                "role": u.role,
                "is_active": bool(u.is_active),
                "assigned_org_ids": assigned,
                "created_at": u.created_at.isoformat() if u.created_at else None,
            }
        )
    return {"ok": True, "users": out}


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

    meta_json: dict | None = None
    if role == StaffRole.MANAGER.value and body.assigned_org_ids is not None:
        allowed = await tenant_org_ids_for_staff_home(db, org_id)
        assigned = [int(x) for x in body.assigned_org_ids if int(x) in allowed]
        if not assigned:
            raise HTTPException(
                status_code=400,
                detail="assigned_org_ids должны быть активными филиалами вашей сети",
            )
        meta_json = {"assigned_org_ids": assigned}

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
    return {"ok": True, "user": {"id": int(u.id), "email": u.email, "role": u.role}, "temp_password": pwd}


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
