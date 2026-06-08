"""Unified agent command contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class CommandContext:
    organization_id: int
    payload: dict[str, Any]
    staff_role: str = "admin"


@dataclass(frozen=True)
class AgentCommandSpec:
    action_type: str
    command_name: str
    risk_level: str
    required_role: str = "manager"
    requires_owner_confirm: bool = False
    requires_preview: bool = False
    confirm_required: bool = True
    external_side_effect: bool = False


class AgentCommand(Protocol):
    spec: AgentCommandSpec

    def validate(self, payload: dict[str, Any] | None) -> dict[str, Any]: ...

    async def preview(self, db: AsyncSession, ctx: CommandContext) -> dict[str, Any]: ...

    async def apply(self, db: AsyncSession, ctx: CommandContext) -> dict[str, Any]: ...

    def audit_payload(self, payload: dict[str, Any]) -> dict[str, Any]: ...


def command_public(spec: AgentCommandSpec) -> dict[str, Any]:
    return {
        "name": spec.command_name,
        "action_type": spec.action_type,
        "version": 2,
        "risk_level": spec.risk_level,
        "required_role": spec.required_role,
        "requires_owner_confirm": spec.requires_owner_confirm,
        "requires_preview": spec.requires_preview,
        "confirm_required": spec.confirm_required,
        "external_side_effect": spec.external_side_effect,
    }
