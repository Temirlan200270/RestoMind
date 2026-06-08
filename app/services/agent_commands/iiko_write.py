"""iiko menu write commands (staged + guarded live)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MenuItem
from app.services.agent_commands.base import AgentCommandSpec, CommandContext, command_public
from app.services.pos_write.iiko_adapter import IikoWriteAdapter


class IikoWriteStagedCommand:
    spec = AgentCommandSpec(
        action_type="iiko_write_staged",
        command_name="StageIikoWriteCommand",
        risk_level="high",
        required_role="admin",
        requires_owner_confirm=True,
        requires_preview=True,
        external_side_effect=True,
    )

    def validate(self, payload: dict[str, Any] | None) -> dict[str, Any]:
        data = dict(payload or {})
        items = data.get("items") or []
        if not isinstance(items, list) or not items:
            raise ValueError("iiko_write_items_required")
        data["items"] = items[:50]
        data["operation"] = str(data.get("operation") or "menu_price_update").strip()[:80]
        data["_command"] = command_public(self.spec)
        return data

    async def preview(self, db: AsyncSession, ctx: CommandContext) -> dict[str, Any]:
        items_in = ctx.payload.get("items") or []
        menu_rows = (
            await db.execute(
                select(MenuItem).where(
                    MenuItem.organization_id == ctx.organization_id,
                    MenuItem.is_archived.is_(False),
                ).limit(500),
            )
        ).scalars().all()
        by_name = {str(m.name or "").strip().lower(): m for m in menu_rows if m.name}
        diffs: list[dict[str, Any]] = []
        for raw in items_in:
            if not isinstance(raw, dict):
                continue
            label = str(raw.get("label") or raw.get("name") or "").strip()
            new_price = raw.get("new_price") or raw.get("price")
            matched = by_name.get(label.lower()) if label else None
            old_price = float(matched.price or 0) if matched else None
            diffs.append(
                {
                    "label": label or str(raw),
                    "menu_item_id": matched.id if matched else None,
                    "old_price": old_price,
                    "new_price": float(new_price) if new_price is not None else None,
                    "matched": matched is not None,
                },
            )
        return {
            "action": "iiko_write_staged",
            "operation": ctx.payload.get("operation") or "menu_price_update",
            "diff": diffs,
            "affected_count": sum(1 for d in diffs if d.get("matched")),
            "unmatched_count": sum(1 for d in diffs if not d.get("matched")),
            "risk": "high",
            "live_write_enabled": False,
            "note": "Staged write — live iiko API только после preview и owner confirm.",
        }

    async def apply(self, db: AsyncSession, ctx: CommandContext) -> dict[str, Any]:
        adapter = IikoWriteAdapter(db, organization_id=ctx.organization_id)
        preview = ctx.payload.get("_preview") or {}
        if not preview and ctx.payload.get("_requires_live"):
            result = await adapter.apply_menu_price_update(
                items=ctx.payload.get("items") or [],
                idempotency_key=str(ctx.payload.get("_idempotency_key") or ""),
                previewed=True,
            )
            return result
        return {
            "staged": True,
            "organization_id": ctx.organization_id,
            "operation": str(ctx.payload.get("operation") or "menu_price_update"),
            "items": ctx.payload.get("items") or [],
            "note": "Запрос сохранён. Live write через IikoWriteAdapter после guardrails.",
        }

    def audit_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": payload.get("operation"),
            "items_count": len(payload.get("items") or []),
        }


iiko_write_staged_command = IikoWriteStagedCommand()
