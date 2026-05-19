"""Единые проверки «интеграция настроена» для статуса, инцидентов и онбординга."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import Organization
from app.services.org_iiko import resolve_org_iiko_credentials


def iiko_env_configured() -> bool:
    return bool(
        str(settings.iiko_api_login or "").strip()
        and str(settings.iiko_organization_id or "").strip()
    )


async def iiko_effective_configured(db: AsyncSession, org_id: int) -> bool:
    """Креды iiko в БД филиала или глобальный fallback .env."""
    creds = await resolve_org_iiko_credentials(db, org_id)
    if creds is not None:
        return True
    return iiko_env_configured()


def whatsapp_token_configured() -> bool:
    return bool(str(settings.whatsapp_api_token or "").strip())


async def whatsapp_effective_configured(db: AsyncSession, org_id: int) -> bool:
    """
    WhatsApp готов к исходящим: токен в .env и phone_number_id в .env или в Organization.
    """
    if not whatsapp_token_configured():
        return False
    org = await db.get(Organization, org_id)
    org_phone = (org.whatsapp_phone_number_id or "").strip() if org is not None else ""
    env_phone = str(settings.whatsapp_phone_number_id or "").strip()
    return bool(org_phone or env_phone)


def ai_provider_configured() -> bool:
    """True если хотя бы один AI-провайдер имеет ключ — независимо от AI_PROVIDER настройки."""
    if bool(str(settings.gemini_api_key or "").strip()):
        return True
    if bool(str(settings.openai_api_key or "").strip()):
        return True
    return False


def ai_active_provider() -> str:
    """Возвращает имя реально активного провайдера для отображения в UI."""
    provider = (settings.ai_provider or "").strip().lower()
    if provider == "gemini" or bool(str(settings.gemini_api_key or "").strip()):
        return "gemini"
    return "openai"
