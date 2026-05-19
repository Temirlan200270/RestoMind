"""
Общие SQLAlchemy-условия для мультитенантности и legacy-строк без organization_id.

Централизуем, чтобы админка, ROI и фоновые сервисы (авто-iiko и т.д.) не расходились.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import FailedTask, Order, Organization, StaffRole, StaffUser, Tenant, User


def phones_subquery_for_org(org_id: int):
    return select(User.phone).where(User.organization_id == org_id)


def orders_tenant_clause(org_id: int):
    """Заказы филиала: явный organization_id или legacy через user."""
    return or_(
        Order.organization_id == org_id,
        and_(
            Order.organization_id.is_(None),
            Order.user_id.in_(select(User.id).where(User.organization_id == org_id)),
        ),
    )


def failed_tasks_tenant_clause(org_id: int):
    return or_(
        FailedTask.organization_id == org_id,
        and_(
            FailedTask.organization_id.is_(None),
            FailedTask.phone.in_(phones_subquery_for_org(org_id)),
        ),
    )


def branding_empty_payload() -> dict[str, Any | None]:
    """Дефолтная (пустая) форма branding для GET /auth/me и GET /admin/branding."""
    return {"brand_name": None, "brand_logo_url": None, "brand_color_hex": None}


# Сохраняем старое имя для обратной совместимости импорта (тесты, чужие модули).
branding_placeholder_e21 = branding_empty_payload


def staff_role_normalized(staff: StaffUser) -> str:
    return (staff.role or StaffRole.ADMIN.value).strip().lower()


def staff_assigned_org_ids(staff: StaffUser) -> list[int] | None:
    """Список филиалов для manager из meta_json. None — не фильтровать по assigned."""
    meta = getattr(staff, "meta_json", None)
    if not isinstance(meta, dict):
        return None
    raw = meta.get("assigned_org_ids")
    if raw is None:
        return None
    if not isinstance(raw, list):
        return None
    out: list[int] = []
    for x in raw:
        try:
            oid = int(x)
        except (TypeError, ValueError):
            continue
        if oid > 0:
            out.append(oid)
    return out


async def tenant_org_ids_for_staff_home(db: AsyncSession, home_org_id: int) -> set[int]:
    """ID филиалов сети для домашнего organization_id staff (или только home)."""
    home = await db.get(Organization, int(home_org_id))
    if home is None:
        return set()
    if home.tenant_id is None:
        return {int(home.id)}
    orgs = await _tenant_org_list(db, int(home.tenant_id))
    return {int(o.id) for o in orgs}


async def _tenant_org_list(db: AsyncSession, tenant_id: int) -> list[Organization]:
    res = await db.execute(
        select(Organization)
        .where(
            Organization.tenant_id == int(tenant_id),
            Organization.is_active.is_(True),
        )
        .order_by(Organization.id.asc()),
    )
    return list(res.scalars().all())


def _filter_orgs_by_assigned(
    orgs: list[Organization],
    assigned_ids: list[int] | None,
) -> list[Organization]:
    if assigned_ids is None:
        return orgs
    allowed = {int(x) for x in assigned_ids}
    return [o for o in orgs if int(o.id) in allowed]


async def resolve_active_tenant_id(
    db: AsyncSession,
    *,
    staff: StaffUser | None,
    active_organization_id: int,
) -> int | None:
    """Tenant_id для текущей сессии (E2.1: владелец сети vs филиал)."""
    if staff is not None and staff.tenant_owner_id is not None:
        return int(staff.tenant_owner_id)
    org = await db.get(Organization, int(active_organization_id))
    if org is None or org.tenant_id is None:
        return None
    return int(org.tenant_id)


async def resolve_branding_for_session(
    db: AsyncSession,
    *,
    staff: StaffUser | None,
    active_organization_id: int,
) -> dict[str, Any | None]:
    """Branding для контракта /auth/me; null-поля если арендатор не настроил бренд."""
    tenant_id = await resolve_active_tenant_id(
        db,
        staff=staff,
        active_organization_id=int(active_organization_id),
    )
    if tenant_id is None:
        return branding_empty_payload()
    t = await db.get(Tenant, tenant_id)
    if t is None:
        return branding_empty_payload()
    return {
        "brand_name": str(t.brand_name) if t.brand_name else None,
        "brand_color_hex": str(t.brand_color_hex) if t.brand_color_hex else None,
        "brand_logo_url": str(t.brand_logo_url) if t.brand_logo_url else None,
    }


async def available_organizations_for_admin_session(
    db: AsyncSession,
    *,
    staff: StaffUser | None,
    is_superadmin: bool,
    is_demo: bool,
    session_organization_id: int,
) -> list[dict[str, int | str]]:
    """
    Филиалы, доступные текущей админ-сессии для селектора и POST /auth/select-org.

    Суперадмин видит все активные организации; владелец сети — все филиалы tenant;
    обычный staff — один филиал.
    """
    if is_demo:
        row = await db.get(Organization, int(session_organization_id))
        if row is None:
            return []
        return [{"id": int(row.id), "name": str(row.name)}]

    if is_superadmin:
        res = await db.execute(
            select(Organization.id, Organization.name)
            .where(Organization.is_active.is_(True))
            .order_by(Organization.id.asc()),
        )
        return [{"id": int(r[0]), "name": str(r[1])} for r in res.all()]

    if staff is None:
        row = await db.get(Organization, int(session_organization_id))
        if row is None:
            return []
        return [{"id": int(row.id), "name": str(row.name)}]

    role = staff_role_normalized(staff)
    home_id = int(staff.organization_id)

    if role == StaffRole.OPERATOR.value:
        row = await db.get(Organization, home_id)
        if row is None:
            return []
        return [{"id": int(row.id), "name": str(row.name)}]

    tid = staff.tenant_owner_id
    if tid is not None:
        orgs = await _tenant_org_list(db, int(tid))
        seen = {int(o.id) for o in orgs}
        if home_id not in seen:
            home = await db.get(Organization, home_id)
            if home is not None and home.tenant_id is not None and int(home.tenant_id) == int(tid):
                orgs.append(home)
                orgs.sort(key=lambda o: int(o.id))
        if role == StaffRole.MANAGER.value:
            assigned = staff_assigned_org_ids(staff)
            if assigned is not None:
                orgs = _filter_orgs_by_assigned(orgs, assigned)
            else:
                orgs = [o for o in orgs if int(o.id) == home_id]
        return [{"id": int(o.id), "name": str(o.name)} for o in orgs]

    home = await db.get(Organization, home_id)
    if home is None:
        return []

    if role == StaffRole.MANAGER.value:
        assigned = staff_assigned_org_ids(staff)
        if assigned is not None and home.tenant_id is not None:
            tenant_orgs = await _tenant_org_list(db, int(home.tenant_id))
            orgs = _filter_orgs_by_assigned(tenant_orgs, assigned)
        elif assigned is not None:
            orgs = [home] if home_id in {int(x) for x in assigned} else []
        else:
            orgs = [home]
        return [{"id": int(o.id), "name": str(o.name)} for o in orgs]

    return [{"id": int(home.id), "name": str(home.name)}]


async def resolve_tenant_summary_for_session(
    db: AsyncSession,
    *,
    staff: StaffUser | None,
    active_organization_id: int,
) -> dict[str, Any] | None:
    """Краткое описание сети для GET /auth/me."""
    tenant_id = await resolve_active_tenant_id(
        db,
        staff=staff,
        active_organization_id=int(active_organization_id),
    )
    if tenant_id is None:
        return None
    t = await db.get(Tenant, tenant_id)
    if t is None:
        return None
    return {
        "id": int(t.id),
        "name": str(t.name),
        "plan": str(t.plan),
        "plan_status": str(t.plan_status or "active"),
    }


async def organization_id_allowed_for_admin_session(
    db: AsyncSession,
    *,
    staff: StaffUser | None,
    is_superadmin: bool,
    is_demo: bool,
    target_organization_id: int,
    session_organization_id: int,
) -> bool:
    allowed = await available_organizations_for_admin_session(
        db,
        staff=staff,
        is_superadmin=is_superadmin,
        is_demo=is_demo,
        session_organization_id=int(session_organization_id),
    )
    target = int(target_organization_id)
    return any(int(x["id"]) == target for x in allowed)
