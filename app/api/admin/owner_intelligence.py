"""Owner Intelligence admin API — сводка для владельца."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin.deps import (
    _session_is_superadmin,
    _session_staff_user,
    admin_org_from_session,
    require_admin_session_active,
)
from app.db.session import get_db
from app.services.owner_digest_delivery import (
    list_digest_history,
    preview_weekly_digest,
    send_weekly_digest,
)
from app.services.owner_intelligence import build_owner_intelligence_summary
from app.services.tenant_scope import allowed_location_ids_for_staff


class OwnerDigestSendBody(BaseModel):
    force: bool = Field(default=False, description="Обойти 30-мин cooldown ручной отправки")

router = APIRouter(
    prefix="/admin/owner-intelligence",
    tags=["Owner Intelligence"],
    dependencies=[Depends(require_admin_session_active)],
)


@router.get("/summary")
async def owner_intelligence_summary(
    request: Request,
    db: AsyncSession = Depends(get_db),
    period: str = Query("today", description="Окно: today | 7d | 30d"),
    location_id: Annotated[int | None, Query(ge=1)] = None,
) -> dict:
    org_id = admin_org_from_session(request)
    staff = await _session_staff_user(request, db)
    is_super = await _session_is_superadmin(request, db)
    allowed_location_ids = await allowed_location_ids_for_staff(
        db,
        staff=staff,
        org_id=org_id,
        is_superadmin=is_super,
    )
    if location_id is not None and allowed_location_ids is not None and int(location_id) not in allowed_location_ids:
        raise HTTPException(status_code=403, detail="Location is not allowed")

    summary = await build_owner_intelligence_summary(
        db,
        org_id,
        location_id=location_id,
        period=period,
        allowed_location_ids=allowed_location_ids,
    )
    location_scoped = bool(location_id is not None or allowed_location_ids is not None)
    return {
        "ok": True,
        "organization_id": org_id,
        **summary,
        "location_scope": {
            "location_id": int(location_id) if location_id is not None else None,
            "source": "sql_location" if location_scoped else "org",
        },
    }


@router.get("/digest/preview")
async def owner_digest_preview(
    request: Request,
    db: AsyncSession = Depends(get_db),
    period: str = Query("prev_week", description="Окно: prev_week"),
) -> dict:
    org_id = admin_org_from_session(request)
    payload = await preview_weekly_digest(db, org_id, period=period)
    history = await list_digest_history(db, org_id, limit=1)
    last_sent = next((row for row in history if row.get("success")), None)
    return {
        **payload,
        "last_sent": last_sent,
    }


@router.post("/digest/send")
async def owner_digest_send(
    request: Request,
    body: OwnerDigestSendBody,
    db: AsyncSession = Depends(get_db),
) -> dict:
    org_id = admin_org_from_session(request)
    result = await send_weekly_digest(
        db,
        org_id,
        force=bool(body.force),
        channel="telegram",
        triggered_by="admin",
    )
    if result.get("skipped") and result.get("skip_reason") == "manual_cooldown":
        raise HTTPException(
            status_code=429,
            detail="Повторная отправка возможна через 30 минут или с force=true",
        )
    if not result.get("ok") and result.get("error") == "telegram_not_configured":
        raise HTTPException(status_code=503, detail="Telegram не настроен для организации")
    if not result.get("ok") and result.get("error") == "send_failed":
        raise HTTPException(status_code=502, detail="Не удалось отправить дайджест в Telegram")
    return {"ok": True, **result}


@router.get("/digest/history")
async def owner_digest_history(
    request: Request,
    db: AsyncSession = Depends(get_db),
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> dict:
    org_id = admin_org_from_session(request)
    items = await list_digest_history(db, org_id, limit=limit)
    return {"ok": True, "organization_id": org_id, "items": items}
