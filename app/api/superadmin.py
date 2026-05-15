"""Super Admin API: управление ресторанами и заявками."""

from __future__ import annotations

import base64
import secrets
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin import require_admin_session
from app.core.config import settings
from app.core.passwords import hash_password
from app.db.models import AiUsageLog, MessageAccountingLog, Order, Organization, PaymentWebhookEvent, RegistrationRequest, StaffRole, StaffUser, Tenant
from app.db.session import get_db
from app.services.integration_health import record_menu_sync
from app.services.menu_sync import sync_menu_from_iiko
from app.services.org_iiko import resolve_org_iiko_credentials
from app.services.secrets_crypto import encrypt_secret, fernet_or_none
from app.services.time_context import check_operational_status, parse_schedule_json

router = APIRouter(prefix="/superadmin", tags=["Super Admin"])


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


async def _send_superadmin_telegram_html(text: str) -> None:
    token = (settings.telegram_bot_token or "").strip()
    chat_id = (settings.superadmin_telegram_chat_id or settings.telegram_admin_chat_id or "").strip()
    if not token or not chat_id:
        return
    payload = {
        "chat_id": chat_id,
        "text": text[:4000],
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()


async def require_superadmin(request: Request, db: AsyncSession = Depends(get_db)) -> StaffUser:
    require_admin_session(request)
    sid = request.session.get("staff_id")
    if sid is None:
        raise HTTPException(status_code=403, detail="Только для Super Admin")
    staff = await db.get(StaffUser, int(sid))
    if staff is None or not staff.is_active or not bool(staff.is_superadmin):
        raise HTTPException(status_code=403, detail="Только для Super Admin")
    return staff


class OrganizationCreateBody(BaseModel):
    restaurant_name: str = Field(..., min_length=2, max_length=255)
    tenant_name: str = Field(default="", max_length=255)
    plan: str = Field(default="standard", max_length=64)
    admin_email: str = Field(..., min_length=3, max_length=255)
    admin_password: str = Field(default="", max_length=128)


class OrganizationStatusBody(BaseModel):
    is_active: bool


class OrganizationCredentialsBody(BaseModel):
    iiko_api_login: str | None = None
    iiko_organization_id: str | None = None
    iiko_terminal_group_id: str | None = None
    whatsapp_phone_number_id: str | None = None
    telegram_ops_chat_id: str | None = None


class OrganizationScheduleBody(BaseModel):
    schedule_json: dict[str, object]


class RegistrationRequestBody(BaseModel):
    restaurant_name: str = Field(..., min_length=2, max_length=255)
    contact_name: str = Field(default="", max_length=255)
    phone: str = Field(default="", max_length=32)
    email: str = Field(default="", max_length=255)
    has_iiko: bool = False
    note: str = Field(default="", max_length=4000)


class RegistrationDecisionBody(BaseModel):
    reason: str = Field(default="", max_length=1000)


class RegistrationApproveBody(BaseModel):
    tenant_name: str = Field(default="", max_length=255)
    plan: str = Field(default="standard", max_length=64)
    admin_email: str = Field(default="", max_length=255)
    admin_password: str = Field(default="", max_length=128)


class PasswordResetBody(BaseModel):
    password: str = Field(default="", max_length=128)


@router.get("/me")
async def superadmin_me(_staff: StaffUser = Depends(require_superadmin)) -> dict:
    return {"ok": True}


@router.get("/organizations")
async def superadmin_organizations(
    _staff: StaffUser = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    now = _now_utc()
    from_dt = now - timedelta(days=30)

    orders_subq = (
        select(
            Order.organization_id.label("org_id"),
            func.count(Order.id).label("orders_30d"),
            func.coalesce(func.sum(Order.total_price), 0).label("revenue_30d"),
        )
        .where(Order.created_at >= from_dt)
        .group_by(Order.organization_id)
        .subquery()
    )
    staff_subq = (
        select(
            StaffUser.organization_id.label("org_id"),
            func.count(StaffUser.id).label("staff_count"),
        )
        .where(StaffUser.is_active.is_(True))
        .group_by(StaffUser.organization_id)
        .subquery()
    )

    q = await db.execute(
        select(
            Organization,
            Tenant.name.label("tenant_name"),
            Tenant.plan.label("tenant_plan"),
            func.coalesce(orders_subq.c.orders_30d, 0),
            func.coalesce(orders_subq.c.revenue_30d, 0),
            func.coalesce(staff_subq.c.staff_count, 0),
        )
        .outerjoin(Tenant, Tenant.id == Organization.tenant_id)
        .outerjoin(orders_subq, orders_subq.c.org_id == Organization.id)
        .outerjoin(staff_subq, staff_subq.c.org_id == Organization.id)
        .order_by(Organization.created_at.desc(), Organization.id.desc())
    )
    rows = []
    for org, tenant_name, tenant_plan, orders_30d, revenue_30d, staff_count in q.all():
        op = check_operational_status(
            (org.timezone or "").strip() or "Etc/GMT-5",
            getattr(org, "schedule_json", None),
        )
        rows.append(
            {
                "id": int(org.id),
                "name": org.name,
                "slug": org.slug or "",
                "is_active": bool(org.is_active),
                "is_demo": bool(getattr(org, "is_demo", False)),
                "created_at": org.created_at.isoformat() if org.created_at else None,
                "tenant_id": int(org.tenant_id) if org.tenant_id is not None else None,
                "tenant_name": tenant_name or "",
                "tenant_plan": tenant_plan or "",
                "orders_30d": int(orders_30d or 0),
                "revenue_30d": float(revenue_30d or 0),
                "staff_count": int(staff_count or 0),
                "has_iiko": bool((org.iiko_organization_id or "").strip() and ((org.iiko_api_login or "").strip() or (org.iiko_api_login_enc or "").strip())),
                "has_whatsapp": bool((org.whatsapp_phone_number_id or "").strip()),
                "iiko_organization_id": (org.iiko_organization_id or "").strip(),
                "whatsapp_phone_number_id": (org.whatsapp_phone_number_id or "").strip(),
                "telegram_ops_chat_id": (org.telegram_ops_chat_id or "").strip(),
                "schedule_json": parse_schedule_json(getattr(org, "schedule_json", None)).model_dump(mode="json"),
                "operational_label": op.human_label,
                "is_business_open": op.is_business_open,
                "is_kitchen_open": op.is_kitchen_open,
            },
        )
    return {"items": rows}


@router.post("/organizations")
async def superadmin_create_organization(
    body: OrganizationCreateBody,
    _staff: StaffUser = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    email = (body.admin_email or "").strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Укажите корректный email администратора")
    exists = await db.scalar(select(StaffUser.id).where(StaffUser.email == email))
    if exists is not None:
        raise HTTPException(status_code=409, detail="Пользователь с таким email уже существует")

    tenant_name = (body.tenant_name or "").strip()
    tenant_id: int | None = None
    if tenant_name:
        tenant = Tenant(name=tenant_name, plan=(body.plan or "standard").strip() or "standard")
        db.add(tenant)
        await db.flush()
        tenant_id = int(tenant.id)

    org = Organization(
        name=(body.restaurant_name or "").strip(),
        tenant_id=tenant_id,
    )
    db.add(org)
    await db.flush()

    generated = False
    raw_password = (body.admin_password or "").strip()
    if not raw_password:
        raw_password = secrets.token_urlsafe(10)
        generated = True
    staff = StaffUser(
        organization_id=int(org.id),
        email=email,
        password_hash=hash_password(raw_password),
        role=StaffRole.ADMIN.value,
        is_active=True,
    )
    db.add(staff)
    await db.commit()

    return {
        "ok": True,
        "organization_id": int(org.id),
        "staff_id": int(staff.id),
        "admin_email": email,
        "generated_password": raw_password if generated else "",
    }


@router.get("/organizations/{organization_id}/staff")
async def superadmin_organization_staff(
    organization_id: int,
    _staff: StaffUser = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    org = await db.get(Organization, int(organization_id))
    if org is None:
        raise HTTPException(status_code=404, detail="Организация не найдена")
    q = await db.execute(
        select(StaffUser)
        .where(StaffUser.organization_id == int(organization_id))
        .order_by(StaffUser.created_at.desc(), StaffUser.id.desc()),
    )
    items = []
    for s in q.scalars().all():
        items.append(
            {
                "id": int(s.id),
                "email": s.email,
                "role": (s.role or "").strip().lower(),
                "is_active": bool(s.is_active),
                "is_superadmin": bool(s.is_superadmin),
                "created_at": s.created_at.isoformat() if s.created_at else None,
            },
        )
    return {"items": items}


@router.post("/organizations/{organization_id}/staff/{staff_id}/reset-password")
async def superadmin_reset_staff_password(
    organization_id: int,
    staff_id: int,
    body: PasswordResetBody,
    _staff: StaffUser = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    org = await db.get(Organization, int(organization_id))
    if org is None:
        raise HTTPException(status_code=404, detail="Организация не найдена")
    target = await db.get(StaffUser, int(staff_id))
    if target is None or int(target.organization_id) != int(organization_id):
        raise HTTPException(status_code=404, detail="Сотрудник не найден")
    new_password = (body.password or "").strip() or secrets.token_urlsafe(10)
    target.password_hash = hash_password(new_password)
    await db.commit()
    return {"ok": True, "staff_id": int(target.id), "new_password": new_password}


@router.patch("/organizations/{organization_id}/status")
async def superadmin_set_organization_status(
    organization_id: int,
    body: OrganizationStatusBody,
    _staff: StaffUser = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    org = await db.get(Organization, int(organization_id))
    if org is None:
        raise HTTPException(status_code=404, detail="Организация не найдена")
    org.is_active = bool(body.is_active)
    await db.commit()
    return {"ok": True, "organization_id": int(org.id), "is_active": bool(org.is_active)}


@router.patch("/organizations/{organization_id}/credentials")
async def superadmin_update_organization_credentials(
    organization_id: int,
    body: OrganizationCredentialsBody,
    _staff: StaffUser = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    org = await db.get(Organization, int(organization_id))
    if org is None:
        raise HTTPException(status_code=404, detail="Организация не найдена")

    if body.whatsapp_phone_number_id is not None:
        org.whatsapp_phone_number_id = (body.whatsapp_phone_number_id or "").strip()
    if body.telegram_ops_chat_id is not None:
        org.telegram_ops_chat_id = (body.telegram_ops_chat_id or "").strip()
    if body.iiko_terminal_group_id is not None:
        org.iiko_terminal_group_id = (body.iiko_terminal_group_id or "").strip()
    if body.iiko_organization_id is not None:
        org.iiko_organization_id = (body.iiko_organization_id or "").strip()
    if body.iiko_api_login is not None:
        login = (body.iiko_api_login or "").strip()
        if login:
            if fernet_or_none() is not None:
                org.iiko_api_login_enc = encrypt_secret(login)
                org.iiko_api_login = ""
            else:
                org.iiko_api_login = login
                org.iiko_api_login_enc = None
        else:
            org.iiko_api_login = ""
            org.iiko_api_login_enc = None

    await db.commit()
    return {"ok": True}


@router.patch("/organizations/{organization_id}/schedule")
async def superadmin_update_organization_schedule(
    organization_id: int,
    body: OrganizationScheduleBody,
    _staff: StaffUser = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    org = await db.get(Organization, int(organization_id))
    if org is None:
        raise HTTPException(status_code=404, detail="Организация не найдена")
    org.schedule_json = parse_schedule_json(body.schedule_json).model_dump(mode="json")
    await db.commit()
    return {"ok": True, "organization_id": int(org.id), "schedule_json": org.schedule_json}


@router.post("/organizations/{organization_id}/sync-menu")
async def superadmin_sync_org_menu(
    organization_id: int,
    _staff: StaffUser = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    org = await db.get(Organization, int(organization_id))
    if org is None:
        raise HTTPException(status_code=404, detail="Организация не найдена")
    creds = await resolve_org_iiko_credentials(db, int(org.id))
    if creds is None:
        raise HTTPException(status_code=400, detail="Сначала заполните iiko_api_login и iiko_organization_id")
    try:
        stats = await sync_menu_from_iiko(
            db,
            creds.api_login,
            creds.iiko_organization_id,
            restomind_organization_id=int(org.id),
        )
        sk = stats.get("skipped")
        detail_m = (
            f"Синхронизация меню (superadmin): успешно "
            f"(всего {stats.get('total', 0)}, новых {stats.get('created', 0)}, обновлено {stats.get('updated', 0)}"
            + (f", пропущено {sk}" if sk else "")
            + ")"
        )
        await record_menu_sync(db, True, None, detail=detail_m, organization_id=int(org.id))
        await db.commit()
        return {"ok": True, "stats": stats}
    except Exception as exc:
        err = str(exc)
        await record_menu_sync(
            db, False, err, detail=f"Синхронизация меню (superadmin): ошибка — {err[:400]}",
            organization_id=int(org.id),
        )
        await db.commit()
        raise HTTPException(status_code=502, detail=err[:500]) from exc


@router.get("/registration-requests")
async def superadmin_registration_requests(
    status: str = "pending",
    _staff: StaffUser = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    st = (status or "").strip().lower()
    q = select(RegistrationRequest).order_by(RegistrationRequest.created_at.desc(), RegistrationRequest.id.desc())
    if st:
        q = q.where(RegistrationRequest.status == st)
    rows = (await db.execute(q)).scalars().all()
    items = []
    for row in rows:
        items.append(
            {
                "id": int(row.id),
                "restaurant_name": row.restaurant_name,
                "contact_name": row.contact_name or "",
                "phone": row.phone or "",
                "email": row.email or "",
                "has_iiko": bool(row.has_iiko),
                "note": row.note or "",
                "status": row.status,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "decided_at": row.decided_at.isoformat() if row.decided_at else None,
                "decided_by_staff_id": int(row.decided_by_staff_id) if row.decided_by_staff_id is not None else None,
            },
        )
    return {"items": items}


@router.post("/registration-requests")
async def create_registration_request(
    body: RegistrationRequestBody,
    db: AsyncSession = Depends(get_db),
) -> dict:
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
    try:
        await _send_superadmin_telegram_html(
            (
                "🆕 <b>Новая заявка на подключение</b>\n"
                f"Ресторан: <code>{req.restaurant_name}</code>\n"
                f"Контакт: <code>{req.contact_name or '—'}</code>\n"
                f"Телефон: <code>{req.phone or '—'}</code>\n"
                f"Email: <code>{req.email or '—'}</code>\n"
                f"iiko: <code>{'да' if req.has_iiko else 'нет'}</code>"
            ),
        )
    except Exception:
        # Оповещение best-effort: не ломаем API заявки.
        pass
    return {"ok": True, "request_id": int(req.id)}


@router.post("/registration-requests/{request_id}/approve")
async def superadmin_approve_registration_request(
    request_id: int,
    body: RegistrationApproveBody,
    current: StaffUser = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    req = await db.get(RegistrationRequest, int(request_id))
    if req is None:
        raise HTTPException(status_code=404, detail="Заявка не найдена")
    if (req.status or "").strip().lower() != "pending":
        raise HTTPException(status_code=409, detail="Заявка уже обработана")

    email = (body.admin_email or req.email or "").strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Укажите корректный email администратора")
    exists = await db.scalar(select(StaffUser.id).where(StaffUser.email == email))
    if exists is not None:
        raise HTTPException(status_code=409, detail="Пользователь с таким email уже существует")

    tenant_name = (body.tenant_name or "").strip()
    tenant_id: int | None = None
    if tenant_name:
        t = Tenant(name=tenant_name, plan=(body.plan or "standard").strip() or "standard")
        db.add(t)
        await db.flush()
        tenant_id = int(t.id)

    org = Organization(name=(req.restaurant_name or "").strip(), tenant_id=tenant_id)
    db.add(org)
    await db.flush()

    generated = False
    pwd = (body.admin_password or "").strip()
    if not pwd:
        pwd = secrets.token_urlsafe(10)
        generated = True
    staff = StaffUser(
        organization_id=int(org.id),
        email=email,
        password_hash=hash_password(pwd),
        role=StaffRole.ADMIN.value,
        is_active=True,
    )
    db.add(staff)

    req.status = "approved"
    req.decided_at = _now_utc()
    req.decided_by_staff_id = int(current.id)
    await db.commit()

    return {
        "ok": True,
        "organization_id": int(org.id),
        "staff_id": int(staff.id),
        "admin_email": email,
        "generated_password": pwd if generated else "",
    }


@router.post("/registration-requests/{request_id}/reject")
async def superadmin_reject_registration_request(
    request_id: int,
    _body: RegistrationDecisionBody,
    current: StaffUser = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    req = await db.get(RegistrationRequest, int(request_id))
    if req is None:
        raise HTTPException(status_code=404, detail="Заявка не найдена")
    if (req.status or "").strip().lower() != "pending":
        raise HTTPException(status_code=409, detail="Заявка уже обработана")
    req.status = "rejected"
    req.decided_at = _now_utc()
    req.decided_by_staff_id = int(current.id)
    await db.commit()
    return {"ok": True}


@router.get("/payment-webhook-events")
async def superadmin_payment_webhook_events(
    provider: str | None = None,
    applied: bool | None = None,
    limit: int = 50,
    offset: int = 0,
    _staff: StaffUser = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Аудит входящих платёжных webhook (сырой payload)."""
    lim = max(1, min(int(limit or 50), 200))
    off = max(0, int(offset or 0))
    q = select(PaymentWebhookEvent).order_by(PaymentWebhookEvent.received_at.desc())
    if provider and (provider.strip()):
        q = q.where(PaymentWebhookEvent.provider_slug == provider.strip().lower()[:64])
    if applied is not None:
        q = q.where(PaymentWebhookEvent.applied == bool(applied))
    q = q.offset(off).limit(lim)
    rows = (await db.execute(q)).scalars().all()
    items = []
    for ev in rows:
        items.append(
            {
                "id": int(ev.id),
                "provider_slug": ev.provider_slug,
                "organization_id": int(ev.organization_id) if ev.organization_id is not None else None,
                "order_id": int(ev.order_id) if ev.order_id is not None else None,
                "external_payment_id": ev.external_payment_id,
                "verified": bool(ev.verified),
                "verify_error": (ev.verify_error or "")[:500],
                "applied": bool(ev.applied),
                "duplicate": bool(ev.duplicate),
                "payment_event_id": int(ev.payment_event_id) if ev.payment_event_id is not None else None,
                "received_at": ev.received_at.isoformat() if ev.received_at else None,
                "has_payload": bool(ev.payload_bytes or ev.payload_text),
            },
        )
    return {"items": items, "limit": lim, "offset": off}


@router.get("/payment-webhook-events/{event_id}")
async def superadmin_payment_webhook_event_detail(
    event_id: int,
    _staff: StaffUser = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    ev = await db.get(PaymentWebhookEvent, int(event_id))
    if ev is None:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    payload_b64 = None
    if ev.payload_bytes:
        payload_b64 = base64.b64encode(bytes(ev.payload_bytes)).decode("ascii")
    return {
        "id": int(ev.id),
        "provider_slug": ev.provider_slug,
        "organization_id": int(ev.organization_id) if ev.organization_id is not None else None,
        "order_id": int(ev.order_id) if ev.order_id is not None else None,
        "external_payment_id": ev.external_payment_id,
        "signature_header": ev.signature_header,
        "http_headers_json": ev.http_headers_json,
        "payload_text": ev.payload_text,
        "payload_bytes_base64": payload_b64,
        "verified": bool(ev.verified),
        "verify_error": ev.verify_error or "",
        "parsed_status": ev.parsed_status,
        "parsed_amount": float(ev.parsed_amount) if ev.parsed_amount is not None else None,
        "applied": bool(ev.applied),
        "duplicate": bool(ev.duplicate),
        "payment_event_id": int(ev.payment_event_id) if ev.payment_event_id is not None else None,
        "received_at": ev.received_at.isoformat() if ev.received_at else None,
    }


# ── E1 хвост ── сырой payload для отладки (кнопка в superadmin.html)


@router.get("/ai-usage")
async def superadmin_ai_usage(
    _staff: StaffUser = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Токены ИИ за сегодня по всем организациям."""
    from datetime import date
    today = date.today()
    rows = (await db.execute(
        select(Organization.id, Organization.name, AiUsageLog.total_tokens)
        .outerjoin(AiUsageLog, (AiUsageLog.organization_id == Organization.id) & (AiUsageLog.day == today))
        .where(Organization.is_active.is_(True))
        .order_by(AiUsageLog.total_tokens.desc().nulls_last(), Organization.name)
    )).all()
    return {
        "date": today.isoformat(),
        "items": [
            {"org_id": r[0], "org_name": r[1], "tokens": int(r[2]) if r[2] is not None else 0}
            for r in rows
        ],
    }


@router.get("/message-accounting")
async def superadmin_message_accounting(
    days: int = 7,
    _staff: StaffUser = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Сводка входящих/исходящих сообщений WhatsApp по организациям за N дней."""
    days = max(1, min(int(days), 90))
    since = date.today() - timedelta(days=days - 1)

    rows = (await db.execute(
        select(
            Organization.id,
            Organization.name,
            MessageAccountingLog.direction,
            MessageAccountingLog.source,
            MessageAccountingLog.message_type,
            func.sum(MessageAccountingLog.count).label("total"),
        )
        .join(MessageAccountingLog, MessageAccountingLog.organization_id == Organization.id)
        .where(
            Organization.is_active.is_(True),
            MessageAccountingLog.day >= since,
        )
        .group_by(
            Organization.id,
            Organization.name,
            MessageAccountingLog.direction,
            MessageAccountingLog.source,
            MessageAccountingLog.message_type,
        )
        .order_by(Organization.name)
    )).all()

    # Агрегируем по org_id
    by_org: dict[int, dict] = defaultdict(lambda: {
        "org_name": "",
        "inbound": 0,
        "outbound_ai": 0,
        "outbound_operator": 0,
        "outbound_template": 0,
        "outbound_other": 0,
    })
    for org_id, org_name, direction, source, msg_type, total in rows:
        e = by_org[org_id]
        e["org_name"] = org_name
        n = int(total or 0)
        if direction == "inbound":
            e["inbound"] += n
        elif direction == "outbound":
            if source == "ai":
                e["outbound_ai"] += n
            elif source == "operator":
                e["outbound_operator"] += n
            elif source == "system" and msg_type == "template":
                e["outbound_template"] += n
            elif source == "system":
                e["outbound_template"] += n  # text blast тоже в template
            else:
                e["outbound_other"] += n

    items = [
        {
            "org_id": org_id,
            "org_name": v["org_name"],
            "inbound": v["inbound"],
            "outbound_ai": v["outbound_ai"],
            "outbound_operator": v["outbound_operator"],
            "outbound_template": v["outbound_template"],
            "outbound_total": v["outbound_ai"] + v["outbound_operator"] + v["outbound_template"] + v["outbound_other"],
        }
        for org_id, v in sorted(by_org.items(), key=lambda x: x[1]["org_name"])
    ]
    return {"period_days": days, "since": since.isoformat(), "items": items}


@router.get("/payment-webhook-events/{event_id}/payload.bin")
async def superadmin_payment_webhook_payload_bin(
    event_id: int,
    _staff: StaffUser = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Скачивание сырого тела webhook (как пришло), для Super Admin."""
    ev = await db.get(PaymentWebhookEvent, int(event_id))
    if ev is None:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    body = bytes(ev.payload_bytes) if ev.payload_bytes else b""
    return Response(
        content=body,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="payment_webhook_{int(event_id)}.bin"',
        },
    )
