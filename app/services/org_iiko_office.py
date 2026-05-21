"""Учётные данные iiko Office для организации (integration_config_json.iiko_office)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Organization
from app.services.secrets_crypto import decrypt_secret, fernet_or_none

logger = logging.getLogger(__name__)

IIKO_OFFICE_CONFIG_KEY = "iiko_office"


@dataclass(frozen=True)
class OrgIikoOfficeCredentials:
    host: str
    login: str
    password: str
    store_id: str
    department_id: str


def _integration_config(org: Organization | None) -> dict[str, Any]:
    raw = getattr(org, "integration_config_json", None) if org is not None else None
    return raw if isinstance(raw, dict) else {}


def _plain_password_from_block(block: dict[str, Any]) -> str:
    enc = str(block.get("password_enc") or "").strip()
    if enc:
        try:
            return decrypt_secret(enc).strip()
        except ValueError:
            logger.warning("iiko_office: не удалось расшифровать password_enc")
            return ""
    return str(block.get("password") or "").strip()


def org_has_iiko_office_in_db(org: Organization | None) -> bool:
    if org is None:
        return False
    block = _integration_config(org).get(IIKO_OFFICE_CONFIG_KEY)
    if not isinstance(block, dict):
        return False
    host = str(block.get("host") or "").strip()
    login = str(block.get("login") or "").strip()
    if (block.get("password_enc") or "").strip() and fernet_or_none() is None:
        return False
    password = _plain_password_from_block(block)
    store_id = str(block.get("store_id") or "").strip()
    return bool(host and login and password and store_id)


async def resolve_org_iiko_office_credentials(
    db: AsyncSession,
    organization_id: int,
) -> OrgIikoOfficeCredentials | None:
    org = await db.get(Organization, int(organization_id))
    if org is None:
        return None
    block = _integration_config(org).get(IIKO_OFFICE_CONFIG_KEY)
    if not isinstance(block, dict):
        return None
    host = str(block.get("host") or "").strip().rstrip("/")
    login = str(block.get("login") or "").strip()
    password = _plain_password_from_block(block)
    store_id = str(block.get("store_id") or "").strip()
    department_id = str(block.get("department_id") or "").strip()
    if not host or not login or not password or not store_id:
        return None
    return OrgIikoOfficeCredentials(
        host=host,
        login=login,
        password=password,
        store_id=store_id,
        department_id=department_id,
    )


async def list_organizations_with_iiko_office_db(db: AsyncSession) -> list[Organization]:
    res = await db.execute(select(Organization).where(Organization.is_active.is_(True)))
    return [o for o in res.scalars().all() if org_has_iiko_office_in_db(o)]


def resolve_location_id_for_iiko_office_store(
    org: Organization | None,
    store_id: str,
) -> int | None:
    """
    Маппинг склада iiko Office → ``locations.id`` для мульти-точечных филиалов.

    Приоритет: ``store_location_map[store_id]`` → ``location_id`` (дефолт для store_id блока).
    """
    if org is None:
        return None
    block = _integration_config(org).get(IIKO_OFFICE_CONFIG_KEY)
    if not isinstance(block, dict):
        return None
    sid = (store_id or "").strip()
    smap = block.get("store_location_map")
    if isinstance(smap, dict) and sid:
        raw = smap.get(sid)
        if raw is not None and str(raw).strip() != "":
            try:
                return int(raw)
            except (TypeError, ValueError):
                return None
    raw_loc = block.get("location_id")
    if raw_loc is not None and str(raw_loc).strip() != "":
        try:
            return int(raw_loc)
        except (TypeError, ValueError):
            return None
    return None


def iiko_office_config_public(org: Organization | None) -> dict[str, Any]:
    """Публичное представление конфига (без пароля)."""
    if org is None:
        return {
            "configured": False,
            "host": "",
            "login": "",
            "store_id": "",
            "department_id": "",
            "location_id": None,
            "store_location_map": {},
            "password_set": False,
            "secrets_encrypt_ready": fernet_or_none() is not None,
        }
    block = _integration_config(org).get(IIKO_OFFICE_CONFIG_KEY)
    if not isinstance(block, dict):
        block = {}
    smap = block.get("store_location_map")
    store_location_map: dict[str, int] = {}
    if isinstance(smap, dict):
        for k, v in smap.items():
            if not str(k).strip() or v is None:
                continue
            try:
                store_location_map[str(k).strip()] = int(v)
            except (TypeError, ValueError):
                continue
    loc_raw = block.get("location_id")
    location_id: int | None = None
    if loc_raw is not None and str(loc_raw).strip() != "":
        try:
            location_id = int(loc_raw)
        except (TypeError, ValueError):
            location_id = None
    has_pw = bool(
        (block.get("password_enc") or "").strip() or (block.get("password") or "").strip()
    )
    return {
        "configured": org_has_iiko_office_in_db(org),
        "host": str(block.get("host") or "").strip(),
        "login": str(block.get("login") or "").strip(),
        "store_id": str(block.get("store_id") or "").strip(),
        "department_id": str(block.get("department_id") or "").strip(),
        "location_id": location_id,
        "store_location_map": store_location_map,
        "password_set": has_pw,
        "secrets_encrypt_ready": fernet_or_none() is not None,
    }


def apply_iiko_office_config_patch(
    org: Organization,
    *,
    host: str | None = None,
    login: str | None = None,
    password_plain: str | None = None,
    store_id: str | None = None,
    department_id: str | None = None,
    location_id: int | None = None,
    store_location_map: dict[str, int] | None = None,
    encrypt_password: bool,
) -> dict[str, Any]:
    """Слить patch в ``integration_config_json.iiko_office``; вернуть обновлённый блок."""
    from app.services.secrets_crypto import encrypt_secret

    cfg = dict(org.integration_config_json) if isinstance(org.integration_config_json, dict) else {}
    block = dict(cfg.get(IIKO_OFFICE_CONFIG_KEY) or {})
    if not isinstance(block, dict):
        block = {}

    if host is not None:
        block["host"] = (host or "").strip().rstrip("/")
    if login is not None:
        block["login"] = (login or "").strip()
    if store_id is not None:
        block["store_id"] = (store_id or "").strip()
    if department_id is not None:
        block["department_id"] = (department_id or "").strip()
    if location_id is not None:
        block["location_id"] = int(location_id) if int(location_id) > 0 else None
    if store_location_map is not None:
        block["store_location_map"] = {
            str(k).strip(): int(v)
            for k, v in store_location_map.items()
            if str(k).strip() and v is not None and int(v) > 0
        }

    pwd = (password_plain or "").strip()
    if pwd:
        if encrypt_password:
            block["password_enc"] = encrypt_secret(pwd)
            block.pop("password", None)
        else:
            block["password"] = pwd
            block.pop("password_enc", None)

    cfg[IIKO_OFFICE_CONFIG_KEY] = block
    org.integration_config_json = cfg
    return block
