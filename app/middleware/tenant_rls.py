"""Bind tenant org_id from admin session before DB queries (Postgres RLS)."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import Response

from app.core.config import settings
from app.db.tenant_rls import reset_tenant_rls_bypass, reset_tenant_rls_context, set_tenant_rls_bypass, set_tenant_rls_context


async def tenant_rls_middleware(request: Request, call_next) -> Response:
    org_token = None
    bypass_token = None
    path = request.url.path or ""
    try:
        if path.startswith("/api/admin"):
            org_raw = request.session.get("organization_id")
            org_id = int(org_raw) if org_raw is not None else int(settings.default_organization_id)
            org_token = set_tenant_rls_context(org_id)
        else:
            bypass_token = set_tenant_rls_bypass(True)
        return await call_next(request)
    finally:
        if org_token is not None:
            reset_tenant_rls_context(org_token)
        if bypass_token is not None:
            reset_tenant_rls_bypass(bypass_token)
