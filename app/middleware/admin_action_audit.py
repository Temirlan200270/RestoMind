"""Audit log for admin HTTP mutations outside emit_event (OS gap closure)."""

from __future__ import annotations

import logging
from typing import Callable

from starlette.requests import Request
from starlette.responses import Response

from app.services.async_tasks import spawn_tracked

logger = logging.getLogger(__name__)

_SKIP_PREFIXES = (
    "/api/admin/auth/login",
    "/api/admin/auth/demo-login",
    "/api/admin/auth/logout",
    "/api/admin/auth/me",
    "/api/admin/ws",
    "/api/admin/test-bot",
)

_MUTATING = frozenset({"POST", "PUT", "PATCH", "DELETE"})


async def _persist_admin_mutation_audit(
    *,
    organization_id: int,
    actor: str,
    method: str,
    path: str,
    staff_id: int | None,
) -> None:
    from app.db.models import AuditLog
    from app.db.session import async_session_factory
    from app.services.events import publish_org_event

    action = f"admin.{method.lower()}.{path.strip('/')}"
    details = {"method": method, "path": path}
    if staff_id is not None:
        details["staff_id"] = staff_id
    try:
        async with async_session_factory() as db:
            entry = AuditLog(
                organization_id=int(organization_id),
                actor=str(actor),
                action=action[:120],
                entity_type="admin_request",
                entity_id=path,
                details=details,
            )
            db.add(entry)
            await db.commit()
            await db.refresh(entry)
            created = entry.created_at.isoformat() if entry.created_at else None
        await publish_org_event(
            int(organization_id),
            "os.audit",
            {
                "organization_id": int(organization_id),
                "org_id": int(organization_id),
                "actor": str(actor),
                "action": action[:120],
                "entity_type": "admin_request",
                "entity_id": path,
                "created_at": created,
                "title": f"Админ: {method} {path}",
            },
        )
    except Exception:
        logger.exception("admin_action_audit failed org=%s path=%s", organization_id, path)


async def admin_action_audit_middleware(request: Request, call_next: Callable) -> Response:
    response = await call_next(request)
    if response.status_code >= 400:
        return response
    method = request.method.upper()
    if method not in _MUTATING:
        return response
    path = request.url.path
    if not path.startswith("/api/admin"):
        return response
    if any(path.startswith(prefix) for prefix in _SKIP_PREFIXES):
        return response
    session = request.session
    if not session.get("admin_ok"):
        return response
    if session.get("is_demo"):
        return response
    org_id = session.get("organization_id")
    if not org_id:
        return response
    actor = str(session.get("admin_user") or "staff")
    staff_raw = session.get("staff_id")
    staff_id = int(staff_raw) if staff_raw is not None else None
    spawn_tracked(
        _persist_admin_mutation_audit(
            organization_id=int(org_id),
            actor=actor,
            method=method,
            path=path,
            staff_id=staff_id,
        ),
        name=f"admin_action_audit_{org_id}",
        log=logger,
    )
    return response
