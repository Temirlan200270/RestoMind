"""Agent command registry v2."""

from __future__ import annotations

from typing import Any

from app.services.agent_commands.base import AgentCommand, AgentCommandSpec, command_public
from app.services.agent_commands.force_close import force_close_command
from app.services.agent_commands.iiko_write import iiko_write_staged_command
from app.services.agent_commands.upsell_rule_create import upsell_rule_create_command

_COMMANDS: dict[str, AgentCommand] = {
    force_close_command.spec.action_type: force_close_command,
    upsell_rule_create_command.spec.action_type: upsell_rule_create_command,
    iiko_write_staged_command.spec.action_type: iiko_write_staged_command,
}

_ROLE_RANK = {"operator": 0, "manager": 1, "admin": 2, "owner": 2}


def get_agent_command(action_type: str) -> AgentCommand:
    key = (action_type or "").strip()
    cmd = _COMMANDS.get(key)
    if cmd is None:
        raise ValueError(f"unsupported_action_type:{key}")
    return cmd


def supported_agent_commands() -> list[dict[str, Any]]:
    return [command_public(cmd.spec) for cmd in _COMMANDS.values()]


def validate_agent_command(action_type: str, payload: dict[str, Any] | None) -> dict[str, Any]:
    return get_agent_command(action_type).validate(payload)


def command_requires_preview(spec: AgentCommandSpec) -> bool:
    return bool(spec.requires_preview or spec.risk_level == "high" or spec.external_side_effect)


def staff_role_allows(spec: AgentCommandSpec, staff_role: str) -> bool:
    role = (staff_role or "admin").strip().lower()
    if role == "owner":
        role = "admin"
    required = (spec.required_role or "manager").strip().lower()
    if spec.requires_owner_confirm and _ROLE_RANK.get(role, 0) < _ROLE_RANK["admin"]:
        return False
    return _ROLE_RANK.get(role, 0) >= _ROLE_RANK.get(required, 1)
