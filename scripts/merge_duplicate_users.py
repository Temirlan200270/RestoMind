"""One-off merge of duplicate User rows (legacy phone format → E.164).

Usage:
    python scripts/merge_duplicate_users.py --org-id 1 --dry-run
    python scripts/merge_duplicate_users.py --org-id 1 --phone +77051310837 --apply
"""

from __future__ import annotations

import argparse
import asyncio
import json

from app.db.session import async_session_factory
from app.services.user_phone_merge import merge_duplicate_users


async def _run(org_id: int, phone: str | None, apply: bool) -> int:
    dry_run = not apply
    async with async_session_factory() as db:
        reports = await merge_duplicate_users(
            db,
            org_id,
            phone_filter=phone,
            dry_run=dry_run,
        )
    if not reports:
        print(f"No duplicate groups to merge for org_id={org_id}.")
        return 0
    mode = "DRY-RUN" if dry_run else "APPLIED"
    print(f"{mode}: {len(reports)} group(s)")
    for row in reports:
        print(json.dumps(row, ensure_ascii=False, indent=2))
    if dry_run:
        print("\nRe-run with --apply to persist changes.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Merge duplicate users by normalized phone")
    parser.add_argument("--org-id", type=int, required=True)
    parser.add_argument("--phone", type=str, default=None, help="Limit to one MSISDN")
    parser.add_argument("--apply", action="store_true", help="Persist merge (default: dry-run)")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_run(args.org_id, args.phone, args.apply)))


if __name__ == "__main__":
    main()
