"""Учётные данные r_keeper для организации (meta_json + fallback .env)."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import Organization


@dataclass(frozen=True)
class OrgRKeeperCredentials:
    server_url: str
    object_id: str


def _meta_dict(org: Organization | None) -> dict:
    if org is None or not isinstance(org.meta_json, dict):
        return {}
    return dict(org.meta_json)


def org_has_rkeeper_in_db(org: Organization | None) -> bool:
    if org is None:
        return False
    meta = _meta_dict(org)
    rk = meta.get("rkeeper") if isinstance(meta.get("rkeeper"), dict) else meta
    server_url = str(rk.get("server_url") or rk.get("rkeeper_server_url") or "").strip()
    object_id = str(rk.get("object_id") or rk.get("rkeeper_object_id") or "").strip()
    return bool(server_url and object_id)


async def resolve_org_rkeeper_credentials(
    db: AsyncSession,
    organization_id: int,
) -> OrgRKeeperCredentials | None:
    """Приоритет: ``Organization.meta_json``, иначе глобальный .env для default org."""
    org = await db.get(Organization, int(organization_id))
    server_url = ""
    object_id = ""

    if org is not None:
        meta = _meta_dict(org)
        rk = meta.get("rkeeper") if isinstance(meta.get("rkeeper"), dict) else meta
        server_url = str(rk.get("server_url") or rk.get("rkeeper_server_url") or "").strip()
        object_id = str(rk.get("object_id") or rk.get("rkeeper_object_id") or "").strip()

    if (not server_url or not object_id) and int(organization_id) == int(settings.default_organization_id):
        server_url = server_url or str(getattr(settings, "rkeeper_server_url", "") or "").strip()
        object_id = object_id or str(getattr(settings, "rkeeper_object_id", "") or "").strip()

    if not server_url or not object_id:
        return None
    return OrgRKeeperCredentials(server_url=server_url, object_id=object_id)
