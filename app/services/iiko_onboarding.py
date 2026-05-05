"""Проверка apiLogin и первичная привязка организации iiko (без сохранения ключа до setup)."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Organization
from app.integrations.iiko_client import IikoClient
from app.services.menu_sync import sync_menu_from_iiko
from app.services.secrets_crypto import encrypt_secret


async def verify_iiko_api_login(api_login: str) -> list[dict[str, Any]]:
    """Список организаций, доступных по ключу (ничего не пишет в БД)."""
    login = (api_login or "").strip()
    if not login:
        return []
    async with IikoClient(api_login=login) as client:
        orgs = await client.get_organizations()
    out: list[dict[str, Any]] = []
    for row in orgs:
        if not isinstance(row, dict):
            continue
        oid = (row.get("id") or "").strip()
        name = (row.get("name") or "").strip() or oid
        if oid:
            out.append({"id": oid, "name": name})
    return out


async def setup_organization_iiko(
    db: AsyncSession,
    *,
    organization_id: int,
    api_login_plain: str,
    iiko_organization_uuid: str,
    encrypt_login: bool,
) -> dict[str, int]:
    """
    Сохраняет учётные данные и импортирует меню для ``organization_id`` RestoMind.
    Credentials коммитятся отдельной транзакцией ДО синхронизации меню — чтобы
    ошибка меню (недоступный iiko, таймаут) не откатила сохранённые ключи.
    """
    org = await db.get(Organization, organization_id)
    if org is None:
        raise ValueError("organization not found")

    login = (api_login_plain or "").strip()
    iid = (iiko_organization_uuid or "").strip()
    if not login or not iid:
        raise ValueError("api_login and iiko_organization_id required")

    if encrypt_login:
        org.iiko_api_login_enc = encrypt_secret(login)
        org.iiko_api_login = ""
    else:
        org.iiko_api_login_enc = None
        org.iiko_api_login = login
    org.iiko_organization_id = iid
    # Commit credentials first so they survive a menu-sync failure.
    await db.commit()

    stats = await sync_menu_from_iiko(
        db, login, iid, restomind_organization_id=organization_id,
    )
    return stats
