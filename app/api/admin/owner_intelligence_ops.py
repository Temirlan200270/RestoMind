"""Owner Intelligence ops API — Kitchen Gate v2 (STAGE 4)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin.deps import (
    _session_staff_user,
    admin_org_from_session,
    require_admin_session_active,
    require_staff_manager_or_admin,
)
from app.api.admin.intelligence import _location_scope_for_request
from app.db.models import Organization
from app.db.session import get_db
from app.services.operational_mode import (
    DELIVERY_MODE_NORMAL,
    DELIVERY_MODE_PAUSED,
    KITCHEN_LOAD_BUSY,
    KITCHEN_LOAD_NORMAL,
    KITCHEN_LOAD_OVERLOAD,
    VALID_EXPIRES_PRESETS,
    get_operational_mode,
    operational_mode_to_dict,
    resolve_expires_preset,
    set_operational_mode,
)
from app.services.system_events import emit_system_event

owner_intelligence_ops_router = APIRouter(
    prefix="/admin/owner-intelligence",
    tags=["Owner Intelligence"],
    dependencies=[Depends(require_admin_session_active)],
)

_KITCHEN_LOADS = {KITCHEN_LOAD_NORMAL, KITCHEN_LOAD_BUSY, KITCHEN_LOAD_OVERLOAD}
_DELIVERY_MODES = {DELIVERY_MODE_NORMAL, DELIVERY_MODE_PAUSED}


class KitchenGatePatchBody(BaseModel):
    kitchen_load: str | None = Field(default=None, max_length=16)
    prep_time_extra_min: int | None = Field(default=None, ge=0, le=180)
    delivery_mode: str | None = Field(default=None, max_length=16)
    force_pickup_only: bool | None = None
    reason: str | None = Field(default=None, max_length=500)
    expires_at: datetime | None = None
    expires_preset: str | None = Field(default=None, max_length=32)


@owner_intelligence_ops_router.get("/kitchen-gate")
async def owner_intel_kitchen_gate_get(
    request: Request,
    location_id: Annotated[int | None, Query(ge=1)] = None,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    org_id = admin_org_from_session(request)
    await _location_scope_for_request(request, db, org_id, location_id)
    mode = await get_operational_mode(db, org_id, location_id=location_id)
    return {"ok": True, "mode": operational_mode_to_dict(mode)}


@owner_intelligence_ops_router.patch("/kitchen-gate")
async def owner_intel_kitchen_gate_patch(
    request: Request,
    body: KitchenGatePatchBody,
    location_id: Annotated[int | None, Query(ge=1)] = None,
    db: AsyncSession = Depends(get_db),
    _perm: None = Depends(require_staff_manager_or_admin),
) -> dict[str, Any]:
    org_id = admin_org_from_session(request)
    await _location_scope_for_request(request, db, org_id, location_id)

    if body.kitchen_load is not None and body.kitchen_load not in _KITCHEN_LOADS:
        raise HTTPException(status_code=422, detail="Invalid kitchen_load")
    if body.delivery_mode is not None and body.delivery_mode not in _DELIVERY_MODES:
        raise HTTPException(status_code=422, detail="Invalid delivery_mode")

    org = await db.get(Organization, org_id)
    org_tz = getattr(org, "timezone", None) if org is not None else "UTC"

    staff = await _session_staff_user(request, db)
    patch_kwargs: dict[str, Any] = {
        "kitchen_load": body.kitchen_load,
        "prep_time_extra_min": body.prep_time_extra_min,
        "delivery_mode": body.delivery_mode,
        "force_pickup_only": body.force_pickup_only,
        "reason": body.reason,
        "updated_by_staff_id": int(staff.id) if staff is not None else None,
    }
    if body.expires_preset is not None:
        preset = body.expires_preset.strip().lower()
        if preset not in VALID_EXPIRES_PRESETS:
            raise HTTPException(status_code=422, detail="Invalid expires_preset")
        try:
            patch_kwargs["expires_at"] = resolve_expires_preset(org_tz or "UTC", preset)
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid expires_preset") from None
    elif "expires_at" in body.model_fields_set:
        patch_kwargs["expires_at"] = body.expires_at

    before, after = await set_operational_mode(
        db,
        org_id,
        location_id=location_id,
        **patch_kwargs,
    )

    await emit_system_event(
        db,
        organization_id=org_id,
        event_type="kitchen_gate.mode_changed",
        payload={
            "location_id": location_id,
            "before": operational_mode_to_dict(before),
            "after": operational_mode_to_dict(after),
        },
        entity_type="operational_mode",
        source="admin",
    )

    return {"ok": True, "mode": operational_mode_to_dict(after)}
