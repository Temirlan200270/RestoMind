"""
Сводная проверка готовности окружения для экрана «Состояние» в админке.
Без утечки секретов — только флаги и маски.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import InMemoryRedis, redis_client
from app.services.integration_health import build_status_payload
from app.services.org_iiko import resolve_org_iiko_credentials

logger = logging.getLogger(__name__)


def _iiko_env_fallback_configured() -> bool:
    return bool(str(settings.iiko_api_login or "").strip() and str(settings.iiko_organization_id or "").strip())


async def _iiko_effective_configured(db: AsyncSession, org_id: int) -> bool:
    c = await resolve_org_iiko_credentials(db, org_id)
    if c is not None:
        return True
    return _iiko_env_fallback_configured()


def _whatsapp_env_configured() -> bool:
    return bool(
        str(settings.whatsapp_api_token or "").strip()
        and str(settings.whatsapp_phone_number_id or "").strip()
    )


async def build_admin_readiness_payload(db: AsyncSession, org_id: int) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    try:
        await db.execute(text("SELECT 1"))
        checks.append({"id": "database", "ok": True, "label": "База данных", "detail": "Запрос выполнен"})
    except Exception as exc:
        checks.append({"id": "database", "ok": False, "label": "База данных", "detail": str(exc)[:220]})

    redis_ok = False
    redis_detail = ""
    try:
        pong = await redis_client.ping()
        redis_ok = bool(pong)
        if isinstance(redis_client, InMemoryRedis):
            redis_detail = "In-memory (не кластерный Redis)"
        elif settings.redis_memory_only:
            redis_detail = "REDIS_MEMORY_ONLY — локальная память процесса"
        elif settings.redis_enabled:
            redis_detail = "TCP Redis отвечает"
        else:
            redis_detail = "Redis отключён — заглушка в памяти"
    except Exception as exc:
        redis_detail = str(exc)[:220]
    checks.append({"id": "redis", "ok": redis_ok, "label": "Redis / PubSub", "detail": redis_detail})

    alembic_version: str | None = None
    alembic_detail = ""
    try:
        raw = await db.scalar(text("SELECT version_num FROM alembic_version LIMIT 1"))
        alembic_version = str(raw).strip() if raw else None
        alembic_detail = alembic_version or "Пусто"
    except Exception as exc:
        alembic_detail = f"Таблица недоступна: {str(exc)[:120]}"
    checks.append(
        {
            "id": "alembic",
            "ok": alembic_version is not None,
            "label": "Миграции Alembic",
            "detail": alembic_detail,
        },
    )

    iiko_ok = await _iiko_effective_configured(db, org_id)
    wa_ok = _whatsapp_env_configured()
    integ = await build_status_payload(
        db,
        iiko_configured=iiko_ok,
        whatsapp_configured=wa_ok,
    )
    menu_ok = True
    slot_m = integ.get("last_menu_sync") if isinstance(integ, dict) else None
    if isinstance(slot_m, dict) and slot_m.get("at") and not slot_m.get("ok"):
        menu_ok = False
    checks.append(
        {
            "id": "iiko_org",
            "ok": iiko_ok,
            "label": "Креды iiko (филиал или env)",
            "detail": "Настроено" if iiko_ok else "Нет apiLogin / organization id",
        },
    )
    checks.append(
        {
            "id": "whatsapp_api",
            "ok": wa_ok,
            "label": "WhatsApp Cloud API",
            "detail": "Токен и phone_number_id заданы" if wa_ok else "Задайте WHATSAPP_API_TOKEN и WHATSAPP_PHONE_NUMBER_ID",
        },
    )
    checks.append(
        {
            "id": "last_menu_sync",
            "ok": menu_ok,
            "label": "Последняя синхронизация меню",
            "detail": (slot_m.get("error") or "OK")[:220] if isinstance(slot_m, dict) else "Нет данных",
        },
    )

    ai_provider = (settings.ai_provider or "openai").strip().lower()
    ai_ok = (
        bool(str(settings.gemini_api_key or "").strip())
        if ai_provider == "gemini"
        else bool(str(settings.openai_api_key or "").strip())
    )
    checks.append(
        {
            "id": "ai_provider",
            "ok": ai_ok,
            "label": f"AI ({ai_provider})",
            "detail": "Ключ задан" if ai_ok else "Задайте ключ провайдера",
        },
    )

    pub = (settings.public_base_url or "").strip().rstrip("/")
    pub_ok = bool(pub)
    checks.append(
        {
            "id": "public_base_url",
            "ok": pub_ok,
            "label": "PUBLIC_BASE_URL",
            "detail": pub if pub_ok else "Не задан — webhook URL в интеграциях будет некорректен",
        },
    )

    hmac_ok = True
    if settings.is_prod_like:
        hmac_ok = bool(str(settings.payment_webhook_hmac_secret or "").strip())
    checks.append(
        {
            "id": "payment_webhook_hmac",
            "ok": hmac_ok,
            "label": "Подпись webhook оплаты (HMAC)",
            "detail": "Задано" if hmac_ok else ("Обязательно в prod-like" if settings.is_prod_like else "Опционально в dev"),
        },
    )

    wa_secret_ok = True
    if settings.is_prod_like:
        wa_secret_ok = bool(str(settings.whatsapp_app_secret or "").strip())
    checks.append(
        {
            "id": "whatsapp_app_secret",
            "ok": wa_secret_ok,
            "label": "WhatsApp App Secret",
            "detail": "Задано" if wa_secret_ok else ("Рекомендуется в prod-like" if settings.is_prod_like else "Опционально"),
        },
    )

    fernet_ok = bool(str(settings.app_secrets_fernet_key or "").strip())
    checks.append(
        {
            "id": "app_secrets_fernet",
            "ok": fernet_ok,
            "label": "Шифрование секретов (Fernet)",
            "detail": "Ключ задан" if fernet_ok else "APP_SECRETS_FERNET_KEY не задан",
        },
    )

    sqlite_prod_bad = settings.db_mode == "sqlite" and settings.is_prod_like
    checks.append(
        {
            "id": "db_mode_prod",
            "ok": not sqlite_prod_bad,
            "label": "Режим БД для production",
            "detail": "SQLite при prod-like — риск" if sqlite_prod_bad else f"DB_MODE={settings.db_mode}",
        },
    )

    payment_hook_url = f"{pub}/api/webhooks/payment" if pub else None
    whatsapp_hook_url = f"{pub}/api/whatsapp/webhook" if pub else None

    overall_ok = all(c.get("ok") for c in checks)

    return {
        "ok": overall_ok,
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "checks": checks,
        "alembic_version": alembic_version,
        "links": {
            "payment_webhook_url": payment_hook_url,
            "whatsapp_webhook_url": whatsapp_hook_url,
            "public_base_url": pub or None,
        },
        "environment": {
            "app_environment": settings.app_environment,
            "is_prod_like": settings.is_prod_like,
            "db_mode": settings.db_mode,
            "ai_provider": ai_provider,
        },
    }
