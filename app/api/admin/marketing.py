"""Marketing blasts and loyalty admin API."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin.deps import admin_org_from_session, require_admin_session_active
from app.db.session import get_db

router = APIRouter(
    prefix="/admin/marketing",
    tags=["Marketing"],
    dependencies=[Depends(require_admin_session_active)],
)

loyalty_router = APIRouter(
    prefix="/admin/loyalty",
    tags=["Loyalty"],
    dependencies=[Depends(require_admin_session_active)],
)


# ─── Marketing blasts ────────────────────────────────────────────────────────


class BlastCreateBody(BaseModel):
    name: str = Field(..., min_length=2, max_length=255)
    segment_type: str = Field(..., pattern="^(inactive_30d|frequent|all_active|custom)$")
    message_text: str = Field(..., min_length=5, max_length=4000)
    template_name: str | None = Field(default=None, max_length=120)
    scheduled_for: datetime | None = Field(default=None)

    @field_validator("template_name", "scheduled_for", mode="before")
    @classmethod
    def empty_str_to_none(cls, v: Any) -> Any:
        if v == "":
            return None
        return v


def _blast_public(blast: object) -> dict:
    return {
        "id": blast.id,
        "name": blast.name,
        "segment_type": blast.segment_type,
        "message_text": blast.message_text,
        "template_name": blast.template_name,
        "status": blast.status,
        "total_recipients": blast.total_recipients,
        "sent_count": blast.sent_count,
        "failed_count": blast.failed_count,
        "scheduled_for": blast.scheduled_for.isoformat() if blast.scheduled_for else None,
        "sent_at": blast.sent_at.isoformat() if blast.sent_at else None,
        "created_at": blast.created_at.isoformat() if blast.created_at else None,
    }


@router.get("/blasts")
async def list_blasts(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    from app.db.models import MarketingBlast
    org_id = admin_org_from_session(request)
    rows = (await db.execute(
        select(MarketingBlast)
        .where(MarketingBlast.organization_id == org_id)
        .order_by(MarketingBlast.created_at.desc())
        .limit(50)
    )).scalars().all()
    return {"items": [_blast_public(r) for r in rows]}


@router.post("/blasts")
async def create_blast(
    body: BlastCreateBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    from app.services.marketing import create_blast
    org_id = admin_org_from_session(request)
    blast = await create_blast(
        db, org_id,
        name=body.name,
        segment_type=body.segment_type,
        message_text=body.message_text,
        template_name=body.template_name,
        scheduled_for=body.scheduled_for,
    )
    return {"ok": True, "item": _blast_public(blast)}


@router.get("/blasts/{blast_id}")
async def get_blast(
    blast_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    from app.db.models import MarketingBlast
    org_id = admin_org_from_session(request)
    blast = await db.get(MarketingBlast, blast_id)
    if blast is None or int(blast.organization_id) != int(org_id):
        raise HTTPException(status_code=404, detail="Blast not found")
    return {"item": _blast_public(blast)}


@router.post("/blasts/{blast_id}/send")
async def send_blast(
    blast_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> dict:
    from app.db.models import MarketingBlast
    from app.services.marketing import run_send_blast_batch
    from app.services.task_queue import TaskQueueEnqueueError, arq_can_run, enqueue_job
    org_id = admin_org_from_session(request)
    blast = await db.get(MarketingBlast, blast_id)
    if blast is None or int(blast.organization_id) != int(org_id):
        raise HTTPException(status_code=404, detail="Blast not found")
    if blast.status not in ("draft",):
        raise HTTPException(status_code=400, detail=f"Рассылка уже в статусе «{blast.status}»")
    if arq_can_run():
        try:
            await enqueue_job("send_blast_batch", blast_id=blast_id)
            return {"ok": True, "blast_id": blast_id, "mode": "arq"}
        except TaskQueueEnqueueError:
            pass
    background_tasks.add_task(run_send_blast_batch, blast_id=blast_id)
    return {"ok": True, "blast_id": blast_id, "mode": "background"}


@router.post("/blasts/{blast_id}/cancel")
async def cancel_blast(
    blast_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    from app.db.models import MarketingBlast
    org_id = admin_org_from_session(request)
    blast = await db.get(MarketingBlast, blast_id)
    if blast is None or int(blast.organization_id) != int(org_id):
        raise HTTPException(status_code=404, detail="Blast not found")
    if blast.status not in ("draft", "sending"):
        raise HTTPException(status_code=400, detail=f"Нельзя отменить рассылку в статусе «{blast.status}»")
    blast.status = "cancelled"
    await db.commit()
    return {"ok": True}


@router.delete("/blasts/{blast_id}")
async def delete_blast(
    blast_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    from sqlalchemy import delete as sa_delete

    from app.db.models import MarketingBlast, MarketingBlastRecipient
    org_id = admin_org_from_session(request)
    blast = await db.get(MarketingBlast, blast_id)
    if blast is None or int(blast.organization_id) != int(org_id):
        raise HTTPException(status_code=404, detail="Blast not found")
    if blast.status == "sending":
        raise HTTPException(status_code=400, detail="Рассылка отправляется — сначала отмените её")
    # Только Core DELETE: ORM delete(MarketingBlast) мог выставлять blast_id=NULL у recipients во flush.
    await db.execute(sa_delete(MarketingBlastRecipient).where(MarketingBlastRecipient.blast_id == blast_id))
    await db.execute(
        sa_delete(MarketingBlast).where(
            MarketingBlast.id == blast_id,
            MarketingBlast.organization_id == org_id,
        ),
    )
    await db.commit()
    return {"ok": True}


@router.get("/segment-preview/{segment_type}")
async def segment_preview(
    segment_type: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    from app.services.marketing import get_segment_phones
    if segment_type not in ("inactive_30d", "frequent", "all_active"):
        raise HTTPException(status_code=400, detail="Неизвестный сегмент")
    org_id = admin_org_from_session(request)
    phones = await get_segment_phones(db, org_id, segment_type)
    return {"segment_type": segment_type, "count": len(phones)}


# ─── Loyalty ─────────────────────────────────────────────────────────────────


class LoyaltyAdjustBody(BaseModel):
    phone: str = Field(..., min_length=7, max_length=20)
    points: int = Field(..., description=">0 начислить, <0 списать")
    note: str | None = Field(default=None, max_length=512)


@loyalty_router.get("/transactions")
async def loyalty_transactions(
    request: Request,
    phone: str = Query(...),
    db: AsyncSession = Depends(get_db),
) -> dict:
    from app.db.models import LoyaltyTransaction, User
    from sqlalchemy import select as sa_select
    org_id = admin_org_from_session(request)
    user = await db.scalar(
        sa_select(User).where(User.organization_id == org_id, User.phone == phone)
    )
    if user is None:
        raise HTTPException(status_code=404, detail="Клиент не найден")
    txs = (await db.execute(
        sa_select(LoyaltyTransaction)
        .where(LoyaltyTransaction.organization_id == org_id, LoyaltyTransaction.user_id == user.id)
        .order_by(LoyaltyTransaction.created_at.desc())
        .limit(50)
    )).scalars().all()
    return {
        "phone": phone,
        "balance": sum(t.points for t in txs) if txs else 0,
        "transactions": [
            {
                "id": t.id, "points": t.points, "kind": t.kind,
                "note": t.note, "order_id": t.order_id,
                "created_at": t.created_at.isoformat() if t.created_at else None,
            }
            for t in txs
        ],
    }


@loyalty_router.post("/adjust")
async def loyalty_adjust(
    body: LoyaltyAdjustBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    from app.db.models import User
    from app.services.loyalty import add_points
    from sqlalchemy import select as sa_select
    org_id = admin_org_from_session(request)
    user = await db.scalar(
        sa_select(User).where(User.organization_id == org_id, User.phone == body.phone)
    )
    if user is None:
        raise HTTPException(status_code=404, detail="Клиент не найден")
    new_balance = await add_points(
        db, org_id, user.id, body.points,
        kind="adjust", note=body.note,
    )
    await db.commit()
    return {"ok": True, "phone": body.phone, "new_balance": new_balance}
