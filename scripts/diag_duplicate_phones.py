"""Find duplicate User rows per organization (legacy phone formats vs E.164).

Usage:
    python scripts/diag_duplicate_phones.py --org-id 1
    python scripts/diag_duplicate_phones.py --org-id 1 --phone +77051310837
"""

from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict

from sqlalchemy import select

from app.db.models import User
from app.db.session import async_session_factory
from app.services.phone_normalize import canonical_user_phone, phone_digits_key


async def _run(org_id: int, phone_filter: str | None) -> int:
    async with async_session_factory() as db:
        rows = (
            await db.scalars(
                select(User).where(User.organization_id == int(org_id)).order_by(User.id),
            )
        ).all()

    if phone_filter:
        needle = phone_digits_key(phone_filter)
        rows = [u for u in rows if phone_digits_key(u.phone) == needle]

    groups: dict[str, list[User]] = defaultdict(list)
    for user in rows:
        key = phone_digits_key(user.phone)
        if key:
            groups[key].append(user)

    dup_groups = {k: v for k, v in groups.items() if len(v) > 1}
    if not dup_groups:
        print(f"No duplicate phone groups for org_id={org_id}.")
        return 0

    print(f"Duplicate phone groups for org_id={org_id}: {len(dup_groups)}")
    for digits, users in sorted(dup_groups.items(), key=lambda x: x[0]):
        print(f"\n  digits={digits}  canonical={canonical_user_phone('+' + digits)}")
        for u in sorted(users, key=lambda x: int(x.id or 0)):
            created = u.created_at.isoformat() if u.created_at else "-"
            print(f"    id={u.id}  phone={u.phone!r}  created_at={created}")
        print(
            "  SQL:",
            f"SELECT id, phone, created_at FROM users WHERE organization_id = {org_id}",
            f"AND regexp_replace(phone, '\\D', '', 'g') = '{digits}' ORDER BY created_at;",
        )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnose duplicate users by normalized phone")
    parser.add_argument("--org-id", type=int, required=True, help="Organization id")
    parser.add_argument("--phone", type=str, default=None, help="Optional phone filter (+7705…)")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_run(args.org_id, args.phone)))


if __name__ == "__main__":
    main()
