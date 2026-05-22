"""Dangerous settings ops + environment snapshot for admin (E0.1 tail)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete as sql_delete
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import (
    Booking,
    ChatLog,
    EscalationEvent,
    FailedTask,
    IntegrationEvent,
    MenuItem,
    Order,
    Organization,
)
from app.db.session import get_db, redis_client
from app.services.chat_log_retention import count_chat_logs_eligible_for_purge, purge_old_chat_logs
from app.services.dialog_mgr import purge_all_session_keys_for_phone
from app.services.integration_config import (
    ai_provider_configured,
    iiko_effective_configured,
    whatsapp_effective_configured,
)
from app.services.integration_health import build_status_payload
from app.services.tenant_scope import (
    failed_tasks_tenant_clause as _failed_tasks_tenant_clause,
    orders_tenant_clause as _orders_tenant_clause,
)

from .deps import (
    _bookings_tenant_clause,
    _escalation_tenant_clause,
    _integration_events_tenant_clause,
    admin_org_from_session,
    require_admin_session_active,
    require_superadmin,
)
from .menu_schemas import ClearMenuBody

logger = logging.getLogger(__name__)

settings_ops_router = APIRouter(dependencies=[Depends(require_admin_session_active)])

SETTINGS_PURGE_PHRASE = "УДАЛИТЬ ВСЕ ДАННЫЕ"


class PurgeOperationalBody(BaseModel):
    """Сброс операционных данных (заказы, чаты, брони и т.д.) без удаления клиентов ``users``."""

    confirm: bool = Field(False, description="Должно быть true")
    phrase: str = Field("", description="Точная фраза подтверждения")


class RetentionRunBody(BaseModel):
    """Разовый запуск политики ретеншна chat_logs вручную."""

    confirm: bool = Field(False, description="Должно быть true")


class RedisPurgePhoneBody(BaseModel):
    """Сброс ключей Redis/InMemory-сессии по номеру (без изменений в БД)."""

    confirm: bool = Field(False, description="Должно быть true")
    phone: str = Field("", description="Телефон клиента (+7700…)")


def _sql_delete_rowcount(res) -> int:
    n = res.rowcount
    return int(n) if n is not None and n >= 0 else 0


@settings_ops_router.get("/settings/environment")
async def settings_environment(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    """
    Безопасный снимок окружения для админки (без секретов и полных токенов).
    """
    org_id = admin_org_from_session(request)
    iiko_ok = await iiko_effective_configured(db, org_id)
    wa_ok = await whatsapp_effective_configured(db, org_id)
    integ = await build_status_payload(
        db,
        organization_id=int(org_id),
        iiko_configured=iiko_ok,
        whatsapp_configured=wa_ok,
    )
    org_row = await db.get(Organization, org_id)
    tg_token_ok = bool(str(settings.telegram_bot_token or "").strip())
    tg_global_chat_ok = bool(str(settings.telegram_admin_chat_id or "").strip())
    tg_org_chat_ok = bool(
        str(getattr(org_row, "telegram_ops_chat_id", "") or "").strip(),
    ) if org_row is not None else False
    telegram_staff_reachable = tg_token_ok and (tg_global_chat_ok or tg_org_chat_ok)
    elig = await count_chat_logs_eligible_for_purge(db)
    return {
        "app_name": settings.app_name,
        "app_version": settings.app_version,
        "app_debug": settings.app_debug,
        "app_environment": settings.app_environment,
        "is_prod_like": settings.is_prod_like,
        "db_mode": settings.db_mode,
        "redis_enabled": settings.redis_enabled,
        "redis_memory_only": settings.redis_memory_only,
        "redis_backend": "redis" if settings.redis_enabled else "in_memory",
        "integrations": {
            "iiko": {
                "configured": iiko_ok,
                "terminal_group_id_set": bool(str(settings.iiko_terminal_group_id or "").strip()),
            },
            "whatsapp": {
                "configured": wa_ok,
                "phone_number_id_set": bool(
                    str(getattr(org_row, "whatsapp_phone_number_id", "") or "").strip()
                    or str(settings.whatsapp_phone_number_id or "").strip()
                ),
            },
            "telegram": {
                "configured": telegram_staff_reachable,
                "bot_token_set": tg_token_ok,
                "default_chat_set": tg_global_chat_ok,
                "org_chat_set": tg_org_chat_ok,
            },
            "openai": {"configured": bool(str(settings.openai_api_key or "").strip())},
            "gemini": {"configured": bool(str(settings.gemini_api_key or "").strip())},
            "ai_active_configured": ai_provider_configured(),
            "ai_provider": (settings.ai_provider or "openai").strip().lower(),
            "public_base_url_set": bool(str(settings.public_base_url or "").strip()),
        },
        "integration_health": {
            "last_stoplist": integ.get("last_stoplist"),
            "last_menu_sync": integ.get("last_menu_sync"),
        },
        "chat_log_retention": {
            "enabled": settings.chat_log_retention_days > 0,
            "retention_days": settings.chat_log_retention_days,
            "interval_seconds": settings.chat_log_retention_interval_seconds,
            "eligible_for_purge_count": elig,
        },
        "loyalty": {
            "enabled": settings.loyalty_enabled,
            "points_per_kzt": settings.loyalty_points_per_kzt,
        },
    }


@settings_ops_router.post("/settings/purge-operational-data")
async def purge_operational_data(
    request: Request,
    body: PurgeOperationalBody,
    _perm: None = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Удалить операционные записи **текущего филиала** (сессия): ``chat_logs``, ``orders``,
    ``bookings``, ``escalation_events``, ``integration_events``, ``failed_tasks``.

    Таблицы ``users``, ``menu_items``, ``organizations`` **не** трогаются.
    """
    if not body.confirm:
        raise HTTPException(
            status_code=400,
            detail='Передайте {"confirm": true, "phrase": "УДАЛИТЬ ВСЕ ДАННЫЕ"}',
        )
    if (body.phrase or "").strip() != SETTINGS_PURGE_PHRASE:
        raise HTTPException(
            status_code=400,
            detail=f"Введите фразу подтверждения: {SETTINGS_PURGE_PHRASE}",
        )

    org_id = admin_org_from_session(request)

    r_chat = await db.execute(sql_delete(ChatLog).where(ChatLog.organization_id == org_id))
    r_ord = await db.execute(sql_delete(Order).where(_orders_tenant_clause(org_id)))
    r_book = await db.execute(sql_delete(Booking).where(_bookings_tenant_clause(org_id)))
    r_esc = await db.execute(sql_delete(EscalationEvent).where(_escalation_tenant_clause(org_id)))
    r_int = await db.execute(sql_delete(IntegrationEvent).where(_integration_events_tenant_clause(org_id)))
    r_ft = await db.execute(sql_delete(FailedTask).where(_failed_tasks_tenant_clause(org_id)))

    await db.commit()
    logger.warning(
        "Админ: сброс операционных данных филиала org_id=%s (чаты/заказы/брони/эскалации/интеграции/failed_tasks)",
        org_id,
    )
    return {
        "ok": True,
        "organization_id": org_id,
        "chat_logs_deleted": _sql_delete_rowcount(r_chat),
        "orders_deleted": _sql_delete_rowcount(r_ord),
        "bookings_deleted": _sql_delete_rowcount(r_book),
        "escalation_events_deleted": _sql_delete_rowcount(r_esc),
        "integration_events_deleted": _sql_delete_rowcount(r_int),
        "failed_tasks_deleted": _sql_delete_rowcount(r_ft),
    }


