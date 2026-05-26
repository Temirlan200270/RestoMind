"""Owner Intelligence — QA auto-audit API for risky AI orders."""

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
from app.services.order_ai_audit import (
    audit_public,
    list_order_ai_audits,
    mark_order_ai_audit_status,
    summarize_order_ai_audits,
)
from app.services.tenant_scope import allowed_location_ids_for_staff

router = APIRouter(
    prefix="/admin/owner-intelligence",
    tags=["Owner Intelligence"],
    dependencies=[Depends(require_admin_session_active)],
)


class OrderAuditReviewBody(BaseModel):
    review_reason: str | None = Field(
        default=None,
        description="no_error | fixed | escalated_to_manager",
        max_length=32,
    )


async def _location_scope_for_request(
    request: Request,
    db: AsyncSession,
    org_id: int,
    location_id: int | None,
) -> tuple[set[int] | None, bool]:
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
    return allowed, bool(location_id is not None or allowed is not None)


def _audit_filter_kwargs(
    *,
    status: str,
    period: str,
    risk_level: str | None,
    tags: str | None,
    unreviewed_only: bool,
    location_id: int | None,
    order_id: int | None,
    allowed_location_ids: set[int] | None,
) -> dict:
    return {
        "status": status,
        "period": period,
        "risk_level": risk_level,
        "tags": tags,
        "unreviewed_only": unreviewed_only,
        "location_id": location_id,
        "order_id": order_id,
        "allowed_location_ids": allowed_location_ids,
    }


@router.get("/order-audits/summary")
async def get_order_ai_audits_summary(
    request: Request,
    db: AsyncSession = Depends(get_db),
    status: str = Query("open", description="open | reviewed | dismissed | resolved | all"),
    period: str = Query("today", description="today | week | 7d"),
    risk_level: str | None = Query(
        None,
        description="Filter by risk level: high,critical (comma-separated)",
    ),
    tags: str | None = Query(
        None,
        description="Filter by tags: stoplist_conflict,wrong_address_risk (comma-separated)",
    ),
    unreviewed_only: bool = Query(False, description="Only open/unreviewed audits"),
    location_id: Annotated[int | None, Query(ge=1)] = None,
    order_id: Annotated[int | None, Query(ge=1)] = None,
) -> dict:
    org_id = admin_org_from_session(request)
    allowed_location_ids, location_scoped = await _location_scope_for_request(
        request,
        db,
        org_id,
        location_id,
    )
    summary = await summarize_order_ai_audits(
        db,
        org_id,
        **_audit_filter_kwargs(
            status=status,
            period=period,
            risk_level=risk_level,
            tags=tags,
            unreviewed_only=unreviewed_only,
            location_id=location_id,
            order_id=order_id,
            allowed_location_ids=allowed_location_ids,
        ),
    )
    return {
        "ok": True,
        "organization_id": org_id,
        **summary,
        "location_scope": {
            "location_id": int(location_id) if location_id is not None else None,
            "source": "sql_location" if location_scoped else "org",
        },
    }


@router.get("/order-audits")
async def get_order_ai_audits(
    request: Request,
    db: AsyncSession = Depends(get_db),
    status: str = Query("open", description="open | reviewed | dismissed | resolved | all"),
    period: str = Query("today", description="today | week | 7d"),
    risk_level: str | None = Query(
        None,
        description="Filter by risk level: high,critical (comma-separated)",
    ),
    tags: str | None = Query(
        None,
        description="Filter by tags: stoplist_conflict,wrong_address_risk (comma-separated)",
    ),
    unreviewed_only: bool = Query(False, description="Only open/unreviewed audits"),
    location_id: Annotated[int | None, Query(ge=1)] = None,
    order_id: Annotated[int | None, Query(ge=1)] = None,
    limit: int = Query(50, ge=1, le=200),
) -> dict:
    org_id = admin_org_from_session(request)
    allowed_location_ids, location_scoped = await _location_scope_for_request(
        request,
        db,
        org_id,
        location_id,
    )
    rows = await list_order_ai_audits(
        db,
        org_id,
        **_audit_filter_kwargs(
            status=status,
            period=period,
            risk_level=risk_level,
            tags=tags,
            unreviewed_only=unreviewed_only,
            location_id=location_id,
            order_id=order_id,
            allowed_location_ids=allowed_location_ids,
        ),
        limit=limit,
    )
    return {
        "ok": True,
        "organization_id": org_id,
        "items": [audit_public(r) for r in rows],
        "count": len(rows),
        "location_scope": {
            "location_id": int(location_id) if location_id is not None else None,
            "source": "sql_location" if location_scoped else "org",
        },
    }


@router.post("/order-audits/{audit_id}/review")
async def review_order_ai_audit(
    audit_id: int,
    request: Request,
    body: OrderAuditReviewBody | None = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
    org_id = admin_org_from_session(request)
    staff = await _session_staff_user(request, db)
    staff_id = int(staff.id) if staff is not None else None
    review_reason = body.review_reason if body is not None else None
    try:
        row = await mark_order_ai_audit_status(
            db,
            audit_id,
            org_id,
            "reviewed",
            staff_id,
            review_reason=review_reason,
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="Audit not found") from None
    except ValueError as exc:
        code = str(exc)
        if code.startswith("invalid_transition:"):
            raise HTTPException(status_code=409, detail="Status transition not allowed") from None
        if code.startswith("invalid_review_reason:"):
            raise HTTPException(status_code=422, detail="Invalid review reason") from None
        raise HTTPException(status_code=400, detail="Invalid audit status") from None
    await db.commit()
    return {"ok": True, "item": audit_public(row)}


@router.post("/order-audits/{audit_id}/dismiss")
async def dismiss_order_ai_audit(
    audit_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    org_id = admin_org_from_session(request)
    staff = await _session_staff_user(request, db)
    staff_id = int(staff.id) if staff is not None else None
    try:
        row = await mark_order_ai_audit_status(
            db,
            audit_id,
            org_id,
            "dismissed",
            staff_id,
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="Audit not found") from None
    except ValueError as exc:
        code = str(exc)
        if code.startswith("invalid_transition:"):
            raise HTTPException(status_code=409, detail="Status transition not allowed") from None
        raise HTTPException(status_code=400, detail="Invalid audit status") from None
    await db.commit()
    return {"ok": True, "item": audit_public(row)}


@router.post("/order-audits/backfill")
async def backfill_order_ai_audits_endpoint(
    request: Request,
    db: AsyncSession = Depends(get_db),
    period: str = Query("today", description="today | week | 7d"),
    location_id: Annotated[int | None, Query(ge=1)] = None,
    limit: int = Query(200, ge=1, le=500),
) -> dict:
    """Пересчитать QA-аудиты для подтверждённых заказов за период."""
    from app.services.order_ai_audit import backfill_order_ai_audits

    org_id = admin_org_from_session(request)
    allowed_location_ids, _ = await _location_scope_for_request(
        request, db, org_id, location_id,
    )
    stats = await backfill_order_ai_audits(
        db,
        org_id,
        period=period,
        location_id=location_id,
        allowed_location_ids=allowed_location_ids,
        limit=limit,
    )
    await db.commit()
    return {"ok": True, "organization_id": org_id, **stats}
