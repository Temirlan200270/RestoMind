"""Учётные данные iiko для организации: БД (зашифровано) + fallback .env."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import Organization
from app.services.secrets_crypto import decrypt_secret, fernet_or_none

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OrgIikoCredentials:
    api_login: str
    iiko_organization_id: str
    terminal_group_id: str


def _plain_api_login_from_org(org: Organization) -> str:
    enc = (org.iiko_api_login_enc or "").strip()
    if enc:
        try:
            return decrypt_secret(enc).strip()
        except ValueError:
            logger.warning("organization_id=%s: не удалось расшифровать iiko_api_login_enc", org.id)
            return ""
    return (org.iiko_api_login or "").strip()


def org_has_iiko_in_db(org: Organization | None) -> bool:
    if org is None:
        return False
    if (org.iiko_api_login_enc or "").strip():
        if fernet_or_none() is None:
            return False
        login = _plain_api_login_from_org(org)
    else:
        login = (org.iiko_api_login or "").strip()
    iid = (org.iiko_organization_id or "").strip()
    return bool(login and iid)


async def resolve_org_iiko_credentials(db: AsyncSession, organization_id: int) -> OrgIikoCredentials | None:
    """
    Приоритет: поля ``Organization`` (в т.ч. зашифрованный логин), иначе глобальный .env
    для ``settings.default_organization_id`` (обратная совместимость).
    """
    org = await db.get(Organization, organization_id)
    api_login = ""
    iiko_org = ""
    terminal = ""

    if org is not None:
        api_login = _plain_api_login_from_org(org)
        iiko_org = (org.iiko_organization_id or "").strip()
        terminal = (org.iiko_terminal_group_id or "").strip()

    if not api_login or not iiko_org:
        if int(organization_id) == int(settings.default_organization_id):
            api_login = (settings.iiko_api_login or "").strip()
            iiko_org = (settings.iiko_organization_id or "").strip()
            terminal = terminal or (settings.iiko_terminal_group_id or "").strip()

    if not api_login or not iiko_org:
        return None
    return OrgIikoCredentials(
        api_login=api_login,
        iiko_organization_id=iiko_org,
        terminal_group_id=terminal or (settings.iiko_terminal_group_id or "").strip(),
    )


async def list_organizations_with_iiko_db(db: AsyncSession) -> list[Organization]:
    """Организации с заполненным iiko (для фоновой синхронизации стоп-листов)."""
    res = await db.execute(select(Organization).where(Organization.is_active.is_(True)))
    rows = list(res.scalars().all())
    out: list[Organization] = []
    for o in rows:
        if org_has_iiko_in_db(o):
            out.append(o)
    return out
