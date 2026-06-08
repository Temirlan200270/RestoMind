"""Postgres RLS helpers — last-line tenant isolation via session settings."""

from __future__ import annotations

import logging
from contextvars import ContextVar, Token

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

RLS_ORG_SETTING = "app.organization_id"
RLS_BYPASS_SETTING = "app.bypass_rls"

_tenant_org_id: ContextVar[int | None] = ContextVar("tenant_org_id", default=None)
_bypass_rls: ContextVar[bool] = ContextVar("bypass_rls", default=False)


def set_tenant_rls_context(organization_id: int | None) -> Token[int | None]:
    """Bind current request/task to a tenant for RLS policies."""
    return _tenant_org_id.set(int(organization_id) if organization_id is not None else None)


def set_tenant_rls_bypass(enabled: bool = True) -> Token[bool]:
    """Allow cross-tenant reads/writes for workers, tests, superadmin maintenance."""
    return _bypass_rls.set(bool(enabled))


def reset_tenant_rls_context(token: Token[int | None]) -> None:
    _tenant_org_id.reset(token)


def reset_tenant_rls_bypass(token: Token[bool]) -> None:
    _bypass_rls.reset(token)


def current_tenant_rls_org_id() -> int | None:
    return _tenant_org_id.get()


def tenant_rls_bypass_enabled() -> bool:
    return _bypass_rls.get()


async def apply_tenant_rls(session: AsyncSession) -> None:
    """Apply per-transaction Postgres settings consumed by RLS policies."""
    bind = session.get_bind()
    dialect_name = getattr(getattr(bind, "dialect", None), "name", "")
    if dialect_name != "postgresql":
        return
    if tenant_rls_bypass_enabled():
        await session.execute(
            text(f"SELECT set_config('{RLS_BYPASS_SETTING}', 'true', true)"),
        )
        return
    org_id = current_tenant_rls_org_id()
    if org_id is not None:
        await session.execute(
            text(f"SELECT set_config('{RLS_ORG_SETTING}', :oid, true)"),
            {"oid": str(int(org_id))},
        )
