"""Поиск User по телефону с учётом legacy-форматов (7705… vs +7705…)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User
from app.services.phone_normalize import canonical_user_phone, phone_lookup_variants


async def find_user_by_phone(
    db: AsyncSession,
    organization_id: int,
    phone: str,
) -> User | None:
    """Находит пользователя по exact или legacy-вариантам номера."""
    variants = phone_lookup_variants(phone)
    if not variants:
        return None
    rows = (
        await db.scalars(
            select(User).where(
                User.organization_id == int(organization_id),
                User.phone.in_(variants),
            ),
        )
    ).all()
    if not rows:
        return None
    if len(rows) == 1:
        return rows[0]
    canon = canonical_user_phone(phone)
    for row in rows:
        if row.phone == canon:
            return row
    return min(rows, key=lambda u: int(u.id or 0))


async def ensure_user_phone_canonical(user: User, phone: str) -> bool:
    """Обновляет users.phone на E.164 если нашли legacy-запись. Возвращает True если изменили."""
    canon = canonical_user_phone(phone)
    if not canon or user.phone == canon:
        return False
    user.phone = canon
    return True
