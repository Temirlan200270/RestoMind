"""
Общие SQLAlchemy-условия для мультитенантности и legacy-строк без organization_id.

Централизуем, чтобы админка, ROI и фоновые сервисы (авто-iiko и т.д.) не расходились.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import FailedTask, Order, Organization, StaffUser, Tenant, User


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


def branding_placeholder_e21() -> dict[str, Any | None]:
    """Заглушка до E2.2 (Tenant.brand_*); контракт auth/me уже резервирует поля."""
    return {"brand_name": None, "brand_logo_url": None, "brand_color_hex": None}


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

    tid = staff.tenant_owner_id
    if tid is None:
        row = await db.get(Organization, int(staff.organization_id))
        if row is None:
            return []
        return [{"id": int(row.id), "name": str(row.name)}]

    res = await db.execute(
        select(Organization)
        .where(
            Organization.tenant_id == int(tid),
            Organization.is_active.is_(True),
        )
        .order_by(Organization.id.asc()),
    )
    orgs = list(res.scalars().all())
    home_id = int(staff.organization_id)
    seen = {int(o.id) for o in orgs}
    if home_id not in seen:
        home = await db.get(Organization, home_id)
        if home is not None and home.tenant_id is not None and int(home.tenant_id) == int(tid):
            orgs.append(home)
            orgs.sort(key=lambda o: int(o.id))

    return [{"id": int(o.id), "name": str(o.name)} for o in orgs]


async def resolve_tenant_summary_for_session(
    db: AsyncSession,
    *,
    staff: StaffUser | None,
    active_organization_id: int,
) -> dict[str, Any] | None:
    """Краткое описание сети для GET /auth/me; plan_status — заглушка до E2.3."""
    tenant_id: int | None = None
    if staff is not None and staff.tenant_owner_id is not None:
        tenant_id = int(staff.tenant_owner_id)
    else:
        org = await db.get(Organization, int(active_organization_id))
        if org is None or org.tenant_id is None:
            return None
        tenant_id = int(org.tenant_id)

    t = await db.get(Tenant, tenant_id)
    if t is None:
        return None
    return {
        "id": int(t.id),
        "name": str(t.name),
        "plan": str(t.plan),
        "plan_status": "active",
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