@settings_ops_router.post("/settings/clear-menu-and-stop-snapshot")
async def clear_menu_and_stop_snapshot(
    request: Request,
    body: ClearMenuBody,
    _perm: None = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Удалить строки ``menu_items`` с ``organization_id`` текущего филиала."""
    if not body.confirm:
        raise HTTPException(
            status_code=400,
            detail='Для очистки передайте в теле JSON: {"confirm": true}',
        )
    org_id = admin_org_from_session(request)
    cnt = (
        await db.scalar(
            select(func.count()).select_from(MenuItem).where(MenuItem.organization_id == org_id),
        )
        or 0
    )
    await db.execute(sql_delete(MenuItem).where(MenuItem.organization_id == org_id))
    await db.commit()
    logger.warning(
        "Админ: очистка menu_items филиала org_id=%s, позиций: %d",
        org_id,
        int(cnt),
    )
    return {"ok": True, "organization_id": org_id, "menu_items_deleted": int(cnt)}


@settings_ops_router.post("/settings/redis-purge-phone")
async def redis_purge_phone(
    request: Request,
    body: RedisPurgePhoneBody,
    _perm: None = Depends(require_superadmin),
) -> dict:
    """Удалить из Redis/in-memory ключи chat:history, user:state, pending_order/booking для номера."""
    if not body.confirm:
        raise HTTPException(
            status_code=400,
            detail='Передайте {"confirm": true, "phone": "+7700..."}',
        )
    phone = (body.phone or "").strip()
    if not phone:
        raise HTTPException(status_code=400, detail="Укажите телефон")
    await purge_all_session_keys_for_phone(
        redis_client, phone, organization_id=admin_org_from_session(request),
    )
    logger.warning("Админ: сброшена Redis-сессия для %s", phone[:6] + "…")
    return {"ok": True, "phone": phone}


@settings_ops_router.post("/settings/chat-logs/run-retention")
async def run_chat_log_retention_manual(
    body: RetentionRunBody,
    _perm: None = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Разовый запуск политики ретеншна (та же, что в фоне по расписанию)."""
    if not body.confirm:
        raise HTTPException(status_code=400, detail='Передайте {"confirm": true}')
    if settings.chat_log_retention_days <= 0:
        raise HTTPException(
            status_code=400,
            detail="Ретеншн выключен: задайте CHAT_LOG_RETENTION_DAYS > 0 в .env",
        )
    n = await purge_old_chat_logs(db)
    await db.commit()
    return {"ok": True, "deleted": n, "retention_days": settings.chat_log_retention_days}
