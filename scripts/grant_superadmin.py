"""Grant super admin flag for an existing staff user.

Usage:
    python scripts/grant_superadmin.py owner@example.com
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select

from app.db.models import StaffUser
from app.db.session import async_session_factory


async def _grant(email: str) -> int:
    normalized = (email or "").strip().lower()
    if not normalized:
        print("Email is required.")
        return 2
    async with async_session_factory() as db:
        staff = await db.scalar(select(StaffUser).where(StaffUser.email == normalized))
        if staff is None:
            print(f"Staff user not found: {normalized}")
            return 1
        if not bool(staff.is_superadmin):
            staff.is_superadmin = True
            await db.commit()
            print(f"Granted superadmin: {normalized}")
            return 0
        print(f"Already superadmin: {normalized}")
        return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Grant superadmin role to staff user")
    parser.add_argument("email", type=str, help="Staff email")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_grant(args.email)))


if __name__ == "__main__":
    main()
