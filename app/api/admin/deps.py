"""
Общие зависимости, проверки сессии и SQL-clause для tenant-scope (E0.1).

Вынесено из монолита, чтобы следующие подмодули импортировали одну точку входа.
"""

from __future__ import annotations

import logging
import secrets

from fastapi import Depends, HTTPException, Request
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import (
    Booking,
    EscalationEvent,
    IntegrationEvent,
    KnowledgeItem,
    MenuItem,
    Order,
    Organization,
    PackagingRule,
    StaffRole,
    StaffUser,
    User,
)
from app.db.session import get_db
from app.services.order_logic import classify_packaging_kind
from app.services.org_iiko import resolve_org_iiko_credentials
from app.services.billing_guard import (
    billing_suspended_http_exception,
    tenant_billing_blocks_inbound,
)
from app.services.tenant_scope import organization_id_allowed_for_admin_session
from app.services.tenant_scope import phones_subquery_for_org as _phones_subquery_for_org

logger = logging.getLogger(__name__)


def _pick_seed_menu_item(menu_items: list[MenuItem]) -> MenuItem | None:
    """Первая доступная позиция без обязательного выбора упаковки плова 1 кг."""
    for mi in menu_items:
        if not mi.is_available:
            continue
        pk = classify_packaging_kind(mi.name or "", mi.category or "")
        if pk != "plov_1kg":
            return mi
    for mi in menu_items:
        if mi.is_available:
            return mi
    return None


def _credentials_ok(username: str, password: str) -> bool:
    u_ok = secrets.compare_digest(username, settings.admin_username)
    p_ok = secrets.compare_digest(password, settings.admin_password)
    return u_ok and p_ok


def _superadmin_credentials_ok(username: str, password: str) -> bool:
    """Legacy супер-админ по env (без StaffUser). Включается только если заданы оба секрета."""
    su = (settings.superadmin_username or "").strip()
    sp = (settings.superadmin_password or "").strip()
    if not su or not sp:
        return False
    u_ok = secrets.compare_digest(username, su)
    p_ok = secrets.compare_digest(password, sp)
    return u_ok and p_ok


def require_admin_session(request: Request) -> None:
    """Доступ только после успешного входа (cookie-сессия)."""
    if not request.session.get("admin_ok"):
        raise HTTPException(status_code=401, detail="Требуется вход в админку")


async def _session_organization_allowed_for_staff(
    request: Request,
    db: AsyncSession,
    staff: StaffUser,
) -> bool:
    """
    Сессия указывает на филиал, разрешённый для этого staff: «домашний» org, tenant_owner
    или (для суперадмина) любой активный филиал — см. ``organization_id_allowed_for_admin_session``.
    """
    org_id = admin_org_from_session(request)
    is_demo = bool(request.session.get("is_demo"))
    return await organization_id_allowed_for_admin_session(
        db,
        staff=staff,
        is_superadmin=bool(staff.is_superadmin),
        is_demo=is_demo,
        target_organization_id=int(org_id),
        session_organization_id=int(org_id),
    )


async def require_admin_session_active(request: Request, db: AsyncSession = Depends(get_db)) -> None:
    """Проверка сессии + активности организации; Super Admin не блокируется статусом org."""
    require_admin_session(request)
    if bool(request.session.get("is_demo")) and request.method.upper() not in {"GET", "HEAD", "OPTIONS"}:
        raise HTTPException(status_code=403, detail="Демо-режим: изменения запрещены")

    sid = request.session.get("staff_id")
    org_id = admin_org_from_session(request)

    if sid is None:
        org = await db.get(Organization, int(org_id))
        if org is not None and not bool(org.is_active):
            raise HTTPException(status_code=403, detail="Подписка приостановлена. Свяжитесь с администратором.")
        if org is not None and await tenant_billing_blocks_inbound(db, org):
            raise billing_suspended_http_exception()
        return

    staff = await db.get(StaffUser, int(sid))
    if staff is None or not bool(staff.is_active):
        raise HTTPException(status_code=403, detail="Недостаточно прав")
    if bool(staff.is_superadmin):
        return
    if not await _session_organization_allowed_for_staff(request, db, staff):
        raise HTTPException(status_code=403, detail="Недостаточно прав")
    org = await db.get(Organization, int(org_id))
    if org is not None and not bool(org.is_active):
        raise HTTPException(status_code=403, detail="Подписка приостановлена. Свяжитесь с администратором.")
    if org is not None and await tenant_billing_blocks_inbound(db, org):
        raise billing_suspended_http_exception()


async def require_staff_admin(request: Request, db: AsyncSession = Depends(get_db)) -> None:
    """
    Доступ только для admin-ролей staff (или legacy admin без staff_id).
    Нужен для чувствительных действий (команда/права).
    """
    await require_admin_session_active(request, db)
    sid = request.session.get("staff_id")
    if sid is None:
        return
    staff = await db.get(StaffUser, int(sid))
    if staff is None or not staff.is_active:
        raise HTTPException(status_code=403, detail="Недостаточно прав")
    if not await _session_organization_allowed_for_staff(request, db, staff):
        raise HTTPException(status_code=403, detail="Недостаточно прав")
    if (staff.role or "").strip().lower() != StaffRole.ADMIN.value:
        raise HTTPException(status_code=403, detail="Недостаточно прав")


