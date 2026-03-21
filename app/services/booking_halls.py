"""
Залы для бронирований и проверка занятости VIP (один столик на слот дата+время).
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Booking

BOOKING_HALL_1 = "hall_1"
BOOKING_HALL_2 = "hall_2"
BOOKING_HALL_VIP = "vip"

BOOKING_HALL_KEYS: tuple[str, ...] = (BOOKING_HALL_1, BOOKING_HALL_2, BOOKING_HALL_VIP)

HALL_LABEL_RU: dict[str, str] = {
    BOOKING_HALL_1: "Зал 1",
    BOOKING_HALL_2: "Зал 2",
    BOOKING_HALL_VIP: "VIP зал",
}


def normalize_hall_key(value: str | None) -> str:
    """Приводит значение к одному из ключей BOOKING_HALL_* или к Зал 1 по умолчанию."""
    v = (value or "").strip().lower()
    if v in BOOKING_HALL_KEYS:
        return v
    return BOOKING_HALL_1


async def vip_slot_occupied(
    db: AsyncSession,
    booking_date,
    booking_time,
    exclude_booking_id: int | None = None,
) -> bool:
    """
    True, если VIP-зал уже занят на эту дату и время
    (любая бронь кроме отменённой).
    """
    q = (
        select(func.count(Booking.id))
        .select_from(Booking)
        .where(
            Booking.hall == BOOKING_HALL_VIP,
            Booking.booking_date == booking_date,
            Booking.booking_time == booking_time,
            Booking.status != "cancelled",
        )
    )
    if exclude_booking_id is not None:
        q = q.where(Booking.id != exclude_booking_id)
    n = await db.scalar(q)
    return (n or 0) > 0
