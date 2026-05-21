"""Аудит действий Super Admin (платформенный уровень)."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import StaffUser, SuperadminAuditLog

_SECRET_CREDENTIAL_FIELDS = frozenset({"iiko_api_login"})


def sanitize_credentials_audit_details(changed_fields: list[str]) -> dict[str, object]:
    """Не сохраняем значения секретов — только имена изменённых полей."""
    return {
        "changed_fields": changed_fields,
        "secrets_updated": [f for f in changed_fields if f in _SECRET_CREDENTIAL_FIELDS],
    }


async def log_superadmin_action(
    db: AsyncSession,
    *,
    actor: StaffUser,
    action: str,
    target_type: str,
    target_id: str | int,
    organization_id: int | None = None,
    details: dict[str, object] | None = None,
) -> SuperadminAuditLog:
    entry = SuperadminAuditLog(
        actor_staff_user_id=int(actor.id),
        actor_email=(actor.email or "").strip(),
        action=(action or "").strip(),
        target_type=(target_type or "").strip(),
        target_id=str(target_id),
        organization_id=int(organization_id) if organization_id is not None else None,
        details_json=details or None,
    )
    db.add(entry)
    await db.flush()
    return entry


async def list_superadmin_audit(
    db: AsyncSession,
    *,
    limit: int = 50,
    offset: int = 0,
    organization_id: int | None = None,
) -> tuple[list[SuperadminAuditLog], int]:
    lim = max(1, min(int(limit or 50), 200))
    off = max(0, int(offset or 0))

    base = select(SuperadminAuditLog)
    if organization_id is not None:
        base = base.where(SuperadminAuditLog.organization_id == int(organization_id))

    q = base.order_by(SuperadminAuditLog.created_at.desc(), SuperadminAuditLog.id.desc()).offset(off).limit(lim)
    rows = list((await db.execute(q)).scalars().all())

    count_base = select(func.count()).select_from(SuperadminAuditLog)
    if organization_id is not None:
        count_base = count_base.where(SuperadminAuditLog.organization_id == int(organization_id))
    total = int((await db.scalar(count_base)) or 0)
    return rows, total
