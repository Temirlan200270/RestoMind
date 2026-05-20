"""Бронирования админки (E0.1: вынесено из _monolith)."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin.deps import (
    _session_is_superadmin,
    _session_staff_user,
    admin_org_from_session,
    require_admin_session_active,
)
from app.db.models import Booking, Order, User
from app.db.session import get_db
from app.services.booking_halls import BOOKING_HALL_KEYS, BOOKING_HALL_VIP, vip_slot_occupied
from app.services.tenant_scope import allowed_location_ids_for_staff, bookings_location_filter

bookings_router = APIRouter(dependencies=[Depends(require_admin_session_active)])

BOOKING_STATUS_KEYS = frozenset({"draft", "pending", "confirmed", "cancelled"})


class BookingPatch(BaseModel):
    """Частичное обновление брони: зал и/или статус."""

    hall: str | None = Field(default=None, description="hall_1 | hall_2 | vip")
    status: str | None = Field(default=None, description="draft | pending | confirmed | cancelled")


@bookings_router.get("/bookings")
async def list_bookings(
    request: Request,
    status: str | None = Query(None, description="Фильтр по статусу (pending, confirmed, cancelled)"),
    q: str | None = Query(None, description="Поиск по телефону клиента"),
    date_from: date | None = Query(None, description="Дата брони от (включительно, YYYY-MM-DD)"),
    date_to: date | None = Query(None, description="Дата брони до (включительно, YYYY-MM-DD)"),
    location_id: int | None = Query(None, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Список бронирований."""
    if date_from is not None and date_to is not None and date_from > date_to:
        raise HTTPException(status_code=400, detail="date_from не может быть позже date_to")

    org_id = admin_org_from_session(request)
    allowed_location_ids = await allowed_location_ids_for_staff(
        db,
        org_id=org_id,
        staff=_session_staff_user(request),
        is_superadmin=_session_is_superadmin(request),
        is_demo=False,
    )
    if location_id is not None and allowed_location_ids is not None and int(location_id) not in allowed_location_ids:
        raise HTTPException(status_code=403, detail="location_forbidden")
    order_cols = (
        (Booking.booking_date.asc(), Booking.booking_time.asc())
        if date_from is not None or date_to is not None
        else (Booking.created_at.desc(),)
    )
    query = (
        select(Booking, User.phone, User.name, Order.id)
        .join(User, Booking.user_id == User.id)
        .outerjoin(Order, Order.booking_id == Booking.id)
        .where(User.organization_id == org_id, bookings_location_filter(allowed_location_ids, location_id))
        .order_by(*order_cols)
    )
    if status:
        query = query.where(Booking.status == status)
    if date_from is not None:
        query = query.where(Booking.booking_date >= date_from)
    if date_to is not None:
        query = query.where(Booking.booking_date <= date_to)
    if q and q.strip():
        query = query.where(User.phone.ilike(f"%{q.strip()}%"))
    query = query.limit(limit)

    result = await db.execute(query)
    rows = result.all()

    return {
        "count": len(rows),
        "bookings": [
            {
                "id": b.id,
                "user_id": b.user_id,
                "user_phone": phone,
                "user_name": name,
                "date": b.booking_date.isoformat(),
                "time": b.booking_time.isoformat(),
                "guests": b.guests,
                "hall": b.hall,
                "comment": b.comment,
                "status": b.status,
                "location_id": b.location_id,
                "created_at": b.created_at.isoformat() if b.created_at else None,
                "linked_order_id": linked_oid,
            }
            for b, phone, name, linked_oid in rows
        ],
    }


@bookings_router.patch("/bookings/{booking_id}")
async def patch_booking(
    request: Request,
    booking_id: int,
    body: BookingPatch,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Обновить зал и/или статус.
    VIP — не более одной активной брони на дату+время (кроме отменённых).
    """
    if body.hall is None and body.status is None:
        raise HTTPException(status_code=400, detail="Укажите поле hall и/или status")

    org_id = admin_org_from_session(request)
    result = await db.execute(
        select(Booking)
        .join(User, User.id == Booking.user_id)
        .where(Booking.id == booking_id, User.organization_id == org_id),
    )
    booking = result.scalar_one_or_none()
    if not booking:
        raise HTTPException(status_code=404, detail="Бронирование не найдено")

    if body.hall is not None:
        h = (body.hall or "").strip()
        if h not in BOOKING_HALL_KEYS:
            raise HTTPException(status_code=400, detail="Недопустимый зал (ожидается hall_1, hall_2 или vip)")
        booking.hall = h

    if body.status is not None:
        st = (body.status or "").strip()
        if st not in BOOKING_STATUS_KEYS:
            raise HTTPException(
                status_code=400,
                detail="Недопустимый статус (draft, pending, confirmed, cancelled)",
            )
        booking.status = st

    if booking.hall == BOOKING_HALL_VIP and booking.status != "cancelled":
        if await vip_slot_occupied(
            db, booking.booking_date, booking.booking_time, booking.id,
        ):
            raise HTTPException(
                status_code=409,
                detail="VIP зал на это время уже занят — выберите другое время или другой зал",
            )

    await db.commit()
    return {
        "status": "ok",
        "id": booking_id,
        "hall": booking.hall,
        "booking_status": booking.status,
    }
