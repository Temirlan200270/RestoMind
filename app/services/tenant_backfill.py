"""Backfill NULL organization_id / location_id for tenant isolation hardening."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Booking, ChatLog, EscalationEvent, FailedTask, Order, User
from app.services.tenant_scope import ensure_default_location

logger = logging.getLogger(__name__)


async def diagnose_tenant_scope_gaps(db: AsyncSession, org_id: int | None = None) -> dict[str, Any]:
    """Count rows with NULL organization_id / location_id (optionally per org)."""
    tables = (
        ("orders", Order, Order.organization_id),
        ("chat_logs", ChatLog, ChatLog.organization_id),
        ("bookings", Booking, Booking.organization_id),
        ("escalation_events", EscalationEvent, EscalationEvent.organization_id),
        ("failed_tasks", FailedTask, FailedTask.organization_id),
    )
    null_org: dict[str, int] = {}
    for label, model, col in tables:
        stmt = select(func.count()).select_from(model).where(col.is_(None))
        if org_id is not None and hasattr(model, "organization_id"):
            stmt = stmt.where(model.organization_id == org_id)  # type: ignore[attr-defined]
        null_org[label] = int(await db.scalar(stmt) or 0)

    loc_null: dict[str, int] = {}
    for label, model in (("orders", Order), ("chat_logs", ChatLog), ("bookings", Booking)):
        loc_null[label] = int(
            await db.scalar(
                select(func.count()).select_from(model).where(model.location_id.is_(None)),  # type: ignore[attr-defined]
            )
            or 0,
        )
    return {"null_organization_id": null_org, "null_location_id": loc_null}


async def backfill_null_organization_ids(db: AsyncSession) -> dict[str, int]:
    """Fill organization_id from related User where possible."""
    updated: dict[str, int] = {}
    stmts = {
        "orders": """
            UPDATE orders SET organization_id = (
                SELECT u.organization_id FROM users u WHERE u.id = orders.user_id
            )
            WHERE organization_id IS NULL AND user_id IS NOT NULL
              AND EXISTS (
                SELECT 1 FROM users u2 WHERE u2.id = orders.user_id AND u2.organization_id IS NOT NULL
              )
        """,
        "bookings": """
            UPDATE bookings SET organization_id = (
                SELECT u.organization_id FROM users u WHERE u.id = bookings.user_id
            )
            WHERE organization_id IS NULL AND user_id IS NOT NULL
              AND EXISTS (
                SELECT 1 FROM users u2 WHERE u2.id = bookings.user_id AND u2.organization_id IS NOT NULL
              )
        """,
        "chat_logs": """
            UPDATE chat_logs SET organization_id = (
                SELECT u.organization_id FROM users u WHERE u.id = chat_logs.user_id
            )
            WHERE organization_id IS NULL AND user_id IS NOT NULL
              AND EXISTS (
                SELECT 1 FROM users u2 WHERE u2.id = chat_logs.user_id AND u2.organization_id IS NOT NULL
              )
        """,
        "failed_tasks": """
            UPDATE failed_tasks SET organization_id = (
                SELECT u.organization_id FROM users u WHERE u.phone = failed_tasks.phone LIMIT 1
            )
            WHERE organization_id IS NULL AND phone IS NOT NULL
              AND EXISTS (
                SELECT 1 FROM users u2 WHERE u2.phone = failed_tasks.phone AND u2.organization_id IS NOT NULL
              )
        """,
    }
    for label, sql in stmts.items():
        r = await db.execute(text(sql))
        updated[label] = int(r.rowcount or 0)
    logger.info("tenant_backfill org_id: %s", updated)
    return updated


async def backfill_null_location_ids(db: AsyncSession, org_id: int) -> dict[str, int]:
    """Assign default location to orders/chats/bookings missing location_id."""
    loc = await ensure_default_location(db, int(org_id))
    lid = int(loc.id)
    updated: dict[str, int] = {}

    for label, model in (("orders", Order), ("chat_logs", ChatLog), ("bookings", Booking)):
        r = await db.execute(
            update(model)
            .where(
                model.organization_id == int(org_id),  # type: ignore[attr-defined]
                model.location_id.is_(None),  # type: ignore[attr-defined]
            )
            .values(location_id=lid),
        )
        updated[label] = int(r.rowcount or 0)

    logger.info("tenant_backfill location org=%s: %s", org_id, updated)
    return updated


async def run_tenant_scope_backfill(
    db: AsyncSession,
    org_id: int,
    *,
    fill_org: bool = True,
    fill_location: bool = True,
) -> dict[str, Any]:
    """One-shot tenant isolation backfill for an organization."""
    before = await diagnose_tenant_scope_gaps(db)
    org_updates: dict[str, int] = {}
    loc_updates: dict[str, int] = {}
    if fill_org:
        org_updates = await backfill_null_organization_ids(db)
    if fill_location:
        loc_updates = await backfill_null_location_ids(db, org_id)
    await db.commit()
    after = await diagnose_tenant_scope_gaps(db)
    return {
        "ok": True,
        "org_id": org_id,
        "before": before,
        "organization_id_updates": org_updates,
        "location_id_updates": loc_updates,
        "after": after,
    }
