"""
Админ-панель API.
REST-эндпоинты для входа, WebSocket, демо-данных, настроек, экспорта и тест-бота.
"""

import csv
import io
import logging
from typing import Any
from datetime import date, datetime, time as dt_time, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import delete as sql_delete
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from pydantic import BaseModel, Field

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
    User,
)
from app.db.session import get_db, redis_client
from app.services.demo_data import clear_demo_data, demo_data_exists, seed_demo_data
from app.services.integration_health import build_status_payload
from app.services.chat_log_retention import count_chat_logs_eligible_for_purge, purge_old_chat_logs
from app.services.dialog_mgr import (
    clear_pending_order,
    get_pending_order,
    purge_all_session_keys_for_phone,
)
from app.services.integration_config import (
    ai_provider_configured,
    iiko_effective_configured,
    whatsapp_effective_configured,
)
from app.services.org_iiko import resolve_org_iiko_credentials
from app.services.tenant_scope import (
    failed_tasks_tenant_clause as _failed_tasks_tenant_clause,
    orders_tenant_clause as _orders_tenant_clause,
)
from app.services.intelligence_analytics import order_meta_from_items_json

from .deps import (
    _bookings_tenant_clause,
    _escalation_tenant_clause,
    _integration_events_tenant_clause,
    admin_org_from_session,
    require_admin_session,  # noqa: F401 - re-exported from app.api.admin for compatibility
    require_admin_session_active,
    require_superadmin,
)
from .bookings import bookings_router
from .branding import branding_router
from .chats import chats_router
from .customers import customers_router
from .knowledge import knowledge_router
from .menu_bulk import menu_bulk_router
from .menu_schemas import ClearMenuBody
from .system import system_router

logger = logging.getLogger(__name__)

# ─── Публичные эндпоинты входа (без сессии) ──────────────

router = APIRouter(
    prefix="/admin",
    tags=["Admin Panel"],
    dependencies=[Depends(require_admin_session_active)],
)

router.include_router(menu_bulk_router)
router.include_router(knowledge_router)
router.include_router(branding_router)
router.include_router(bookings_router)
router.include_router(customers_router)
router.include_router(chats_router)
router.include_router(system_router)
# NOTE: rules_router, analytics_router, menu_router, organization_router, orders_router
# have their own prefix="/admin" — they are mounted directly in app.main at /api level.


# WebSocket без cookie-сессии (браузер ограничен) — только подписанный токен
def _order_items_count(items_json: dict | None) -> int:
    if not items_json:
        return 0
    items = items_json.get("items")
    return len(items) if isinstance(items, list) else 0


def _check_mixed_payment_split(items_json: dict | None, total_price: float, *, tol: float = 1.0) -> str | None:
    """Проверяет, совпадает ли сумма частей смешанной оплаты с итогом. None = ОК."""
    meta = order_meta_from_items_json(items_json)
    pd = meta.get("payment_details")
    if not isinstance(pd, dict) or pd.get("type") != "mixed":
        return None
    sp = pd.get("split")
    if not isinstance(sp, dict):
        return None
    split_sum = float(sp.get("cash") or 0) + float(sp.get("card") or 0) + float(sp.get("remote") or 0)
    if abs(split_sum - total_price) > tol:
        return f"Сумма частей оплаты ({split_sum:.0f} ₸) ≠ итог заказа ({total_price:.0f} ₸). Уточните у клиента."
    return None


# ─── Демо-данные (админка) ──────────────────────────────


