"""Import guest phones from iiko Cloud delivery history into RestoMind users (marketing segments)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User
from app.integrations.iiko_client import IikoClient
from app.services.org_iiko import resolve_org_iiko_credentials
from app.services.twilio_routing import normalize_e164

logger = logging.getLogger(__name__)

_DEFAULT_STATUSES = (
    "Delivered",
    "Closed",
    "OnWay",
    "Waiting",
    "CookingCompleted",
    "CookingStarted",
    "ReadyForCooking",
    "WaitCooking",
    "Unconfirmed",
)


def _extract_phone_from_delivery_entry(entry: dict[str, Any]) -> str:
    if not isinstance(entry, dict):
        return ""
    order = entry.get("order") if isinstance(entry.get("order"), dict) else entry
    if not isinstance(order, dict):
        return ""
    customer = order.get("customer") if isinstance(order.get("customer"), dict) else {}
    for key in ("phone", "Phone"):
        if isinstance(customer, dict) and customer.get(key):
            return normalize_e164(str(customer.get(key)))
        if order.get(key):
            return normalize_e164(str(order.get(key)))
    return ""


def collect_phones_from_iiko_deliveries(payload: dict[str, Any]) -> set[str]:
    phones: set[str] = set()
    blocks = payload.get("ordersByOrganizations") or payload.get("ordersByOrganization") or []
    if isinstance(blocks, dict):
        blocks = [blocks]
    for block in blocks:
        if not isinstance(block, dict):
            continue
        orders = block.get("orders") or []
        for entry in orders:
            phone = _extract_phone_from_delivery_entry(entry if isinstance(entry, dict) else {})
            if phone:
                phones.add(phone)
    return phones


async def sync_iiko_customers_for_org(
    db: AsyncSession,
    organization_id: int,
    *,
    days: int = 90,
) -> dict[str, Any]:
    """
    Pull delivery orders from iiko Cloud for the last ``days`` and upsert ``User`` rows by phone.
    Does not overwrite marketing_opt_out or existing names.
    """
    creds = await resolve_org_iiko_credentials(db, organization_id)
    if creds is None:
        return {"ok": False, "error": "iiko_not_configured", "detail": "Укажите API-логин и organization ID iiko в настройках."}

    days = max(7, min(int(days or 90), 365))
    date_to = datetime.now(tz=timezone.utc).date()
    date_from = date_to - timedelta(days=days)
    date_from_iso = f"{date_from.isoformat()}T00:00:00.000"
    date_to_iso = f"{date_to.isoformat()}T23:59:59.999"

    async with IikoClient(api_login=creds.api_login) as client:
        payload = await client.fetch_deliveries_by_date_and_status(
            organization_ids=[creds.iiko_organization_id],
            date_from=date_from_iso,
            date_to=date_to_iso,
            statuses=list(_DEFAULT_STATUSES),
        )

    phones = collect_phones_from_iiko_deliveries(payload)
    created = 0
    existing = 0
    skipped = 0

    for phone in sorted(phones):
        if len(phone) < 8:
            skipped += 1
            continue
        user = await db.scalar(
            select(User).where(
                User.organization_id == organization_id,
                User.phone == phone,
            ),
        )
        if user is not None:
            existing += 1
            continue
        db.add(User(phone=phone, organization_id=organization_id))
        created += 1

    await db.commit()
    logger.info(
        "iiko customer sync org=%s days=%s phones=%s created=%s existing=%s",
        organization_id,
        days,
        len(phones),
        created,
        existing,
    )
    return {
        "ok": True,
        "days": days,
        "phones_found": len(phones),
        "users_created": created,
        "users_existing": existing,
        "phones_skipped": skipped,
    }
