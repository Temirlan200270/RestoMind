"""Per-organization rate limit for admin API mutations."""

from __future__ import annotations

import logging
from typing import Callable

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.config import settings
from app.core.rate_limiter import check_rate_limit_window

logger = logging.getLogger(__name__)

_SKIP_PREFIXES = (
    "/api/admin/auth/login",
    "/api/admin/auth/demo-login",
    "/api/admin/auth/logout",
    "/api/admin/auth/me",
    "/api/admin/ws",
)

_MUTATING = frozenset({"POST", "PUT", "PATCH", "DELETE"})


async def admin_org_rate_limit_middleware(request: Request, call_next: Callable) -> Response:
    path = request.url.path
    if not path.startswith("/api/admin/"):
        return await call_next(request)
    if any(path.startswith(p) for p in _SKIP_PREFIXES):
        return await call_next(request)
    if request.method.upper() not in _MUTATING:
        return await call_next(request)

    org_id = request.session.get("organization_id")
    if org_id is None:
        return await call_next(request)

    limit = int(getattr(settings, "admin_rate_limit_per_minute", 120) or 120)
    key = f"rate:org:{int(org_id)}"
    if not await check_rate_limit_window(key, limit=limit, window_seconds=60):
        logger.warning("admin org rate limit org=%s path=%s", org_id, path)
        return JSONResponse(
            status_code=429,
            content={"detail": "Слишком много запросов для организации. Повторите через минуту."},
        )
    return await call_next(request)
