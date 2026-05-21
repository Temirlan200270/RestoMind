"""Admin API: синхронизация остатков из iiko Office."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin.deps import (
    admin_org_from_session,
    require_admin_session_active,
    require_staff_manager_or_admin,
)
from app.db.session import get_db
from app.services.integration_config import iiko_effective_configured
from app.services.integration_health import build_inventory_sync_status, record_inventory_sync
from app.services.iiko_sync_tasks import run_inventory_sync
from app.services.org_iiko_office import org_has_iiko_office_in_db, resolve_org_iiko_office_credentials
from app.db.models import Organization

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/admin",
    tags=["Inventory"],
    dependencies=[Depends(require_admin_session_active)],
)


@router.post("/inventory/sync-iiko")
async def post_inventory_sync_iiko(
    request: Request,
    _perm: None = Depends(require_staff_manager_or_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Ручной запуск синхронизации остатков iiko Office → inventory_stock_snapshots."""
    org_id = admin_org_from_session(request)
    org = await db.get(Organization, org_id)
    if not org_has_iiko_office_in_db(org):
        raise HTTPException(
            status_code=400,
            detail="iiko Office не настроен: заполните integration_config_json.iiko_office (host, login, password_enc, store_id)",
        )
    result = await run_inventory_sync(org_id)
    if not result.get("ok"):
        raise HTTPException(
            status_code=502,
            detail=result.get("error") or "Ошибка синхронизации остатков iiko Office",
        )
    return {"ok": True, "status": "ok", **(result.get("stats") or {})}


@router.get("/inventory/sync-status")
async def get_inventory_sync_status(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Статус последней синхронизации остатков iiko Office для активного филиала."""
    org_id = admin_org_from_session(request)
    org = await db.get(Organization, org_id)
    creds = await resolve_org_iiko_office_credentials(db, org_id)
    iiko_cloud_ok = await iiko_effective_configured(db, org_id)
    payload = await build_inventory_sync_status(
        db,
        organization_id=int(org_id),
        iiko_office_configured=creds is not None,
        iiko_cloud_configured=iiko_cloud_ok,
    )
    payload["iiko_office_host"] = (creds.host if creds else None) or None
    payload["store_id_configured"] = bool(creds and creds.store_id)
    if org is not None and not org_has_iiko_office_in_db(org):
        payload["config_hint"] = (
            "integration_config_json.iiko_office: host, login, password_enc, store_id"
        )
    return payload
