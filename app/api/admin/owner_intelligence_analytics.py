"""Owner Intelligence analytics endpoints: Menu Profit Lab, Network Benchmark."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin.deps import (
    _session_is_superadmin,
    _session_staff_user,
    admin_org_from_session,
    require_admin_session_active,
)
from app.db.session import get_db
from app.services.menu_profit_lab import build_menu_profit_report
from app.services.network_benchmark import build_network_benchmark
from app.services.network_weekly_report import build_network_weekly_report
from app.services.tenant_scope import allowed_location_ids_for_staff

router = APIRouter(
    prefix="/admin/owner-intelligence",
    tags=["Owner Intelligence"],
    dependencies=[Depends(require_admin_session_active)],
)


async def _resolve_location_scope(
    request: Request,
    db: AsyncSession,
    org_id: int,
    location_id: int | None,
) -> set[int] | None:
    staff = await _session_staff_user(request, db)
    is_super = await _session_is_superadmin(request, db)
    allowed = await allowed_location_ids_for_staff(
        db,
        staff=staff,
        org_id=org_id,
        is_superadmin=is_super,
    )
    if location_id is not None and allowed is not None and int(location_id) not in allowed:
        raise HTTPException(status_code=403, detail="Location is not allowed")
    return allowed


@router.get("/menu-profit")
async def owner_intelligence_menu_profit(
    request: Request,
    period: Annotated[str, Query(pattern="^(today|7d|30d)$")] = "7d",
    location_id: Annotated[int | None, Query(ge=1)] = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
    org_id = admin_org_from_session(request)
    allowed = await _resolve_location_scope(request, db, org_id, location_id)
    report = await build_menu_profit_report(
        db,
        org_id,
        location_id=location_id,
        period=period,
        allowed_location_ids=allowed,
    )
    return {"ok": True, "organization_id": org_id, **report}


@router.get("/network-benchmark")
async def owner_intelligence_network_benchmark(
    request: Request,
    period: Annotated[str, Query(pattern="^(today|7d|30d)$")] = "7d",
    db: AsyncSession = Depends(get_db),
) -> dict:
    org_id = admin_org_from_session(request)
    staff = await _session_staff_user(request, db)
    is_super = await _session_is_superadmin(request, db)
    allowed = await allowed_location_ids_for_staff(
        db,
        staff=staff,
        org_id=org_id,
        is_superadmin=is_super,
    )
    result = await build_network_benchmark(
        db,
        org_id,
        period=period,
        allowed_location_ids=allowed,
    )
    return {"ok": True, "organization_id": org_id, **result}


@router.get("/network-benchmark/weekly")
async def owner_intelligence_network_weekly_report(
    request: Request,
    period: Annotated[str, Query(pattern="^(today|7d|30d)$")] = "7d",
    db: AsyncSession = Depends(get_db),
) -> dict:
    org_id = admin_org_from_session(request)
    staff = await _session_staff_user(request, db)
    is_super = await _session_is_superadmin(request, db)
    allowed = await allowed_location_ids_for_staff(
        db,
        staff=staff,
        org_id=org_id,
        is_superadmin=is_super,
    )
    result = await build_network_weekly_report(
        db,
        org_id,
        period=period,
        allowed_location_ids=allowed,
    )
    return {"ok": True, "organization_id": org_id, **result}
