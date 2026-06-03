"""Backfill iiko OLAP sales into the unified sales fact layer."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone

from app.db.session import async_session_factory
from app.services.iiko_olap_sales_sync import sync_olap_sales_for_org


def _parse_since(value: str) -> int:
    text = (value or "30d").strip().lower()
    if text.endswith("d"):
        text = text[:-1]
    days = int(text)
    return max(1, days)


async def _run(org_id: int, days: int) -> int:
    today = datetime.now(tz=timezone.utc).date()
    date_from = today - timedelta(days=days - 1)
    async with async_session_factory() as db:
        count = await sync_olap_sales_for_org(db, org_id, date_from, today)
        await db.commit()
        return count


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill iiko OLAP SALES data")
    parser.add_argument("--org-id", type=int, required=True)
    parser.add_argument("--since", default="30d", help="History depth, e.g. 30d")
    args = parser.parse_args()
    count = asyncio.run(_run(args.org_id, _parse_since(args.since)))
    print(f"OLAP sales backfill complete: org={args.org_id} orders={count}")


if __name__ == "__main__":
    main()
