"""Guarded autonomous actions for Intelligence OS."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.supplymind import build_supplymind_draft
from app.services.system_events import BusinessEvent, emit_event


async def create_guarded_purchase_draft(
    db: AsyncSession,
    org_id: int,
    *,
    location_id: int | None = None,
    cover_days: int = 7,
    actor: str = "system",
) -> dict[str, Any]:
    """Autonomous but guarded: creates an internal draft only; manager must approve it."""
    draft = await build_supplymind_draft(
        db,
        int(org_id),
        location_id=location_id,
        cover_days=cover_days,
    )
    await emit_event(
        db,
        BusinessEvent(
            org_id=int(org_id),
            type="autonomous.purchase_draft.created",
            actor=actor,
            entity_type="supply_purchase_draft",
            entity_id=int(draft.id),
            location_id=location_id,
            payload={
                "draft_id": int(draft.id),
                "requires_human_approval": True,
                "experimental": True,
                "internal_only": True,
                "external_action_taken": False,
                "items_count": len(draft.items_json or []),
                "cover_days": cover_days,
            },
        ),
    )
    return {
        "ok": True,
        "draft_id": int(draft.id),
        "requires_human_approval": True,
        "experimental": True,
        "internal_only": True,
        "external_action_taken": False,
        "items_count": len(draft.items_json or []),
    }
