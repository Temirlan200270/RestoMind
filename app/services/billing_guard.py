"""Проверки блокировки tenant по биллингу (E2.3)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Organization, Tenant


def tenant_is_billing_suspended(tenant: Tenant | None) -> bool:
    if tenant is None:
        return False
    return (tenant.plan_status or "").strip().lower() == "suspended"


async def load_tenant_for_organization(db: AsyncSession, organization_id: int) -> Tenant | None:
    org = await db.get(Organization, int(organization_id))
    if org is None or org.tenant_id is None:
        return None
    return await db.get(Tenant, int(org.tenant_id))


async def tenant_billing_blocks_inbound(db: AsyncSession, org: Organization) -> bool:
    """Блок входящих каналов (WhatsApp) для филиала при suspended у tenant."""
    if org.tenant_id is None:
        return False
    tenant = await db.get(Tenant, int(org.tenant_id))
    return tenant_is_billing_suspended(tenant)
