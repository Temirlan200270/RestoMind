"""Force-close restaurant command."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Organization
from app.services.agent_commands.base import AgentCommandSpec, CommandContext, command_public


class ForceCloseCommand:
    spec = AgentCommandSpec(
        action_type="force_close",
        command_name="ForceCloseRestaurantCommand",
        risk_level="medium",
        required_role="manager",
        requires_preview=True,
    )

    def validate(self, payload: dict[str, Any] | None) -> dict[str, Any]:
        data = dict(payload or {})
        try:
            minutes = int(data.get("minutes") or 0)
        except (TypeError, ValueError):
            raise ValueError("force_close_minutes_invalid") from None
        if minutes < 0 or minutes > 480:
            raise ValueError("force_close_minutes_out_of_range")
        data["minutes"] = minutes
        data["reason"] = str(data.get("reason") or "").strip()[:500]
        data["_command"] = command_public(self.spec)
        return data

    async def preview(self, db: AsyncSession, ctx: CommandContext) -> dict[str, Any]:
        org = await db.get(Organization, ctx.organization_id)
        minutes = int(ctx.payload.get("minutes") or 0)
        now = datetime.now(timezone.utc)
        until = now + timedelta(minutes=minutes) if minutes > 0 else None
        return {
            "action": "force_close",
            "current_force_closed": org.force_closed_until.isoformat() if org and org.force_closed_until else None,
            "proposed_until": until.isoformat() if until else None,
            "minutes": minutes,
            "reason": ctx.payload.get("reason") or "",
            "risk": "medium",
            "summary": f"Пауза приёма заказов на {minutes} мин" if minutes > 0 else "Снять принудительное закрытие",
        }

    async def apply(self, db: AsyncSession, ctx: CommandContext) -> dict[str, Any]:
        org = await db.get(Organization, ctx.organization_id)
        if org is None:
            raise ValueError("organization_not_found")
        minutes = int(ctx.payload.get("minutes") or 0)
        reason = str(ctx.payload.get("reason") or "").strip()
        if minutes <= 0:
            org.force_closed_until = None
            org.force_closed_reason = ""
        else:
            org.force_closed_until = datetime.now(timezone.utc) + timedelta(minutes=minutes)
            org.force_closed_reason = reason
        await db.flush()
        fc_until = org.force_closed_until
        return {
            "force_closed": fc_until is not None,
            "force_closed_until": fc_until.isoformat() if fc_until else None,
            "force_closed_reason": org.force_closed_reason or "",
        }

    def audit_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "minutes": payload.get("minutes"),
            "reason": payload.get("reason"),
        }


force_close_command = ForceCloseCommand()