@router.get("/demo/status")
async def demo_status(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    """Есть ли в БД пакет демо-пользователей (префикс телефона)."""
    oid = admin_org_from_session(request)
    return {"has_demo": await demo_data_exists(db, organization_id=oid)}


@router.post("/demo/seed")
async def demo_seed(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    """Заполнить БД фальшивыми заказами, бронями и чатами (идемпотентно)."""
    oid = admin_org_from_session(request)
    stats = await seed_demo_data(db, organization_id=oid)
    if stats.get("skipped"):
        menu_n = int(stats.get("menu_items_added") or 0)
        if menu_n > 0:
            return {
                "ok": True,
                "partial": True,
                "message": "Демо-клиенты уже в БД; добавлено меню (позиций не было).",
                "menu_items_added": menu_n,
            }
        raise HTTPException(
            status_code=409,
            detail="Демо-данные уже есть. Сначала удалите их кнопкой «Удалить демо».",
        )
    return {"ok": True, **{k: v for k, v in stats.items() if k != "skipped"}}


async def _demo_delete_core(db: AsyncSession, organization_id: int) -> dict:
    """Общая логика удаления демо (БД + Redis-ключи сессий)."""
    if not await demo_data_exists(db, organization_id=organization_id):
        raise HTTPException(status_code=404, detail="Демо-данных нет")
    cleared = await clear_demo_data(db, organization_id=organization_id)
    return {"ok": True, **cleared}


@router.delete("/demo")
async def demo_delete(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    """Удалить всех демо-пользователей и связанные заказы/брони/логи."""
    return await _demo_delete_core(db, admin_org_from_session(request))


@router.post("/demo/delete")
async def demo_delete_post(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    """
    То же, что DELETE /admin/demo.
    Нужен для сред, где HTTP DELETE режется прокси/CDN (удаление «не работает», а POST проходит).
    """
    return await _demo_delete_core(db, admin_org_from_session(request))

# ─── Настройки: опасные операции с БД ───────────────────

SETTINGS_PURGE_PHRASE = "УДАЛИТЬ ВСЕ ДАННЫЕ"


class PurgeOperationalBody(BaseModel):
    """Сброс операционных данных (заказы, чаты, брони и т.д.) без удаления клиентов ``users``."""

    confirm: bool = Field(False, description="Должно быть true")
    phrase: str = Field("", description="Точная фраза подтверждения")


class DeleteOrdersBulkBody(BaseModel):
    """Удаление заказов по списку id (и сброс Redis pending_order при совпадении)."""

    confirm: bool = Field(False, description="Должно быть true")
    order_ids: list[int] = Field(..., min_length=1, max_length=80)


class DeleteSingleOrderBody(BaseModel):
    confirm: bool = Field(False, description="Должно быть true")


class RetentionRunBody(BaseModel):
    """Разовый запуск политики ретеншна chat_logs вручную."""

    confirm: bool = Field(False, description="Должно быть true")


def _sql_delete_rowcount(res) -> int:
    n = res.rowcount
    return int(n) if n is not None and n >= 0 else 0


async def _clear_redis_pending_if_matches(
    phone: str | None,
    order_id: int,
    organization_id: int | None = None,
) -> None:
    """Если в Redis висит черновик этого заказа — снять, чтобы клиент не застрял на мёртвом id."""
    if not phone:
        return
    try:
        pid = await get_pending_order(redis_client, phone, organization_id=organization_id)
        if pid == order_id:
            await clear_pending_order(redis_client, phone, organization_id=organization_id)
    except Exception:
        logger.exception("Redis: не удалось сбросить pending_order для %s", phone)


@router.post("/settings/purge-operational-data")
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
    ``integration_health`` (id=1) — глобальный singleton для всей платформы, при очистке
    одного филиала **не** сбрасывается.

    Требуются ``confirm: true`` и фраза «УДАЛИТЬ ВСЕ ДАННЫЕ».
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


@router.post("/settings/clear-menu-and-stop-snapshot")
async def clear_menu_and_stop_snapshot(
    request: Request,
    body: ClearMenuBody,
    _perm: None = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Удалить строки ``menu_items`` с ``organization_id`` текущего филиала (как ``POST /menu/clear``).

    Строки с ``organization_id IS NULL`` (legacy) **не** трогаем — иначе франшиза сотрёт общую
    номенклатуру платформы. Отдельной таблицы стоп-листа в БД нет. ``integration_health``
    глобальный — не сбрасываем, чтобы не ломать индикаторы других филиалов.
    """
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


# ─── Даты заказов (UTC) — общие для /stats и /analytics ───


def _dt_as_utc(dt: datetime) -> datetime:
    """SQLite часто отдаёт naive datetime — интерпретируем как UTC (единая ось графиков)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _order_day_key_utc(created_at: datetime | None) -> str | None:
    if created_at is None:
        return None
    return _dt_as_utc(created_at).strftime("%Y-%m-%d")


def _sql_dt_for_filter(dt: datetime) -> datetime:
    """
    SQLite хранит naive datetime; сравнение с aware в WHERE даёт пустые выборки
    (особенно узкое окно «сегодня»). Postgres оставляем с tz-aware UTC.
    """
    u = _dt_as_utc(dt)
    if settings.db_mode == "sqlite":
        return u.replace(tzinfo=None)
    return u

MAX_CSV_EXPORT_ROWS = 50_000


def _export_range_utc(date_from: date | None, date_to: date | None) -> tuple[datetime, datetime]:
    """
    Полуинтервал [lo, hi) в UTC для фильтрации по created_at.
    По умолчанию — последние 90 суток.
    """
    today = datetime.now(timezone.utc).date()
    df = date_from or (today - timedelta(days=90))
    dt_end = date_to or today
    if df > dt_end:
        raise HTTPException(status_code=400, detail="date_from не может быть позже date_to")
    lo = datetime.combine(df, dt_time.min, tzinfo=timezone.utc)
    hi_excl = datetime.combine(dt_end, dt_time.min, tzinfo=timezone.utc) + timedelta(days=1)
    return lo, hi_excl


class RedisPurgePhoneBody(BaseModel):
    """Сброс ключей Redis/InMemory-сессии по номеру (без изменений в БД)."""
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

@router.post("/settings/redis-purge-phone")
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


@router.post("/settings/chat-logs/run-retention")
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


@router.get("/export/orders")
async def export_orders_csv(
    request: Request,
    date_from: date | None = Query(None, description="Начало периода (UTC, дата)"),
    date_to: date | None = Query(None, description="Конец периода включительно (UTC, дата)"),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """CSV заказов за период (UTF-8 с BOM для Excel)."""
    org_id = admin_org_from_session(request)
    lo, hi_excl = _export_range_utc(date_from, date_to)
    lo_sql = _sql_dt_for_filter(lo)
    hi_sql = _sql_dt_for_filter(hi_excl)

    res = await db.execute(
        select(Order, User.phone, User.name)
        .join(User, Order.user_id == User.id)
        .where(
            _orders_tenant_clause(org_id),
            User.organization_id == org_id,
            Order.created_at >= lo_sql,
            Order.created_at < hi_sql,
        )
        .order_by(Order.id.asc())
        .limit(MAX_CSV_EXPORT_ROWS + 1),
    )
    rows = res.all()
    if len(rows) > MAX_CSV_EXPORT_ROWS:
        raise HTTPException(
            status_code=400,
            detail=f"Слишком много строк (> {MAX_CSV_EXPORT_ROWS}). Сузьте период.",
        )

    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(
        [
            "order_id",
            "user_id",
            "user_phone",
            "user_name",
            "status",
            "total_price",
            "order_type",
            "created_at_utc",
            "updated_at_utc",
        ],
    )
    for o, phone, name in rows:
        meta = order_meta_from_items_json(o.items_json if isinstance(o.items_json, dict) else None)
        w.writerow(
            [
                o.id,
                o.user_id,
                phone or "",
                name or "",
                o.status or "",
                float(o.total_price),
                meta.get("order_type") or "",
                o.created_at.isoformat() if o.created_at else "",
                o.updated_at.isoformat() if o.updated_at else "",
            ],
        )

    return Response(
        content=buf.getvalue().encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="restomind_orders_export.csv"',
        },
    )


@router.get("/export/chats")
async def export_chats_csv(
    request: Request,
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """CSV сообщений chat_logs за период (роль, телефон клиента)."""
    org_id = admin_org_from_session(request)
    lo, hi_excl = _export_range_utc(date_from, date_to)
    lo_sql = _sql_dt_for_filter(lo)
    hi_sql = _sql_dt_for_filter(hi_excl)

    res = await db.execute(
        select(ChatLog, User.phone)
        .join(User, ChatLog.user_id == User.id)
        .where(
            User.organization_id == org_id,
            ChatLog.created_at >= lo_sql,
            ChatLog.created_at < hi_sql,
        )
        .order_by(ChatLog.id.asc())
        .limit(MAX_CSV_EXPORT_ROWS + 1),
    )
    rows = res.all()
    if len(rows) > MAX_CSV_EXPORT_ROWS:
        raise HTTPException(
            status_code=400,
            detail=f"Слишком много строк (> {MAX_CSV_EXPORT_ROWS}). Сузьте период.",
        )

    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["log_id", "user_id", "user_phone", "role", "created_at_utc", "content"])
    for cl, phone in rows:
        w.writerow(
            [
                cl.id,
                cl.user_id,
                phone or "",
                cl.role or "",
                cl.created_at.isoformat() if cl.created_at else "",
                (cl.content or "").replace("\r\n", "\n").replace("\r", "\n"),
            ],
        )

    return Response(
        content=buf.getvalue().encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="restomind_chats_export.csv"',
        },
    )


# ─── Тест бота (без WhatsApp) ────────────────────────────
