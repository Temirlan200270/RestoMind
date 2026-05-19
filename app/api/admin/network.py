"""Network / Franchise API — Phase 1 OS.

Доступен только при Tenant.is_network = True.
Агрегированная аналитика «Вся сеть», список филиалов, переключение контекста.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Order, OrderStatus, Organization, Tenant
from app.db.session import get_db
from app.services.tenant_scope import _tenant_org_list, orders_tenant_clause

from .deps import admin_org_from_session, require_admin_session_active

logger = logging.getLogger(__name__)

network_router = APIRouter(
    prefix="/admin/network",
    tags=["Network (Franchise)"],
    dependencies=[Depends(require_admin_session_active)],
)


async def _require_network_tenant(request: Request, db: AsyncSession) -> Tenant:
    """Проверяет что у текущей org включён режим сети; возвращает Tenant."""
    org_id = admin_org_from_session(request)
    org = await db.get(Organization, int(org_id))
    if org is None or org.tenant_id is None:
        raise HTTPException(status_code=403, detail="Функция доступна только для сетевых аккаунтов.")
    tenant = await db.get(Tenant, int(org.tenant_id))
    if tenant is None or not bool(getattr(tenant, "is_network", False)):
        raise HTTPException(
            status_code=403,
            detail="Режим сети не включён. Обратитесь к администратору платформы.",
        )
    return tenant


async def _get_tenant_org_ids(db: AsyncSession, tenant_id: int) -> list[int]:
    return [int(o.id) for o in await _tenant_org_list(db, tenant_id)]


@network_router.get("/orgs")
async def network_orgs(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    """Список всех активных филиалов сети для Branch Switcher."""
    tenant = await _require_network_tenant(request, db)
    org_ids = await _get_tenant_org_ids(db, tenant.id)
    orgs = (await db.execute(
        select(Organization.id, Organization.name, Organization.slug)
        .where(Organization.id.in_(org_ids))
        .order_by(Organization.name)
    )).all()
    return {
        "ok": True,
        "tenant_id": tenant.id,
        "tenant_name": tenant.name,
        "orgs": [{"id": int(r.id), "name": r.name, "slug": r.slug} for r in orgs],
        "count": len(orgs),
    }


@network_router.get("/stats")
async def network_stats(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    """Агрегированная аналитика по всей сети за сегодня (UTC).

    Только SUM/COUNT агрегаты — не возвращает сырые данные отдельных филиалов.
    Мультитенантная изоляция: запрос строго по org_ids тенанта.
    """
    from datetime import datetime, timedelta, timezone
    tenant = await _require_network_tenant(request, db)
    org_ids = await _get_tenant_org_ids(db, tenant.id)
    if not org_ids:
        return {"ok": True, "tenant_id": tenant.id, "orgs_count": 0, "today": {}}

    now_utc = datetime.now(tz=timezone.utc)
    today_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)

    not_cancelled = Order.status != OrderStatus.CANCELLED.value

    # Сегодня по всей сети
    today_row = (await db.execute(
        select(
            func.count(Order.id),
            func.coalesce(func.sum(Order.total_price), 0),
        ).where(
            not_cancelled,
            Order.organization_id.in_(org_ids),
            Order.created_at >= today_start,
        )
    )).one()

    # Всего по всей сети
    total_row = (await db.execute(
        select(
            func.count(Order.id),
            func.coalesce(func.sum(Order.total_price), 0),
        ).where(
            not_cancelled,
            Order.organization_id.in_(org_ids),
        )
    )).one()

    # Статистика по филиалам за сегодня
    per_org: list[dict[str, Any]] = []
    for oid in org_ids:
        clause = orders_tenant_clause(oid)
        row = (await db.execute(
            select(
                func.count(Order.id),
                func.coalesce(func.sum(Order.total_price), 0),
            ).where(
                not_cancelled,
                Order.organization_id == oid,
                Order.created_at >= today_start,
            )
        )).one()
        per_org.append({
            "organization_id": oid,
            "today_orders": int(row[0]),
            "today_revenue": float(row[1]),
        })

    return {
        "ok": True,
        "tenant_id": tenant.id,
        "tenant_name": tenant.name,
        "orgs_count": len(org_ids),
        "network": {
            "today_orders": int(today_row[0]),
            "today_revenue": float(today_row[1]),
            "total_orders": int(total_row[0]),
            "total_revenue": float(total_row[1]),
        },
        "per_org": per_org,
    }


@network_router.post("/switch/{org_id}")
async def network_switch_org(
    org_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Переключиться в контекст конкретного филиала сети.

    Проверяет принадлежность org к тенанту (защита от cross-tenant switch).
    После переключения возвращает обновлённый /auth/me payload.
    """
    tenant = await _require_network_tenant(request, db)
    org_ids = await _get_tenant_org_ids(db, tenant.id)
    if org_id not in org_ids:
        raise HTTPException(
            status_code=403,
            detail="Филиал не принадлежит вашей сети или недоступен.",
        )
    request.session["organization_id"] = org_id
    return {"ok": True, "switched_to": org_id, "tenant_id": tenant.id}