async def _session_staff_user(request: Request, db: AsyncSession) -> StaffUser | None:
    sid = request.session.get("staff_id")
    if sid is None:
        return None
    try:
        return await db.get(StaffUser, int(sid))
    except (TypeError, ValueError):
        return None


async def _session_is_superadmin(request: Request, db: AsyncSession) -> bool:
    staff = await _session_staff_user(request, db)
    if staff is not None:
        return bool(staff.is_active and staff.is_superadmin)
    # Legacy супер-админ (по env): помечаем флагом в cookie-сессии на login.
    return bool(request.session.get("superadmin_ok"))


async def require_superadmin(request: Request, db: AsyncSession = Depends(get_db)) -> None:
    await require_admin_session_active(request, db)
    if not await _session_is_superadmin(request, db):
        raise HTTPException(status_code=403, detail="Только Super Admin")


def admin_org_from_session(request: Request) -> int:
    """organization_id текущей админ-сессии (staff или legacy)."""
    v = request.session.get("organization_id")
    if v is not None:
        return int(v)
    return int(settings.default_organization_id)


def admin_actor_key(request: Request) -> str:
    """Стабильный ключ действующего администратора для аудита/triage (staff id или email)."""
    sid = request.session.get("staff_id")
    if sid is not None:
        return f"staff:{sid}"
    email = request.session.get("email") or request.session.get("staff_email")
    if email:
        return f"staff:{email}"
    return "staff:session"


async def _order_in_org(db: AsyncSession, order_id: int, org_id: int) -> Order:
    """
    Заказ принадлежит филиалу: либо Order.organization_id совпадает, либо (legacy) NULL и user в этом org.
    Иначе 404 — без утечки «существует ли id у чужого арендатора».
    """
    order = await db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Заказ не найден")
    oid = order.organization_id
    if oid is not None:
        if int(oid) != int(org_id):
            raise HTTPException(status_code=404, detail="Заказ не найден")
        return order
    u = await db.get(User, order.user_id) if order.user_id else None
    if u is None or int(u.organization_id) != int(org_id):
        raise HTTPException(status_code=404, detail="Заказ не найден")
    return order


async def _iiko_login_org_for_tenant(
    db: AsyncSession,
    org_id: int,
    api_login: str | None,
    organization_id: str | None,
) -> tuple[str, str, str | None]:
    """
    Учётные данные iiko только для филиала ``org_id``.
    Не позволяет передать чужой api_login / iiko UUID в query (multi-tenant).
    Пустой query → из настроек филиала / .env через resolve_org_iiko_credentials.
    """
    creds = await resolve_org_iiko_credentials(db, org_id)
    if creds is None:
        raise HTTPException(
            status_code=400,
            detail="Задайте iiko в настройках филиала или IIKO_* в окружении.",
        )
    lq = (api_login or "").strip()
    oq = (organization_id or "").strip()
    if lq or oq:
        if not lq or not oq:
            raise HTTPException(
                status_code=400,
                detail="Передайте оба query api_login и organization_id или оставьте оба пустыми.",
            )
        if lq != (creds.api_login or "").strip() or oq != (creds.iiko_organization_id or "").strip():
            raise HTTPException(
                status_code=403,
                detail="Учётные данные iiko не соответствуют этому филиалу",
            )
    return (creds.api_login, creds.iiko_organization_id, (creds.terminal_group_id or None))


async def _menu_item_in_org(db: AsyncSession, item_id: int, org_id: int) -> MenuItem:
    """
    Позиция меню доступна для изменения этим филиалом.
    Legacy-строки с organization_id IS NULL — только у арендатора default_organization_id
    (общая номенклатура); остальные филиалы получают 404.
    """
    item = await db.get(MenuItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Позиция не найдена")
    if item.organization_id is not None:
        if int(item.organization_id) != int(org_id):
            raise HTTPException(status_code=404, detail="Позиция не найдена")
        return item
    if int(org_id) != int(settings.default_organization_id):
        raise HTTPException(status_code=404, detail="Позиция не найдена")
    return item


def _menu_tenant_clause(org_id: int):
    """Позиции меню арендатора + legacy без organization_id."""
    return or_(MenuItem.organization_id == org_id, MenuItem.organization_id.is_(None))


def _packaging_tenant_clause(org_id: int):
    return or_(PackagingRule.organization_id == org_id, PackagingRule.organization_id.is_(None))


def _knowledge_tenant_clause(org_id: int):
    return or_(KnowledgeItem.organization_id == org_id, KnowledgeItem.organization_id.is_(None))


def _bookings_tenant_clause(org_id: int):
    return or_(
        Booking.organization_id == org_id,
        and_(
            Booking.organization_id.is_(None),
            Booking.user_id.in_(select(User.id).where(User.organization_id == org_id)),
        ),
    )


def _escalation_tenant_clause(org_id: int):
    return or_(
        EscalationEvent.organization_id == org_id,
        and_(
            EscalationEvent.organization_id.is_(None),
            EscalationEvent.phone.in_(_phones_subquery_for_org(org_id)),
        ),
    )


def _integration_events_tenant_clause(org_id: int):
    """События синхронизации: только филиал; NULL — legacy у дефолтной организации."""
    if int(org_id) == int(settings.default_organization_id):
        return or_(IntegrationEvent.organization_id == org_id, IntegrationEvent.organization_id.is_(None))
    return IntegrationEvent.organization_id == org_id
