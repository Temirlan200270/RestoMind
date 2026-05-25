"""Merge legacy duplicate User rows (7705… vs +7705…) into one canonical E.164 record."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Booking,
    ChatLog,
    CustomerFeedback,
    LoyaltyBalance,
    LoyaltyTransaction,
    Order,
    User,
)
from app.services.phone_normalize import canonical_user_phone, phone_digits_key


@dataclass(frozen=True)
class DuplicatePhoneGroup:
    digits: str
    canonical_phone: str
    users: tuple[User, ...]


def _pick_canonical_user(users: list[User], canonical_phone: str) -> User:
    for user in users:
        if user.phone == canonical_phone:
            return user
    return min(users, key=lambda u: int(u.id or 0))


async def find_duplicate_phone_groups(
    db: AsyncSession,
    organization_id: int,
    *,
    phone_filter: str | None = None,
) -> list[DuplicatePhoneGroup]:
    rows = (
        await db.scalars(
            select(User).where(User.organization_id == int(organization_id)).order_by(User.id),
        )
    ).all()
    if phone_filter:
        needle = phone_digits_key(phone_filter)
        rows = [u for u in rows if phone_digits_key(u.phone) == needle]

    grouped: dict[str, list[User]] = defaultdict(list)
    for user in rows:
        key = phone_digits_key(user.phone)
        if key:
            grouped[key].append(user)

    result: list[DuplicatePhoneGroup] = []
    for digits, users in grouped.items():
        if len(users) < 2:
            continue
        canon = canonical_user_phone(f"+{digits}") or users[0].phone
        result.append(
            DuplicatePhoneGroup(
                digits=digits,
                canonical_phone=canon,
                users=tuple(sorted(users, key=lambda u: int(u.id or 0))),
            ),
        )
    return sorted(result, key=lambda g: g.digits)


async def merge_duplicate_users(
    db: AsyncSession,
    organization_id: int,
    *,
    phone_filter: str | None = None,
    dry_run: bool = True,
) -> list[dict[str, object]]:
    """Repoint FKs from duplicate users to canonical row; delete extras."""
    groups = await find_duplicate_phone_groups(db, organization_id, phone_filter=phone_filter)
    reports: list[dict[str, object]] = []

    for group in groups:
        users = list(group.users)
        keep = _pick_canonical_user(users, group.canonical_phone)
        drop_ids = [int(u.id) for u in users if int(u.id) != int(keep.id)]
        if not drop_ids:
            continue

        moved = {"chat_logs": 0, "orders": 0, "bookings": 0, "feedback": 0, "loyalty_tx": 0}
        keep_id = int(keep.id or 0)
        if not dry_run:
            if keep_id <= 0:
                continue
            if keep.phone != group.canonical_phone:
                keep.phone = group.canonical_phone

            for model, col, key in (
                (ChatLog, ChatLog.user_id, "chat_logs"),
                (Order, Order.user_id, "orders"),
                (Booking, Booking.user_id, "bookings"),
                (CustomerFeedback, CustomerFeedback.user_id, "feedback"),
                (LoyaltyTransaction, LoyaltyTransaction.user_id, "loyalty_tx"),
            ):
                res = await db.execute(
                    update(model)
                    .where(
                        model.organization_id == int(organization_id),  # type: ignore[attr-defined]
                        col.in_(drop_ids),
                    )
                    .values({col: keep_id}),
                    execution_options={"synchronize_session": False},
                )
                moved[key] = int(res.rowcount or 0)

            db.expire_all()

            keep_balance = await db.scalar(
                select(LoyaltyBalance).where(
                    LoyaltyBalance.organization_id == int(organization_id),
                    LoyaltyBalance.user_id == keep_id,
                ),
            )
            for dup_id in drop_ids:
                dup_balance = await db.scalar(
                    select(LoyaltyBalance).where(
                        LoyaltyBalance.organization_id == int(organization_id),
                        LoyaltyBalance.user_id == dup_id,
                    ),
                )
                if dup_balance is None:
                    continue
                if keep_balance is None:
                    dup_balance.user_id = keep_id
                    keep_balance = dup_balance
                else:
                    keep_balance.balance_points = int(keep_balance.balance_points or 0) + int(
                        dup_balance.balance_points or 0,
                    )
                    await db.delete(dup_balance)

            await db.execute(
                delete(User).where(
                    User.organization_id == int(organization_id),
                    User.id.in_(drop_ids),
                ),
                execution_options={"synchronize_session": False},
            )

            await db.commit()

        reports.append(
            {
                "digits": group.digits,
                "canonical_phone": group.canonical_phone,
                "keep_user_id": keep_id,
                "drop_user_ids": drop_ids,
                "dry_run": dry_run,
                "moved": moved,
            },
        )
    return reports

