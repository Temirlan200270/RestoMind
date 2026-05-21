"""Analytics admin API (E0.1 split from _monolith.py)."""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import (
    Booking,
    BusinessRecommendation,
    ChatLog,
    CustomerFeedback,
    EscalationEvent,
    FailedTask,
    MenuItem,
    Order,
    OrderStatus,
    Organization,
    PaymentEvent,
    User,
)
from app.db.session import get_db
from app.services.integration_health import build_status_payload
from app.services.intelligence_analytics import (
    menu_engineering_rows,
    order_meta_from_items_json,
    upsell_stats_from_items_json,
)
from app.services.integration_config import (
    ai_active_provider,
    ai_provider_configured,
    iiko_effective_configured,
    whatsapp_effective_configured,
)
from app.services.analytics_consumer import (
    get_event_stats,
    get_event_stats_for_range,
    get_today_event_summary,
)
from app.services.owner_dashboard import (
    build_recommendation_target,
    build_week_forecast,
    event_revenue_history_usable,
    fetch_daily_revenue_history,
    fetch_daily_revenue_history_from_events,
    rollup_location_event_stats,
    rollup_location_today_summary,
)
from app.services.owner_roi import aggregate_org_window, build_achievements_week, build_today_narrative_ru
from app.services.readiness import build_admin_readiness_payload
from app.services.tenant_scope import (
    allowed_location_ids_for_staff,
    chat_logs_location_filter,
    failed_tasks_tenant_clause as _failed_tasks_tenant_clause,
    orders_location_filter,
    orders_tenant_clause as _orders_tenant_clause,
)
from .deps import (
    _bookings_tenant_clause,
    _escalation_tenant_clause,
    _session_is_superadmin,
    _session_staff_user,
    admin_org_from_session,
    require_admin_session_active,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/admin",
    tags=["Analytics"],
    dependencies=[Depends(require_admin_session_active)],
)


async def _location_scope_for_request(
    request: Request,
    db: AsyncSession,
    org_id: int,
    location_id: int | None,
) -> tuple[set[int] | None, bool]:
    """Return allowed location ids and whether org-level event aggregates are unsafe."""
    staff = await _session_staff_user(request, db)
    is_super = await _session_is_superadmin(request, db)
    allowed = await allowed_location_ids_for_staff(
        db,
        staff=staff,
        org_id=org_id,
        is_superadmin=is_super,
    )
    if location_id is not None and allowed is not None and int(location_id) not in allowed:
        raise HTTPException(status_code=403, detail="Location is not allowed")
    return allowed, bool(location_id is not None or allowed is not None)


def _location_stats_source(location_scoped: bool, event_active: bool) -> str:
    if not location_scoped:
        return "event_driven_or_sql"
    return "event_driven_location" if event_active else "sql_location"


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


_COMPLETED_ORDER_STATUSES = (
    OrderStatus.CONFIRMED.value,
    OrderStatus.SENT_TO_IIKO.value,
    OrderStatus.COMPLETED.value,
)


def _linear_week_forecast(
    daily_series: list[dict[str, Any]],
    *,
    today: date | None = None,
) -> dict[str, Any] | None:
    """Линейная экстраполяция выручки до конца текущей недели (пн–вс, UTC)."""
    if not daily_series:
        return None
    today = today or datetime.now(tz=timezone.utc).date()
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)
    week_days = {
        str(row["date"]): float(row.get("revenue") or 0)
        for row in daily_series
        if isinstance(row, dict)
        and row.get("date")
        and week_start.isoformat() <= str(row["date"]) <= today.isoformat()
    }
    if not week_days:
        return None
    days_elapsed = len(week_days)
    earned = sum(week_days.values())
    daily_avg = earned / days_elapsed if days_elapsed else 0.0
    days_remaining = (week_end - today).days
    forecast = earned + daily_avg * days_remaining
    confidence = "low" if days_elapsed < 3 else ("medium" if days_elapsed < 5 else "high")
    return {
        "forecast_revenue": round(forecast, 2),
        "earned_so_far": round(earned, 2),
        "days_remaining": days_remaining,
        "days_elapsed": days_elapsed,
        "confidence": confidence,
        "daily_avg": round(daily_avg, 2),
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
    }


# ─── Incident constants ──────────────────────────────────

INCIDENT_SAMPLE_LIMIT = 6
INCIDENT_PAYMENT_LOOKBACK_DAYS = 14
INCIDENT_WHATSAPP_LOOKBACK_DAYS = 7


def _incident_dt_iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _incident_short_text(value: Any, limit: int = 220) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _incident_group(
    *,
    group_id: str,
    title: str,
    severity: str,
    items: list[dict[str, Any]],
    count: int | None = None,
    description: str = "",
    action: dict[str, Any] | None = None,
) -> dict:
    return {
        "id": group_id,
        "title": title,
        "severity": severity,
        "description": description,
        "count": int(count if count is not None else len(items)),
        "items": items,
        "action": action or {},
    }


def _incident_severity(counts: dict[str, int]) -> str:
    if counts.get("critical", 0) > 0:
        return "critical"
    if counts.get("warning", 0) > 0:
        return "warning"
    if counts.get("info", 0) > 0:
        return "info"
    return "ok"


def _build_hero_actions(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """До 4 кнопок «Сейчас»: критичные первыми, затем по убыванию count."""
    rank_map = {"critical": 0, "warning": 1, "info": 2, "ok": 3}
    ranked: list[tuple[tuple[int, int], dict[str, Any]]] = []
    for g in groups:
        n = int(g.get("count") or 0)
        if n <= 0:
            continue
        action = g.get("action") if isinstance(g.get("action"), dict) else {}
        tab = action.get("tab")
        if not tab:
            continue
        sev = str(g.get("severity") or "info")
        r = rank_map.get(sev, 9)
        target: dict[str, Any] = {"tab": tab}
        st = action.get("settingsTab") or action.get("settings_tab")
        if st:
            target["settingsTab"] = st
        title = str(g.get("title") or g.get("id") or "incident")
        ranked.append(
            (
                (r, -n),
                {
                    "id": str(g.get("id") or ""),
                    "label": f"{title} ({n})",
                    "count": n,
                    "severity": sev,
                    "target": target,
                },
            ),
        )
    ranked.sort(key=lambda x: x[0])
    return [x[1] for x in ranked[:4]]


# ─── Routes ──────────────────────────────────────────────


@router.get("/readiness")
async def admin_readiness(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    """Сводка состояния окружения для вкладки «Состояние» (без секретов)."""
    org_id = admin_org_from_session(request)
    out = await build_admin_readiness_payload(db, org_id)
    out["organization_id"] = org_id
    return out


@router.get("/incidents")
async def admin_incidents(
    request: Request,
    db: AsyncSession = Depends(get_db),
    mode: Annotated[
        str,
        Query(description="full — полные группы; summary — только счётчики и hero_actions"),
    ] = "full",
    location_id: Annotated[int | None, Query(ge=1)] = None,
) -> dict:
    """Единый центр того, что требует внимания оператора или владельца платформы."""
    summary_mode = (mode or "full").strip().lower() == "summary"
    org_id = admin_org_from_session(request)
    is_superadmin = await _session_is_superadmin(request, db)
    allowed_location_ids, location_scoped = await _location_scope_for_request(
        request,
        db,
        org_id,
        location_id,
    )
    order_location_scope = orders_location_filter(allowed_location_ids, location_id)
    chat_location_scope = chat_logs_location_filter(allowed_location_ids, location_id)
    now_utc = datetime.now(tz=timezone.utc)
    whatsapp_since = _sql_dt_for_filter(now_utc - timedelta(days=INCIDENT_WHATSAPP_LOOKBACK_DAYS))
    payment_since = _sql_dt_for_filter(now_utc - timedelta(days=INCIDENT_PAYMENT_LOOKBACK_DAYS))
    not_cancelled = Order.status != OrderStatus.CANCELLED.value
    org_orders = _orders_tenant_clause(org_id)

    groups: list[dict[str, Any]] = []

    iiko_where = [
        User.organization_id == org_id,
        org_orders,
        order_location_scope,
        not_cancelled,
        Order.iiko_last_error.isnot(None),
        func.coalesce(Order.iiko_last_error, "") != "",
    ]
    iiko_count = int(
        await db.scalar(
            select(func.count(Order.id))
            .select_from(Order)
            .join(User, Order.user_id == User.id)
            .where(*iiko_where),
        )
        or 0,
    )
    if iiko_count:
        items_iiko: list[dict[str, Any]] = []
        if not summary_mode:
            rows_iiko = (
                await db.execute(
                    select(Order, User.phone, User.name)
                    .join(User, Order.user_id == User.id)
                    .where(*iiko_where)
                    .order_by(func.coalesce(Order.updated_at, Order.created_at).desc(), Order.id.desc())
                    .limit(INCIDENT_SAMPLE_LIMIT),
                )
            ).all()
            items_iiko = [
                {
                    "id": f"order:{o.id}:iiko",
                    "title": f"Заказ #{o.id}",
                    "subtitle": phone or name or "Клиент без телефона",
                    "detail": _incident_short_text(o.iiko_last_error),
                    "created_at": _incident_dt_iso(o.updated_at or o.created_at),
                    "meta": [
                        {"label": "Статус", "value": o.status},
                        {"label": "Сумма", "value": float(o.total_price or 0)},
                    ],
                    "target": {"tab": "orders", "order_id": int(o.id)},
                }
                for o, phone, name in rows_iiko
            ]
        groups.append(
            _incident_group(
                group_id="iiko_failed",
                title="iiko не принял заказ",
                severity="critical",
                count=iiko_count,
                description="Заказ есть в админке, но последняя отправка в iiko завершилась ошибкой.",
                action={"tab": "orders", "label": "Открыть заказы"},
                items=items_iiko,
            ),
        )

    prepay_where = [
        User.organization_id == org_id,
        org_orders,
        order_location_scope,
        not_cancelled,
        Order.prepayment_status == "pending",
    ]
    prepay_count = int(
        await db.scalar(
            select(func.count(Order.id))
            .select_from(Order)
            .join(User, Order.user_id == User.id)
            .where(*prepay_where),
        )
        or 0,
    )
    if prepay_count:
        items_prepay: list[dict[str, Any]] = []
        if not summary_mode:
            rows_prepay = (
                await db.execute(
                    select(Order, User.phone, User.name)
                    .join(User, Order.user_id == User.id)
                    .where(*prepay_where)
                    .order_by(func.coalesce(Order.updated_at, Order.created_at).desc(), Order.id.desc())
                    .limit(INCIDENT_SAMPLE_LIMIT),
                )
            ).all()
            items_prepay = [
                {
                    "id": f"order:{o.id}:prepayment",
                    "title": f"Заказ #{o.id}",
                    "subtitle": phone or name or "Клиент без телефона",
                    "detail": "Предоплата в статусе pending",
                    "created_at": _incident_dt_iso(o.updated_at or o.created_at),
                    "meta": [
                        {"label": "Статус", "value": o.status},
                        {"label": "Сумма", "value": float(o.total_price or 0)},
                    ],
                    "target": {"tab": "orders", "order_id": int(o.id)},
                }
                for o, phone, name in rows_prepay
            ]
        groups.append(
            _incident_group(
                group_id="prepayment_pending",
                title="Ждут предоплату",
                severity="warning",
                count=prepay_count,
                description="Заказы нельзя безопасно отправлять дальше, пока оплата не подтверждена.",
                action={"tab": "orders", "label": "Открыть заказы"},
                items=items_prepay,
            ),
        )

    failed_tasks_where = [
        FailedTask.resolved.is_(False),
        _failed_tasks_tenant_clause(org_id),
    ]
    failed_tasks_count = int(
        await db.scalar(select(func.count(FailedTask.id)).where(*failed_tasks_where)) or 0,
    )
    if failed_tasks_count:
        items_ft: list[dict[str, Any]] = []
        if not summary_mode:
            tasks = (
                await db.execute(
                    select(FailedTask)
                    .where(*failed_tasks_where)
                    .order_by(FailedTask.created_at.desc(), FailedTask.id.desc())
                    .limit(INCIDENT_SAMPLE_LIMIT),
                )
            ).scalars().all()
            items_ft = [
                {
                    "id": f"failed_task:{t.id}",
                    "title": t.phone,
                    "subtitle": _incident_short_text(t.message_text, 120),
                    "detail": _incident_short_text(t.error),
                    "created_at": _incident_dt_iso(t.created_at),
                    "meta": [{"label": "Попыток", "value": int(t.attempts or 0)}],
                    "target": {"tab": "operator_queue", "failed_task_id": int(t.id)},
                }
                for t in tasks
            ]
        groups.append(
            _incident_group(
                group_id="failed_tasks",
                title="WhatsApp/AI обработка сорвалась",
                severity="critical",
                count=failed_tasks_count,
                description="Бот исчерпал retry и ждёт ручной помощи.",
                action={"tab": "operator_queue", "label": "Открыть помощь клиентам"},
                items=items_ft,
            ),
        )

    whatsapp_where = [
        ChatLog.organization_id == org_id,
        User.organization_id == org_id,
        ChatLog.delivery_status == "failed",
        ChatLog.created_at >= whatsapp_since,
        chat_location_scope,
    ]
    whatsapp_count = int(
        await db.scalar(
            select(func.count(ChatLog.id))
            .select_from(ChatLog)
            .join(User, ChatLog.user_id == User.id)
            .where(*whatsapp_where),
        )
        or 0,
    )
    if whatsapp_count:
        items_wa: list[dict[str, Any]] = []
        if not summary_mode:
            rows_wa = (
                await db.execute(
                    select(ChatLog, User.phone, User.name)
                    .join(User, ChatLog.user_id == User.id)
                    .where(*whatsapp_where)
                    .order_by(ChatLog.created_at.desc(), ChatLog.id.desc())
                    .limit(INCIDENT_SAMPLE_LIMIT),
                )
            ).all()
            items_wa = [
                {
                    "id": f"chat_log:{log.id}",
                    "title": phone or name or "Клиент",
                    "subtitle": _incident_short_text(log.content, 140),
                    "detail": _incident_short_text(log.error_details if log.error_details else "Meta вернула failed"),
                    "created_at": _incident_dt_iso(log.created_at),
                    "meta": [{"label": "Сообщение", "value": int(log.id)}],
                    "target": {"tab": "chats", "phone": phone},
                }
                for log, phone, name in rows_wa
            ]
        groups.append(
            _incident_group(
                group_id="whatsapp_failed",
                title="WhatsApp failed",
                severity="warning",
                count=whatsapp_count,
                description=f"Исходящие сообщения со статусом failed за {INCIDENT_WHATSAPP_LOOKBACK_DAYS} дней.",
                action={"tab": "chats", "label": "Открыть диалоги"},
                items=items_wa,
            ),
        )

    payment_where = [
        PaymentEvent.event_type == "webhook_failed",
        PaymentEvent.created_at >= payment_since,
        org_orders,
        order_location_scope,
    ]
    payment_count = int(
        await db.scalar(
            select(func.count(PaymentEvent.id))
            .select_from(PaymentEvent)
            .join(Order, PaymentEvent.order_id == Order.id)
            .outerjoin(User, Order.user_id == User.id)
            .where(*payment_where),
        )
        or 0,
    )
    if payment_count:
        items_pay: list[dict[str, Any]] = []
        if not summary_mode:
            rows_pay = (
                await db.execute(
                    select(PaymentEvent, Order, User.phone, User.name)
                    .join(Order, PaymentEvent.order_id == Order.id)
                    .outerjoin(User, Order.user_id == User.id)
                    .where(*payment_where)
                    .order_by(PaymentEvent.created_at.desc(), PaymentEvent.id.desc())
                    .limit(INCIDENT_SAMPLE_LIMIT),
                )
            ).all()
            items_pay = [
                {
                    "id": f"payment_event:{ev.id}",
                    "title": f"Заказ #{order.id}",
                    "subtitle": phone or name or "Клиент без телефона",
                    "detail": _incident_short_text(ev.note),
                    "created_at": _incident_dt_iso(ev.created_at),
                    "meta": [
                        {"label": "Событие", "value": ev.event_type},
                        {"label": "Сумма", "value": float(ev.amount) if ev.amount is not None else None},
                    ],
                    "target": {"tab": "orders", "order_id": int(order.id)},
                }
                for ev, order, phone, name in rows_pay
            ]
        groups.append(
            _incident_group(
                group_id="payment_webhook",
                title="Webhook оплаты не прошёл сверку",
                severity="critical",
                count=payment_count,
                description=f"Платёжный webhook вернул failed или не совпал с заказом за {INCIDENT_PAYMENT_LOOKBACK_DAYS} дней.",
                action={"tab": "orders", "label": "Открыть заказы"},
                items=items_pay,
            ),
        )

    iiko_configured = await iiko_effective_configured(db, org_id)
    wa_configured = await whatsapp_effective_configured(db, org_id)
    integ = await build_status_payload(
        db,
        organization_id=int(org_id),
        iiko_configured=iiko_configured,
        whatsapp_configured=wa_configured,
    )
    integration_items: list[dict[str, Any]] = []
    if not integ.get("iiko_configured"):
        integration_items.append(
            {
                "id": "integration:iiko_config",
                "title": "iiko не настроен",
                "subtitle": "Заказы не смогут уходить на кухню автоматически.",
                "detail": "Проверьте подключение филиала в настройках.",
                "created_at": None,
                "target": {"tab": "settings", "settings_tab": "connections"},
            },
        )
    _sync_grace = timedelta(hours=2)
    for key, title in (("last_menu_sync", "Синхронизация меню iiko"), ("last_stoplist", "Синхронизация стоп-листа iiko")):
        slot = integ.get(key) if isinstance(integ, dict) else None
        if not slot or not slot.get("at") or slot.get("ok"):
            continue
        try:
            slot_at = datetime.fromisoformat(slot["at"])
            if slot_at.tzinfo is None:
                slot_at = slot_at.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            slot_at = None
        if slot_at and (now_utc - slot_at) > _sync_grace:
            # Ошибка старше 2 часов — скорее всего воркер не запущен, не падаем в degraded
            continue
        integration_items.append(
            {
                "id": f"integration:{key}",
                "title": title,
                "subtitle": "Последняя синхронизация завершилась ошибкой.",
                "detail": _incident_short_text(slot.get("error") or "Ошибка без текста"),
                "created_at": slot.get("at"),
                "target": {"tab": "settings", "settings_tab": "connections"},
            },
        )
    if not integ.get("whatsapp_configured"):
        integration_items.append(
            {
                "id": "integration:whatsapp_config",
                "title": "WhatsApp не настроен",
                "subtitle": "Бот не сможет отправлять ответы гостям.",
                "detail": "Проверьте токен и phone_number_id.",
                "created_at": None,
                "target": {"tab": "settings", "settings_tab": "connections"},
            },
        )
    if not ai_provider_configured():
        integration_items.append(
            {
                "id": "integration:ai_config",
                "title": "AI не настроен",
                "subtitle": "Бот не сможет стабильно разбирать заявки и отвечать гостям.",
                "detail": f"Активный провайдер: {ai_active_provider()} — API-ключ не найден",
                "created_at": None,
                "target": {"tab": "settings", "settings_tab": "technical"},
            },
        )
    if integration_items:
        groups.append(
            _incident_group(
                group_id="integrations_degraded",
                title="Интеграции degraded",
                severity="warning",
                description="Сервис работает, но часть внешних подключений требует проверки.",
                action={"tab": "settings", "settingsTab": "connections", "label": "Открыть подключения"},
                items=integration_items,
            ),
        )

    super_items: list[dict[str, Any]] = []
    if settings.db_mode == "sqlite" and settings.is_prod_like:
        super_items.append(
            {
                "id": "system:sqlite_prod",
                "title": "Production работает на SQLite",
                "subtitle": "Это риск потери данных и блокировок при нагрузке.",
                "detail": "Переведите DATABASE_URL/DB_MODE на PostgreSQL.",
                "severity": "critical",
            },
        )
    if settings.redis_memory_only or not settings.redis_enabled:
        super_items.append(
            {
                "id": "system:redis_memory",
                "title": "Redis заменён in-memory хранилищем",
                "subtitle": "Сессии, pending_order и Pub/Sub живут только внутри процесса.",
                "detail": "Для production нужен внешний Redis.",
                "severity": "warning",
            },
        )
    if settings.is_prod_like and not str(settings.payment_webhook_hmac_secret or "").strip():
        super_items.append(
            {
                "id": "system:payment_hmac",
                "title": "Нет HMAC-секрета для webhook оплаты",
                "subtitle": "В production подпись webhook должна быть обязательной.",
                "detail": "Задайте PAYMENT_WEBHOOK_HMAC_SECRET.",
                "severity": "critical",
            },
        )
    if settings.is_prod_like and not str(settings.whatsapp_app_secret or "").strip():
        super_items.append(
            {
                "id": "system:whatsapp_signature",
                "title": "Нет App Secret для WhatsApp webhook",
                "subtitle": "Входящие webhook сложнее проверять на подлинность.",
                "detail": "Задайте WHATSAPP_APP_SECRET/META_APP_SECRET.",
                "severity": "critical",
            },
        )
    if not str(settings.public_base_url or "").strip():
        super_items.append(
            {
                "id": "system:public_base_url",
                "title": "PUBLIC_BASE_URL не задан",
                "subtitle": "В админке и интеграциях не будет корректного публичного webhook URL.",
                "detail": "Задайте публичный HTTPS-адрес приложения.",
                "severity": "warning",
            },
        )
    if not str(settings.app_secrets_fernet_key or "").strip():
        super_items.append(
            {
                "id": "system:fernet_key",
                "title": "Нет ключа шифрования секретов iiko",
                "subtitle": "Новые iiko apiLogin лучше хранить зашифрованными.",
                "detail": "Задайте APP_SECRETS_FERNET_KEY.",
                "severity": "warning",
            },
        )

    if is_superadmin and super_items:
        platform_items = [] if summary_mode else super_items
        groups.append(
            _incident_group(
                group_id="platform_risks",
                title="Опасные настройки платформы",
                severity="critical" if any(i.get("severity") == "critical" for i in super_items) else "warning",
                description="Этот блок виден только Super Admin.",
                action={"tab": "settings", "settingsTab": "technical", "label": "Открыть техдоступ"},
                count=len(super_items),
                items=platform_items,
            ),
        )

    summary = {"critical": 0, "warning": 0, "info": 0, "restricted": 0}
    total_open = 0
    for g in groups:
        n = int(g.get("count") or 0)
        total_open += n
        sev = str(g.get("severity") or "info")
        if sev in summary:
            summary[sev] += n
        else:
            summary["info"] += n
    if not is_superadmin:
        summary["restricted"] = len(super_items)

    hero_actions = _build_hero_actions(groups)

    if summary_mode:
        return {
            "ok": True,
            "mode": "summary",
            "organization_id": org_id,
            "generated_at": now_utc.isoformat(),
            "is_superadmin": is_superadmin,
            "severity": _incident_severity(summary),
            "total_open": total_open,
            "summary": summary,
            "restricted_count": int(summary["restricted"]),
            "hero_actions": hero_actions,
            "location_scope": {
                "location_id": int(location_id) if location_id is not None else None,
                "source": "sql_location" if location_scoped else "org",
            },
            "lookback_days": {
                "whatsapp_failed": INCIDENT_WHATSAPP_LOOKBACK_DAYS,
                "payment_webhook": INCIDENT_PAYMENT_LOOKBACK_DAYS,
            },
        }

    return {
        "ok": True,
        "organization_id": org_id,
        "generated_at": now_utc.isoformat(),
        "is_superadmin": is_superadmin,
        "severity": _incident_severity(summary),
        "total_open": total_open,
        "summary": summary,
        "restricted_count": int(summary["restricted"]),
        "hero_actions": hero_actions,
        "groups": groups,
        "superadmin_only": super_items if is_superadmin else [],
        "location_scope": {
            "location_id": int(location_id) if location_id is not None else None,
            "source": "sql_location" if location_scoped else "org",
        },
        "lookback_days": {
            "whatsapp_failed": INCIDENT_WHATSAPP_LOOKBACK_DAYS,
            "payment_webhook": INCIDENT_PAYMENT_LOOKBACK_DAYS,
        },
    }


# ─── Dashboard stats ────────────────────────────────────

@router.get("/stats")
async def dashboard_stats(
    request: Request,
    db: AsyncSession = Depends(get_db),
    location_id: Annotated[int | None, Query(ge=1)] = None,
) -> dict:
    """Статистика для дашборда: выручка за сегодня, общие счётчики."""
    org_id = admin_org_from_session(request)
    allowed_location_ids, location_scoped = await _location_scope_for_request(
        request,
        db,
        org_id,
        location_id,
    )
    order_location_scope = orders_location_filter(allowed_location_ids, location_id)
    chat_location_scope = chat_logs_location_filter(allowed_location_ids, location_id)
    now_utc = datetime.now(tz=timezone.utc)
    today_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    ts_lo = _sql_dt_for_filter(today_start)
    ts_hi = _sql_dt_for_filter(now_utc)
    ys_lo = _sql_dt_for_filter(today_start - timedelta(days=1))

    not_cancelled = Order.status != OrderStatus.CANCELLED
    org_orders = _orders_tenant_clause(org_id)

    # Phase 5 OS: event-first metrics — DailyOrgStats (org) или rollup SystemEvent (location)
    if location_scoped:
        _event_summary = await rollup_location_today_summary(
            db,
            org_id,
            location_id=location_id,
            allowed_location_ids=allowed_location_ids,
        )
        _event_rows_2d = await rollup_location_event_stats(
            db,
            org_id,
            days=2,
            location_id=location_id,
            allowed_location_ids=allowed_location_ids,
        )
    else:
        _event_summary = await get_today_event_summary(db, org_id)
        _event_rows_2d = await get_event_stats(db, org_id, days=2)
    _event_data_active = (
        _event_summary.get("orders_confirmed", 0) > 0
        or _event_summary.get("revenue_kzt", 0) > 0
    )
    _yesterday_event = next(
        (r for r in _event_rows_2d if r["date"] == (now_utc.date() - timedelta(days=1)).isoformat()),
        None,
    )

    # Кумулятивные метрики (all-time) — только SQL, не хранится в events
    total_q = await db.execute(
        select(func.count(Order.id), func.coalesce(func.sum(Order.total_price), 0))
        .where(not_cancelled, org_orders, order_location_scope)
    )
    total_row = total_q.one()
    total_orders = total_row[0]
    total_revenue = float(total_row[1])

    # Сегодняшние метрики — event-first, SQL как fallback
    if _event_data_active:
        today_orders = _event_summary["orders_confirmed"]
        today_revenue = _event_summary["revenue_kzt"]
    else:
        today_q = await db.execute(
            select(func.count(Order.id), func.coalesce(func.sum(Order.total_price), 0))
            .where(
                not_cancelled,
                org_orders,
                order_location_scope,
                Order.created_at >= ts_lo,
                Order.created_at <= ts_hi,
            )
        )
        today_row = today_q.one()
        today_orders = today_row[0]
        today_revenue = float(today_row[1])

    # Вчерашние метрики — event-first, SQL как fallback
    if _yesterday_event is not None:
        yesterday_orders = _yesterday_event["orders_confirmed"]
        yesterday_revenue = _yesterday_event["revenue_kzt"]
    else:
        yesterday_q = await db.execute(
            select(func.count(Order.id), func.coalesce(func.sum(Order.total_price), 0))
            .where(
                not_cancelled,
                org_orders,
                order_location_scope,
                Order.created_at >= ys_lo,
                Order.created_at < ts_lo,
            )
        )
        yesterday_row = yesterday_q.one()
        yesterday_orders = yesterday_row[0]
        yesterday_revenue = float(yesterday_row[1])

    def _pct_change(current: float, previous: float) -> float | None:
        if previous <= 0:
            return None if current <= 0 else 100.0
        return round((current - previous) / previous * 100, 1)

    # Phase 5 OS: daily_series — event-first для revenue/orders, SQL только для ai_profit
    valid_keys: list[str] = [
        (today_start - timedelta(days=6 - i)).strftime("%Y-%m-%d") for i in range(7)
    ]
    valid_set = set(valid_keys)

    # Получаем ai_profit из SQL (единственная метрика требующая Order.items_json)
    ai_profit_bucket: dict[str, float] = defaultdict(float)
    week_floor_sql = _sql_dt_for_filter(today_start - timedelta(days=6))
    week_rows = await db.execute(
        select(Order.created_at, Order.items_json)
        .where(
            not_cancelled,
            org_orders,
            order_location_scope,
            Order.created_at.isnot(None),
            Order.created_at >= week_floor_sql,
        )
    )
    for created_at, items_json in week_rows:
        dk = _order_day_key_utc(created_at)
        if dk and dk in valid_set:
            _off, _acc, rev_ai = upsell_stats_from_items_json(
                items_json if isinstance(items_json, dict) else None,
            )
            ai_profit_bucket[dk] += float(rev_ai or 0.0)

    # Event-driven revenue/orders для daily_series
    if location_scoped:
        event_rows_7d = await rollup_location_event_stats(
            db,
            org_id,
            days=7,
            location_id=location_id,
            allowed_location_ids=allowed_location_ids,
        )
    else:
        event_rows_7d = await get_event_stats(db, org_id, days=7)
    event_rev_map = {r["date"]: (r["revenue_kzt"], r["orders_confirmed"]) for r in event_rows_7d}

    # SQL fallback для дней без event-данных
    sql_bucket: dict[str, dict[str, float | int]] = defaultdict(
        lambda: {"revenue": 0.0, "orders": 0},
    )
    if not event_rev_map:
        for created_at, total_price, items_json in (
            await db.execute(
                select(Order.created_at, Order.total_price, Order.items_json)
                .where(
                    not_cancelled,
                    org_orders,
                    order_location_scope,
                    Order.created_at.isnot(None),
                    Order.created_at >= week_floor_sql,
                )
            )
        ):
            dk = _order_day_key_utc(created_at)
            if dk and dk in valid_set:
                sql_bucket[dk]["revenue"] += float(total_price or 0)
                sql_bucket[dk]["orders"] += 1

    daily_series = []
    for k in valid_keys:
        if k in event_rev_map:
            rev, ords = event_rev_map[k]
        elif k in sql_bucket:
            rev, ords = sql_bucket[k]["revenue"], sql_bucket[k]["orders"]
        else:
            rev, ords = 0.0, 0
        daily_series.append({
            "date": k,
            "revenue": float(rev),
            "orders": int(ords),
            "ai_profit": round(float(ai_profit_bucket.get(k, 0.0)), 2),
        })

    bookings_result = await db.execute(
        select(func.count(Booking.id)).where(_bookings_tenant_clause(org_id)),
    )
    bookings_count = bookings_result.scalar() or 0

    menu_result = await db.execute(
        select(func.count(MenuItem.id)).where(MenuItem.organization_id == org_id),
    )
    menu_count = menu_result.scalar() or 0

    failed_open = int(
        await db.scalar(
            select(func.count(FailedTask.id)).where(
                FailedTask.resolved.is_(False),
                _failed_tasks_tenant_clause(org_id),
            ),
        )
        or 0,
    )

    upsell_rows = await db.execute(
        select(Order.items_json)
        .where(
            not_cancelled,
            org_orders,
            order_location_scope,
            Order.created_at >= ts_lo,
            Order.created_at <= ts_hi,
        )
    )
    upsell_offered_today = 0
    upsell_accepted_today = 0
    upsell_revenue_today = 0.0
    for (ij,) in upsell_rows.all():
        o, a, rev = upsell_stats_from_items_json(ij if isinstance(ij, dict) else None)
        upsell_offered_today += o
        upsell_accepted_today += a
        upsell_revenue_today += rev

    upsell_conversion_pct: float | None = None
    if upsell_offered_today > 0:
        upsell_conversion_pct = round(upsell_accepted_today / upsell_offered_today * 100, 1)

    iiko_errors_today = int(
        await db.scalar(
            select(func.count(Order.id)).where(
                org_orders,
                order_location_scope,
                Order.created_at >= ts_lo,
                Order.created_at <= ts_hi,
                Order.iiko_last_error.isnot(None),
                func.coalesce(Order.iiko_last_error, "") != "",
            ),
        )
        or 0,
    )

    ai_checks_accept: list[float] = []
    ai_checks_no_offer: list[float] = []
    ai_val_rows = await db.execute(
        select(Order.total_price, Order.items_json).where(
            not_cancelled,
            org_orders,
            order_location_scope,
            Order.created_at >= ts_lo,
            Order.created_at <= ts_hi,
        ),
    )
    for total_price, ij in ai_val_rows.all():
        off, acc, _rev = upsell_stats_from_items_json(ij if isinstance(ij, dict) else None)
        tp = float(total_price or 0)
        if acc > 0:
            ai_checks_accept.append(tp)
        elif off == 0:
            ai_checks_no_offer.append(tp)
    ai_avg_check_upsell_accepted = (
        round(sum(ai_checks_accept) / len(ai_checks_accept), 2) if ai_checks_accept else None
    )
    ai_avg_check_no_upsell_offer = (
        round(sum(ai_checks_no_offer) / len(ai_checks_no_offer), 2) if ai_checks_no_offer else None
    )

    # AI ROI + "экономия времени" (оценка по количеству сообщений ассистента)
    ai_revenue_today = round(upsell_revenue_today, 2)
    ai_revenue_share_pct: float | None = None
    if today_revenue > 0 and ai_revenue_today > 0:
        ai_revenue_share_pct = round(ai_revenue_today / today_revenue * 100, 1)

    # Phase 5 OS: ai_messages_today — event-first (ai_messages_count), SQL как fallback
    if _event_data_active and _event_summary.get("ai_messages_count", 0) > 0:
        ai_messages_today = _event_summary["ai_messages_count"]
    else:
        ai_messages_today = int(
            await db.scalar(
                select(func.count(ChatLog.id)).where(
                    ChatLog.organization_id == org_id,
                    ChatLog.role == "assistant",
                    ChatLog.created_at >= ts_lo,
                    ChatLog.created_at <= ts_hi,
                    chat_location_scope,
                ),
            )
            or 0,
        )
    # Продуктовая метрика: одно сообщение ассистента экономит ~1.5 минуты ручного набора текста.
    minutes_per_message = 1.5
    ai_time_saved_minutes = round(ai_messages_today * minutes_per_message, 1)
    ai_time_saved_hours = round(ai_time_saved_minutes / 60.0, 2)
    # Простой «ROI-индикатор» для CEO: сколько ₸ допродаж ИИ на каждый «час сэкономленного» времени (без себестоимости LLM).
    ai_profit_per_saved_hour_kzt: float | None = None
    if ai_time_saved_hours and float(ai_time_saved_hours) > 0 and ai_revenue_today > 0:
        ai_profit_per_saved_hour_kzt = round(float(ai_revenue_today) / float(ai_time_saved_hours), 2)

    op_before_order = exists(
        select(ChatLog.id).where(
            ChatLog.user_id == Order.user_id,
            ChatLog.role == "operator",
            ChatLog.created_at <= func.coalesce(Order.updated_at, Order.created_at),
        ),
    )
    bot_orders_today = int(
        (
            await db.execute(
                select(func.count(Order.id)).where(
                    not_cancelled,
                    org_orders,
                    order_location_scope,
                    Order.created_at >= ts_lo,
                    Order.created_at <= ts_hi,
                    ~op_before_order,
                ),
            )
        ).scalar()
        or 0,
    )
    # Phase 5 OS: escalations_today — event-first, SQL как fallback
    if _event_data_active:
        escalations_today = _event_summary["escalations"]
    else:
        escalations_today = int(
            await db.scalar(
                select(func.count(EscalationEvent.id)).where(
                    _escalation_tenant_clause(org_id),
                    EscalationEvent.created_at >= ts_lo,
                    EscalationEvent.created_at <= ts_hi,
                ),
            )
            or 0,
        )
    dialogs_today = int(
        await db.scalar(
            select(func.count(func.distinct(ChatLog.user_id))).where(
                ChatLog.organization_id == org_id,
                ChatLog.role == "user",
                ChatLog.user_id.isnot(None),
                ChatLog.created_at >= ts_lo,
                ChatLog.created_at <= ts_hi,
                    chat_location_scope,
            ),
        )
        or 0,
    )
    escalation_rate_pct: float | None = None
    if dialogs_today > 0:
        escalation_rate_pct = round(100 * escalations_today / dialogs_today, 1)

    bot_handled_pct: float | None = None
    if today_orders > 0:
        bot_handled_pct = round(100 * bot_orders_today / today_orders, 1)

    # Пробуем event-driven источник первым; fallback на SQL только если данных мало
    revenue_history_events = await fetch_daily_revenue_history_from_events(
        db,
        org_id,
        days=28,
        now_utc=now_utc,
        location_id=location_id if location_scoped else None,
        allowed_location_ids=allowed_location_ids if location_scoped else None,
    )
    if event_revenue_history_usable(revenue_history_events):
        revenue_for_forecast = revenue_history_events
        forecast_source = "event_driven"
    else:
        revenue_for_forecast = await fetch_daily_revenue_history(
            db,
            org_id,
            days=28,
            now_utc=now_utc,
            location_id=location_id,
            allowed_location_ids=allowed_location_ids,
        )
        forecast_source = "sql_orders"
    week_forecast = build_week_forecast(
        revenue_for_forecast,
        today=_dt_as_utc(now_utc).date(),
    ) or _linear_week_forecast(daily_series, today=_dt_as_utc(now_utc).date())
    if week_forecast is not None:
        week_forecast = {**week_forecast, "source": forecast_source}

    rec_rows = (
        await db.execute(
            select(BusinessRecommendation)
            .where(
                BusinessRecommendation.organization_id == org_id,
                BusinessRecommendation.status.in_(["new", "viewed"]),
            )
            .order_by(
                BusinessRecommendation.expected_impact_kzt.desc().nulls_last(),
                BusinessRecommendation.created_at.desc(),
            )
            .limit(3),
        )
    ).scalars().all()

    result: dict[str, Any] = {
        "total_orders": total_orders,
        "today_orders": today_orders,
        "today_revenue": today_revenue,
        "total_revenue": total_revenue,
        "yesterday_orders": yesterday_orders,
        "yesterday_revenue": yesterday_revenue,
        "revenue_change_pct": _pct_change(today_revenue, yesterday_revenue),
        "orders_change_pct": _pct_change(float(today_orders), float(yesterday_orders)),
        "daily_series": daily_series,
        "week_forecast": week_forecast,
        "bookings": bookings_count,
        "menu_items": menu_count,
        "failed_tasks_open": failed_open,
        "upsell_offered_today": upsell_offered_today,
        "upsell_accepted_today": upsell_accepted_today,
        "upsell_revenue_today": round(upsell_revenue_today, 2),
        "upsell_conversion_pct": upsell_conversion_pct,
        "ai_revenue_today": ai_revenue_today,
        "ai_revenue_share_pct": ai_revenue_share_pct,
        "ai_messages_today": ai_messages_today,
        "ai_time_saved_minutes": ai_time_saved_minutes,
        "ai_time_saved_hours": ai_time_saved_hours,
        "ai_profit_per_saved_hour_kzt": ai_profit_per_saved_hour_kzt,
        "iiko_errors_today": iiko_errors_today,
        "ai_avg_check_upsell_accepted": ai_avg_check_upsell_accepted,
        "ai_avg_check_no_upsell_offer": ai_avg_check_no_upsell_offer,
        "bot_orders": bot_orders_today,
        "bot_handled_pct": bot_handled_pct,
        "escalations_today": escalations_today,
        "escalation_rate_pct": escalation_rate_pct,
        "dialogs_today": dialogs_today,
        "top_actions": [
            {
                "id": r.id,
                "type": r.recommendation_type,
                "title": r.title,
                "body": r.body,
                "impact_kzt": r.expected_impact_kzt,
                "confidence_pct": r.confidence_pct,
                "cta_label": build_recommendation_target(r.recommendation_type).get("label"),
                "target": build_recommendation_target(r.recommendation_type),
            }
            for r in rec_rows
        ],
    }

    # Phase 5 OS: event_driven_stats уже вычислен выше (_event_summary)
    result["event_driven_stats"] = _event_summary
    result["location_scope"] = {
        "location_id": int(location_id) if location_id is not None else None,
        "source": _location_stats_source(location_scoped, _event_data_active),
    }

    return result


@router.get("/funnel")
async def admin_funnel(
    request: Request,
    db: AsyncSession = Depends(get_db),
    days: Annotated[int, Query(ge=1, le=90)] = 7,
    churn_days: Annotated[int, Query(ge=7, le=180)] = 30,
    location_id: Annotated[int | None, Query(ge=1)] = None,
) -> dict[str, Any]:
    """Воронка потерь и отток клиентов за период."""
    org_id = admin_org_from_session(request)
    if not org_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    allowed_location_ids, location_scoped = await _location_scope_for_request(
        request,
        db,
        org_id,
        location_id,
    )
    order_scope = _orders_tenant_clause(org_id)
    order_location_scope = orders_location_filter(allowed_location_ids, location_id)
    chat_location_scope = chat_logs_location_filter(allowed_location_ids, location_id)

    now_utc = datetime.now(tz=timezone.utc)
    cutoff = now_utc - timedelta(days=days)
    cutoff_sql = _sql_dt_for_filter(cutoff)
    churn_cutoff = now_utc - timedelta(days=churn_days)
    churn_cutoff_sql = _sql_dt_for_filter(churn_cutoff)

    # Phase 5 OS: funnel — event-first для dialogs и completed (orders_confirmed)
    if location_scoped:
        _funnel_event_rows = await rollup_location_event_stats(
            db,
            org_id,
            days=days,
            location_id=location_id,
            allowed_location_ids=allowed_location_ids,
        )
    else:
        _funnel_event_rows = await get_event_stats_for_range(
            db,
            org_id,
            start_date=(now_utc.date() - timedelta(days=days - 1)),
            end_date=now_utc.date(),
        )
    _funnel_dialogs_event = sum(r["dialogs_count"] for r in _funnel_event_rows)
    _funnel_completed_event = sum(r["orders_confirmed"] for r in _funnel_event_rows)
    _funnel_event_active = _funnel_completed_event > 0 or _funnel_dialogs_event > 0

    if _funnel_event_active and _funnel_dialogs_event > 0:
        dialogs_count = _funnel_dialogs_event
    else:
        dialogs_count = int(
            await db.scalar(
                select(func.count(func.distinct(ChatLog.user_id))).where(
                    ChatLog.organization_id == org_id,
                    ChatLog.created_at >= cutoff_sql,
                    ChatLog.role == "user",
                    ChatLog.user_id.isnot(None),
                    chat_location_scope,
                ),
            )
            or 0,
        )

    drafts_count = int(
        await db.scalar(
            select(func.count(func.distinct(Order.user_id))).where(
                order_scope,
                order_location_scope,
                Order.created_at >= cutoff_sql,
                Order.user_id.isnot(None),
            ),
        )
        or 0,
    )

    if _funnel_event_active and _funnel_completed_event > 0:
        completed_count = _funnel_completed_event
    else:
        completed_count = int(
            await db.scalar(
                select(func.count(func.distinct(Order.user_id))).where(
                    order_scope,
                    order_location_scope,
                    Order.created_at >= cutoff_sql,
                    Order.user_id.isnot(None),
                    Order.status.in_(_COMPLETED_ORDER_STATUSES),
                ),
            )
            or 0,
        )

    recent_buyers = (
        select(func.distinct(Order.user_id))
        .where(
            order_scope,
            order_location_scope,
            Order.user_id.isnot(None),
            Order.status.in_(_COMPLETED_ORDER_STATUSES),
            Order.created_at >= churn_cutoff_sql,
        )
    )
    churned_count = int(
        await db.scalar(
            select(func.count(func.distinct(Order.user_id))).where(
                order_scope,
                order_location_scope,
                Order.user_id.isnot(None),
                Order.status.in_(_COMPLETED_ORDER_STATUSES),
                Order.user_id.not_in(recent_buyers),
            ),
        )
        or 0,
    )

    ordered_users_period = (
        select(func.distinct(Order.user_id))
        .where(
            order_scope,
            order_location_scope,
            Order.created_at >= cutoff_sql,
            Order.user_id.isnot(None),
        )
    )
    dialog_no_order = int(
        await db.scalar(
            select(func.count(func.distinct(ChatLog.user_id))).where(
                ChatLog.organization_id == org_id,
                ChatLog.created_at >= cutoff_sql,
                ChatLog.role == "user",
                ChatLog.user_id.isnot(None),
                ChatLog.user_id.not_in(ordered_users_period),
                chat_location_scope,
            ),
        )
        or 0,
    )

    feedback_neg = int(
        await db.scalar(
            select(func.count(CustomerFeedback.id)).where(
                CustomerFeedback.organization_id == org_id,
                CustomerFeedback.created_at >= cutoff_sql,
                CustomerFeedback.rating == "negative",
            ),
        )
        or 0,
    )
    feedback_pos = int(
        await db.scalar(
            select(func.count(CustomerFeedback.id)).where(
                CustomerFeedback.organization_id == org_id,
                CustomerFeedback.created_at >= cutoff_sql,
                CustomerFeedback.rating == "positive",
            ),
        )
        or 0,
    )
    feedback_total = feedback_neg + feedback_pos
    feedback_negative_pct: float | None = None
    if feedback_total > 0:
        feedback_negative_pct = round(100 * feedback_neg / feedback_total, 1)

    cancellations = int(
        await db.scalar(
            select(func.count(Order.id)).where(
                order_scope,
                order_location_scope,
                Order.created_at >= cutoff_sql,
                Order.status == OrderStatus.CANCELLED.value,
            ),
        )
        or 0,
    )

    dialog_to_draft = round(100 * drafts_count / dialogs_count, 1) if dialogs_count else None
    draft_to_order = round(100 * completed_count / drafts_count, 1) if drafts_count else None
    dialog_to_order = round(100 * completed_count / dialogs_count, 1) if dialogs_count else None

    return {
        "ok": True,
        "period_days": days,
        "location_scope": {
            "location_id": int(location_id) if location_id is not None else None,
            "source": _location_stats_source(location_scoped, _funnel_event_active),
        },
        "funnel": {
            "dialogs": dialogs_count,
            "drafts": drafts_count,
            "completed": completed_count,
            "dialog_no_order": dialog_no_order,
            "dialog_to_draft_pct": dialog_to_draft,
            "draft_to_order_pct": draft_to_order,
            "dialog_to_order_pct": dialog_to_order,
        },
        "churn": {
            "churned_count": churned_count,
            "churn_threshold_days": churn_days,
            "label": f"Не заказывали {churn_days}+ дней",
        },
        "losses": {
            "cancellations": cancellations,
            "feedback_negative": feedback_neg,
            "feedback_positive": feedback_pos,
            "feedback_negative_pct": feedback_negative_pct,
        },
    }


def _ai_value_window_bounds(
    period: str,
    date_from: str | None,
    date_to: str | None,
    *,
    now_utc: datetime,
) -> tuple[datetime, datetime, str]:
    """Границы окна для GET /ai-value (UTC, как /analytics)."""
    today_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    p = (period or "30d").strip().lower()
    if p == "custom" and (not date_from or not date_to):
        p = "30d"
    if p == "custom" and date_from and date_to:
        df = date.fromisoformat(date_from)
        dt_to = date.fromisoformat(date_to)
        if df > dt_to:
            df, dt_to = dt_to, df
        start = datetime(df.year, df.month, df.day, 0, 0, 0, tzinfo=timezone.utc)
        end = datetime(
            dt_to.year, dt_to.month, dt_to.day, 23, 59, 59, 999999, tzinfo=timezone.utc,
        )
        return start, end, "custom"
    if p == "7d":
        return today_start - timedelta(days=6), now_utc, "7d"
    if p == "90d":
        return today_start - timedelta(days=89), now_utc, "90d"
    if p not in ("30d", "7d", "90d", "custom"):
        p = "30d"
    return today_start - timedelta(days=29), now_utc, p


@router.get("/ai-value")
async def admin_ai_value(
    request: Request,
    response: Response,
    period: str = Query(
        "30d",
        description="Окно: 7d | 30d | 90d | custom (с date_from/date_to)",
    ),
    date_from: str | None = Query(
        None,
        description="Начало произвольного периода YYYY-MM-DD (вместе с date_to, period=custom)",
    ),
    date_to: str | None = Query(
        None,
        description="Конец произвольного периода YYYY-MM-DD",
    ),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Метрики «Вклад ИИ» за период: допродажи, сообщения ассистента, automation, эскалации.
    Формат совместим с нормализацией фронта (`metrics` + `daily_series`).
    """
    response.headers["Cache-Control"] = "no-store"
    org_id = admin_org_from_session(request)
    now_utc = datetime.now(tz=timezone.utc)
    start, end, period_tag = _ai_value_window_bounds(period, date_from, date_to, now_utc=now_utc)
    start_sql = _sql_dt_for_filter(start)
    end_sql = _sql_dt_for_filter(end)

    not_cancelled = Order.status != OrderStatus.CANCELLED
    org_orders = _orders_tenant_clause(org_id)

    op_before_order = exists(
        select(ChatLog.id).where(
            ChatLog.user_id == Order.user_id,
            ChatLog.role == "operator",
            ChatLog.created_at <= func.coalesce(Order.updated_at, Order.created_at),
        ),
    )
    esc_scope = _escalation_tenant_clause(org_id)
    esc_count = int(
        await db.scalar(
            select(func.count(EscalationEvent.id)).where(
                esc_scope,
                EscalationEvent.created_at >= start_sql,
                EscalationEvent.created_at <= end_sql,
            ),
        )
        or 0,
    )

    total_row = (
        await db.execute(
            select(func.count(Order.id), func.coalesce(func.sum(Order.total_price), 0)).where(
                not_cancelled,
                org_orders,
                Order.created_at >= start_sql,
                Order.created_at <= end_sql,
            ),
        )
    ).one()
    orders_n = int(total_row[0])
    revenue_kzt = float(total_row[1])

    bot_row = (
        await db.execute(
            select(func.count(Order.id), func.coalesce(func.sum(Order.total_price), 0)).where(
                not_cancelled,
                org_orders,
                Order.created_at >= start_sql,
                Order.created_at <= end_sql,
                ~op_before_order,
            ),
        )
    ).one()
    bot_orders = int(bot_row[0])
    bot_revenue_kzt = float(bot_row[1])

    takeover_row = (
        await db.execute(
            select(func.count(Order.id)).where(
                not_cancelled,
                org_orders,
                Order.created_at >= start_sql,
                Order.created_at <= end_sql,
                op_before_order,
            ),
        )
    ).one()
    takeover_orders = int(takeover_row[0])

    cur_stmt = select(Order).where(
        not_cancelled,
        org_orders,
        Order.created_at >= start_sql,
        Order.created_at <= end_sql,
    )
    cur_orders_result = await db.execute(cur_stmt)

    daily: dict[str, dict[str, float | int]] = defaultdict(
        lambda: {"revenue": 0.0, "orders": 0},
    )
    daily_ai_profit: dict[str, float] = defaultdict(float)
    upsell_offered_p = 0
    upsell_accepted_p = 0
    upsell_revenue_p = 0.0
    ai_checks_accept: list[float] = []
    ai_checks_no_offer: list[float] = []
    current_orders: list[Order] = []

    for o in cur_orders_result.scalars():
        current_orders.append(o)
        dk = _order_day_key_utc(o.created_at)
        if dk:
            daily[dk]["revenue"] += float(o.total_price or 0)
            daily[dk]["orders"] += 1
        ij = o.items_json if isinstance(o.items_json, dict) else None
        off, acc, rev = upsell_stats_from_items_json(ij)
        upsell_offered_p += int(off)
        upsell_accepted_p += int(acc)
        upsell_revenue_p += float(rev)
        if dk:
            daily_ai_profit[dk] += float(rev)
        tp = float(o.total_price or 0)
        if acc > 0:
            ai_checks_accept.append(tp)
        elif off == 0:
            ai_checks_no_offer.append(tp)

    start_d = _dt_as_utc(start).date()
    end_d = _dt_as_utc(end).date()
    daily_series: list[dict[str, Any]] = []
    walk = start_d
    while walk <= end_d:
        key = walk.isoformat()
        entry = daily.get(key, {"revenue": 0.0, "orders": 0})
        daily_series.append(
            {
                "date": key,
                "revenue": float(entry["revenue"]),
                "orders": int(entry["orders"]),
                "ai_profit": round(float(daily_ai_profit.get(key, 0.0)), 2),
            },
        )
        walk += timedelta(days=1)

    upsell_conversion_pct: float | None = None
    if upsell_offered_p > 0:
        upsell_conversion_pct = round(upsell_accepted_p / upsell_offered_p * 100, 1)

    ai_revenue = round(upsell_revenue_p, 2)
    ai_revenue_share_pct: float | None = None
    if revenue_kzt > 0 and ai_revenue > 0:
        ai_revenue_share_pct = round(ai_revenue / revenue_kzt * 100, 1)

    ai_messages = int(
        await db.scalar(
            select(func.count(ChatLog.id)).where(
                ChatLog.organization_id == org_id,
                ChatLog.role == "assistant",
                ChatLog.created_at >= start_sql,
                ChatLog.created_at <= end_sql,
            ),
        )
        or 0,
    )
    minutes_per_message = 1.5
    ai_time_saved_minutes = round(ai_messages * minutes_per_message, 1)
    ai_time_saved_hours = round(ai_time_saved_minutes / 60.0, 2)
    ai_profit_per_saved_hour_kzt: float | None = None
    if ai_time_saved_hours and float(ai_time_saved_hours) > 0 and ai_revenue > 0:
        ai_profit_per_saved_hour_kzt = round(float(ai_revenue) / float(ai_time_saved_hours), 2)

    ai_avg_check_upsell_accepted = (
        round(sum(ai_checks_accept) / len(ai_checks_accept), 2) if ai_checks_accept else None
    )
    ai_avg_check_no_upsell_offer = (
        round(sum(ai_checks_no_offer) / len(ai_checks_no_offer), 2) if ai_checks_no_offer else None
    )

    eng_rows = menu_engineering_rows(current_orders)
    top_upsell_items: list[dict[str, Any]] = []
    for r in eng_rows[:5]:
        key = str(r.get("key") or "")
        iiko_part = key.split(":", 1)[1] if key.startswith("iiko:") else ""
        top_upsell_items.append(
            {
                "iiko_id": iiko_part or None,
                "name": str(r.get("label") or ""),
                "accepted": int(r.get("accepts") or 0),
                "revenue_kzt": round(float(r.get("revenue") or 0), 2),
            },
        )

    days_n = (_dt_as_utc(end).date() - _dt_as_utc(start).date()).days + 1

    return {
        "period": period_tag,
        "from": start.isoformat(),
        "to": end.isoformat(),
        "days": days_n,
        "metrics": {
            "ai_revenue": ai_revenue,
            "ai_revenue_share_pct": ai_revenue_share_pct,
            "revenue_share_pct": ai_revenue_share_pct,
            "upsell_offered": upsell_offered_p,
            "upsell_accepted": upsell_accepted_p,
            "upsell_conversion_pct": upsell_conversion_pct,
            "ai_messages": ai_messages,
            "ai_time_saved_hours": ai_time_saved_hours,
            "ai_time_saved_minutes": ai_time_saved_minutes,
            "ai_profit_per_saved_hour_kzt": ai_profit_per_saved_hour_kzt,
            "ai_avg_check_upsell_accepted": ai_avg_check_upsell_accepted,
            "ai_avg_check_no_upsell_offer": ai_avg_check_no_upsell_offer,
        },
        "daily_series": daily_series,
        "totals": {
            "orders": orders_n,
            "revenue_kzt": round(revenue_kzt, 2),
            "bot_orders": bot_orders,
            "bot_revenue_kzt": round(bot_revenue_kzt, 2),
            "takeover_orders": takeover_orders,
        },
        "upsell": {
            "offered": upsell_offered_p,
            "accepted": upsell_accepted_p,
            "revenue_kzt": ai_revenue,
            "conversion_pct": upsell_conversion_pct,
        },
        "escalations": {
            "count": esc_count,
            "first_response_avg_sec": None,
        },
        "top_upsell_items": top_upsell_items,
    }


@router.get("/roi/today")
async def roi_today_summary(
    request: Request,
    db: AsyncSession = Depends(get_db),
    location_id: Annotated[int | None, Query(ge=1)] = None,
) -> dict[str, object]:
    """
    ROI-нарратив за сегодня (UTC, как /stats) + «достижения» за последние 7 дней в TZ организации.
    """
    org_id = admin_org_from_session(request)
    allowed_location_ids, location_scoped = await _location_scope_for_request(
        request,
        db,
        org_id,
        location_id,
    )
    org = await db.get(Organization, org_id)
    now_utc = datetime.now(tz=timezone.utc)
    today_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    cur = (getattr(org, "currency", None) or "KZT") if org is not None else "KZT"
    tz = (getattr(org, "timezone", None) or "UTC") if org is not None else "UTC"
    try:
        metrics = await aggregate_org_window(
            db,
            org_id,
            today_start,
            now_utc,
            location_id=location_id,
            allowed_location_ids=allowed_location_ids,
        )
    except Exception:
        logger.exception("Failed to build ROI metrics org=%s", org_id)
        metrics = {
            "orders_count": 0,
            "revenue": 0,
            "upsell_offers": 0,
            "upsell_accepts": 0,
            "upsell_revenue": 0,
            "active_guests": 0,
            "ai_messages": 0,
            "ai_time_saved_minutes": 0,
            "help_events": 0,
        }
    narrative = build_today_narrative_ru(metrics, str(cur))
    try:
        achievements = await build_achievements_week(db, org_id, str(tz))
    except Exception:
        logger.exception("Failed to build ROI achievements org=%s", org_id)
        achievements = []
    return {
        "narrative": narrative,
        "metrics": metrics,
        "achievements": achievements,
        "location_scope": {
            "location_id": int(location_id) if location_id is not None else None,
            "source": "sql_location" if location_scoped else "org",
        },
    }


@router.get("/activity")
async def dashboard_activity(
    request: Request,
    limit: int = Query(25, ge=5, le=100),
    location_id: Annotated[int | None, Query(ge=1)] = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Activity feed для CEO/Staff: последние события, читаемые "за 1 секунду".
    Phase 5 OS: event-first через SystemEvent (один запрос), SQL для delivery_failed.
    """
    from app.db.models import SystemEvent

    org_id = admin_org_from_session(request)
    allowed_location_ids, location_scoped = await _location_scope_for_request(
        request,
        db,
        org_id,
        location_id,
    )
    chat_location_scope = chat_logs_location_filter(allowed_location_ids, location_id)
    now_utc = datetime.now(tz=timezone.utc)
    since = _sql_dt_for_filter(now_utc - timedelta(days=7))

    items: list[dict[str, Any]] = []

    # Phase 5 OS: читаем из SystemEvent — один запрос вместо 4
    _ACTIVITY_EVENT_TYPES = (
        "order.created", "order.confirmed", "order.cancelled",
        "booking.created", "booking.confirmed",
        "ai.escalated", "operator.took_over",
        "payment.completed", "payment.failed",
        "system.sla_violated",
    )
    ev_rows = (await db.execute(
        select(SystemEvent.event_type, SystemEvent.entity_id, SystemEvent.payload_json, SystemEvent.created_at)
        .where(
            SystemEvent.organization_id == org_id,
            SystemEvent.event_type.in_(_ACTIVITY_EVENT_TYPES),
            SystemEvent.created_at.isnot(None),
            SystemEvent.created_at >= since,
        )
        .order_by(SystemEvent.created_at.desc())
        .limit(limit * (4 if location_scoped else 2))  # берём с запасом для дедупликации/фильтра по точке
    )).all()

    for ev_type, entity_id, payload, ts in ev_rows:
        if ts is None:
            continue
        p = payload or {}
        raw_loc = p.get("_location_id") or p.get("location_id")
        try:
            event_location_id = int(raw_loc) if raw_loc is not None else None
        except (TypeError, ValueError):
            event_location_id = None
        if location_id is not None and event_location_id != int(location_id):
            continue
        if location_id is None and allowed_location_ids is not None and event_location_id not in allowed_location_ids:
            continue
        ts_iso = _dt_as_utc(ts).isoformat()
        if ev_type in ("order.created", "order.confirmed"):
            oid = entity_id or p.get("order_id") or "?"
            total = p.get("total_price") or p.get("amount") or 0
            items.append({
                "ts": ts_iso, "kind": "order",
                "title": f"Заказ #{oid}",
                "subtitle": f"{ev_type.split('.')[1]} · {float(total or 0):.0f} ₸",
                "ref": {"tab": "orders", "order_id": int(oid) if str(oid).isdigit() else None},
                "source": "event_driven",
            })
        elif ev_type == "order.cancelled":
            oid = entity_id or p.get("order_id") or "?"
            items.append({
                "ts": ts_iso, "kind": "order_cancelled",
                "title": f"Заказ #{oid} отменён",
                "subtitle": str(p.get("reason") or "")[:80],
                "ref": {"tab": "orders"},
                "source": "event_driven",
            })
        elif ev_type in ("booking.created", "booking.confirmed"):
            bid = entity_id or p.get("booking_id") or "?"
            items.append({
                "ts": ts_iso, "kind": "booking",
                "title": f"Бронирование #{bid}",
                "subtitle": ev_type.split(".")[1],
                "ref": {"tab": "bookings"},
                "source": "event_driven",
            })
        elif ev_type == "ai.escalated":
            phone = p.get("phone") or str(entity_id or "")
            reason = str(p.get("reason") or "")[:80]
            items.append({
                "ts": ts_iso, "kind": "help",
                "title": "Нужна помощь клиенту",
                "subtitle": f"{phone.strip()} · {reason}".strip(" ·"),
                "ref": {"tab": "operator_queue", "phone": phone.strip()},
                "source": "event_driven",
            })
        elif ev_type == "operator.took_over":
            phone = p.get("phone") or str(entity_id or "")
            items.append({
                "ts": ts_iso, "kind": "operator",
                "title": "Оператор подключился",
                "subtitle": phone.strip(),
                "ref": {"tab": "chats", "phone": phone.strip()},
                "source": "event_driven",
            })
        elif ev_type == "payment.completed":
            amount = p.get("amount") or 0
            items.append({
                "ts": ts_iso, "kind": "payment",
                "title": "Оплата получена",
                "subtitle": f"{float(amount or 0):.0f} ₸",
                "ref": {"tab": "orders"},
                "source": "event_driven",
            })
        elif ev_type == "payment.failed":
            items.append({
                "ts": ts_iso, "kind": "payment_failed",
                "title": "Ошибка оплаты",
                "subtitle": str(p.get("error") or "")[:80],
                "ref": {"tab": "orders"},
                "source": "event_driven",
            })
        elif ev_type == "system.sla_violated":
            stage = p.get("stage") or str(entity_id or "")
            p95 = p.get("p95_ms") or 0
            items.append({
                "ts": ts_iso, "kind": "sla_violation",
                "title": f"SLA нарушен: {stage}",
                "subtitle": f"p95={p95}ms",
                "ref": {"tab": "ai_center", "aiCenterTab": "load"},
                "source": "event_driven",
            })

    # Delivery failed — только из ChatLog (нет в event bus)
    f_rows = await db.execute(
        select(ChatLog.id, ChatLog.created_at).where(
            ChatLog.organization_id == org_id,
            ChatLog.delivery_status == "failed",
            ChatLog.created_at.isnot(None),
            ChatLog.created_at >= since,
            chat_location_scope,
        )
        .order_by(ChatLog.created_at.desc())
        .limit(10),
    )
    for cid, ts in f_rows.all():
        if ts is None:
            continue
        items.append({
            "ts": _dt_as_utc(ts).isoformat(),
            "kind": "delivery_failed",
            "title": "Сообщение не доставлено",
            "subtitle": f"ID #{int(cid)}",
            "ref": {"tab": "chats"},
            "source": "sql",
        })

    items.sort(key=lambda x: x.get("ts") or "", reverse=True)
    items = items[: int(limit)]
    return {
        "items": items,
        "source": "event_driven" if not location_scoped else "event_driven_location_filtered",
        "location_scope": {
            "location_id": int(location_id) if location_id is not None else None,
            "source": "event_payload_and_sql" if location_scoped else "org",
        },
    }


# ─── Аналитика ──────────────────────────────────────────


@router.get("/analytics")
async def analytics(
    request: Request,
    response: Response,
    period: str = Query("week", description="day, week, month, year, custom"),
    date_from: str | None = Query(None, description="Начало периода (YYYY-MM-DD)"),
    date_to: str | None = Query(None, description="Конец периода (YYYY-MM-DD)"),
    location_id: Annotated[int | None, Query(ge=1)] = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Аналитика: выручка, количество заказов, средний чек по дням.
    Поддерживает период: day, week, month, custom (с date_from/date_to).

    Границы и дневные корзины — **UTC** (как в GET /stats), ключ дня — календарная дата в UTC.
    """
    from app.services.intelligence_analytics import delivery_geo_rows

    response.headers["Cache-Control"] = "no-store"

    org_id = admin_org_from_session(request)
    allowed_location_ids, location_scoped = await _location_scope_for_request(
        request,
        db,
        org_id,
        location_id,
    )
    org_orders = _orders_tenant_clause(org_id)
    order_location_scope = orders_location_filter(allowed_location_ids, location_id)
    chat_location_scope = chat_logs_location_filter(allowed_location_ids, location_id)

    now = datetime.now(tz=timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    period = (period or "week").strip().lower()
    if period not in ("day", "week", "month", "year", "custom"):
        period = "week"

    if period == "custom" and date_from and date_to:
        df = date.fromisoformat(date_from)
        dt_to = date.fromisoformat(date_to)
        if df > dt_to:
            df, dt_to = dt_to, df
        start = datetime(df.year, df.month, df.day, 0, 0, 0, tzinfo=timezone.utc)
        end = datetime(
            dt_to.year, dt_to.month, dt_to.day, 23, 59, 59, 999999, tzinfo=timezone.utc,
        )
    elif period == "day":
        start = today_start
        end = now
    elif period == "month":
        start = today_start - timedelta(days=30)
        end = now
    elif period == "year":
        start = today_start - timedelta(days=365)
        end = now
    else:
        start = today_start - timedelta(days=7)
        end = now

    prev_duration = end - start
    prev_start = start - prev_duration
    prev_end = start

    start_sql = _sql_dt_for_filter(start)
    end_sql = _sql_dt_for_filter(end)
    prev_start_sql = _sql_dt_for_filter(prev_start)
    prev_end_sql = _sql_dt_for_filter(prev_end)

    esc_scope = _escalation_tenant_clause(org_id)
    esc_count = int(
        await db.scalar(
            select(func.count(EscalationEvent.id)).where(
                esc_scope,
                EscalationEvent.created_at >= start_sql,
                EscalationEvent.created_at <= end_sql,
            ),
        )
        or 0,
    )
    prev_esc = int(
        await db.scalar(
            select(func.count(EscalationEvent.id)).where(
                esc_scope,
                EscalationEvent.created_at >= prev_start_sql,
                EscalationEvent.created_at < prev_end_sql,
            ),
        )
        or 0,
    )

    not_cancelled = Order.status != OrderStatus.CANCELLED

    # Phase 5 OS: event-first для revenue/orders по периоду
    _period_start = _dt_as_utc(start).date()
    _period_end = _dt_as_utc(end).date()
    if location_scoped:
        _span_days = max((_period_end - _period_start).days + 1, 1)
        _rollup_rows = await rollup_location_event_stats(
            db,
            org_id,
            days=_span_days,
            location_id=location_id,
            allowed_location_ids=allowed_location_ids,
        )
        _analytics_event_rows = [
            r for r in _rollup_rows
            if _period_start.isoformat() <= str(r["date"]) <= _period_end.isoformat()
        ]
    else:
        _analytics_event_rows = await get_event_stats_for_range(
            db,
            org_id,
            start_date=_period_start,
            end_date=_period_end,
        )
    _analytics_event_map: dict[str, dict] = {r["date"]: r for r in _analytics_event_rows}
    _analytics_event_orders = sum(r["orders_confirmed"] for r in _analytics_event_rows)
    _analytics_event_revenue = sum(r["revenue_kzt"] for r in _analytics_event_rows)
    _analytics_event_active = _analytics_event_orders > 0 or _analytics_event_revenue > 0

    if _analytics_event_active:
        current_count = _analytics_event_orders
        current_revenue = _analytics_event_revenue
    else:
        # SQL fallback если event-данных нет (новые org без backfill)
        cur_q = await db.execute(
            select(
                func.count(Order.id),
                func.coalesce(func.sum(Order.total_price), 0),
            )
            .where(
                not_cancelled,
                org_orders,
                order_location_scope,
                Order.created_at >= start_sql,
                Order.created_at <= end_sql,
            )
        )
        cur_row = cur_q.one()
        current_count = cur_row[0]
        current_revenue = float(cur_row[1])

    # Агрегаты предыдущего периода (SQL)
    prev_q = await db.execute(
        select(
            func.count(Order.id),
            func.coalesce(func.sum(Order.total_price), 0),
        )
        .where(
            not_cancelled,
            org_orders,
            order_location_scope,
            Order.created_at >= prev_start_sql,
            Order.created_at < prev_end_sql,
        )
    )
    prev_row = prev_q.one()
    prev_count = prev_row[0]
    prev_revenue = float(prev_row[1])

    avg_check = current_revenue / current_count if current_count else 0
    prev_avg = prev_revenue / prev_count if prev_count else 0

    # Заказы текущего периода (daily + top_items) — потоковая выборка без .all()
    cur_stmt = (
        select(Order)
        .where(
            not_cancelled,
            org_orders,
            order_location_scope,
            Order.created_at >= start_sql,
            Order.created_at <= end_sql,
        )
    )
    cur_orders_result = await db.execute(cur_stmt)

    daily: dict[str, dict] = defaultdict(
        lambda: {"revenue": 0.0, "orders": 0}
    )
    daily_ai_profit: dict[str, float] = defaultdict(float)
    ai_profit_total = 0.0
    item_stats: dict[str, dict] = defaultdict(
        lambda: {"quantity": 0, "revenue": 0.0, "ai_profit": 0.0}
    )
    current_orders: list[Order] = []
    for o in cur_orders_result.scalars():
        current_orders.append(o)
        dk = _order_day_key_utc(o.created_at)
        if dk:
            daily[dk]["revenue"] += float(o.total_price or 0)
            daily[dk]["orders"] += 1
        ij = o.items_json if isinstance(o.items_json, dict) else None
        _off, _acc, rev = upsell_stats_from_items_json(ij)
        if rev:
            ai_profit_total += float(rev)
            if dk:
                daily_ai_profit[dk] += float(rev)

        items_data = o.items_json or {}
        for item in items_data.get("items", []):
            name = item.get("name", "?")
            qty = item.get("quantity", 0)
            total = item.get("item_total", 0)
            item_stats[name]["quantity"] += qty
            item_stats[name]["revenue"] += float(total)

        ij2 = items_data if isinstance(items_data, dict) else None
        if not isinstance(ij2, dict):
            continue
        meta = order_meta_from_items_json(ij2)
        trace = meta.get("recommendation_trace")
        if not isinstance(trace, list) or not trace:
            continue
        items_list = ij2.get("items")
        if not isinstance(items_list, list) or not items_list:
            continue
        id_to_name: dict[str, str] = {}
        for it in items_list:
            if not isinstance(it, dict):
                continue
            iid = str(it.get("iiko_item_id") or "").strip()
            nm = str(it.get("name") or "?")
            if iid and nm:
                id_to_name[iid] = nm
        for ev in trace:
            if not isinstance(ev, dict):
                continue
            if ev.get("accepted") is not True:
                continue
            rev_tr = float(ev.get("accepted_revenue_kzt") or 0)
            if rev_tr <= 0:
                continue
            iid = str(ev.get("offered_iiko_id") or ev.get("accepted_iiko_id") or "").strip()
            if not iid:
                continue
            nm = id_to_name.get(iid)
            if not nm:
                continue
            item_stats[nm]["ai_profit"] += float(rev_tr)

    start_d = _dt_as_utc(start).date()
    end_d = _dt_as_utc(end).date()
    daily_data: list[dict] = []
    walk = start_d
    while walk <= end_d:
        key = walk.isoformat()
        # Phase 5 OS: event-first для revenue/orders; SQL как fallback
        if key in _analytics_event_map:
            ev = _analytics_event_map[key]
            rev = ev["revenue_kzt"]
            ords = ev["orders_confirmed"]
        else:
            sql_entry = daily.get(key, {"revenue": 0.0, "orders": 0})
            rev = sql_entry["revenue"]
            ords = sql_entry["orders"]
        daily_data.append(
            {
                "date": key,
                "revenue": float(rev),
                "orders": int(ords),
                "ai_profit": round(float(daily_ai_profit.get(key, 0.0)), 2),
            },
        )
        walk += timedelta(days=1)

    top_items = sorted(
        [{"name": k, **v} for k, v in item_stats.items()],
        key=lambda x: x["revenue"],
        reverse=True,
    )[:10]

    # Воронка: активность в чатах → черновики → «закрытые» в iiko/завершённые
    chat_users_sq = (
        select(ChatLog.user_id)
        .join(User, User.id == ChatLog.user_id)
        .where(
            User.organization_id == org_id,
            ChatLog.created_at >= start_sql,
            ChatLog.created_at <= end_sql,
            chat_location_scope,
        )
        .distinct()
        .subquery()
    )
    funnel_dialogs = int(
        await db.scalar(select(func.count()).select_from(chat_users_sq)) or 0,
    )
    funnel_drafts = int(
        await db.scalar(
            select(func.count(Order.id)).where(
                Order.status == OrderStatus.DRAFT.value,
                not_cancelled,
                org_orders,
                order_location_scope,
                Order.created_at >= start_sql,
                Order.created_at <= end_sql,
            ),
        )
        or 0,
    )
    funnel_finished = int(
        await db.scalar(
            select(func.count(Order.id)).where(
                Order.status.in_(
                    [
                        OrderStatus.SENT_TO_IIKO.value,
                        OrderStatus.IN_TRANSIT.value,
                        OrderStatus.WAITING_PICKUP.value,
                        OrderStatus.COMPLETED.value,
                    ],
                ),
                not_cancelled,
                org_orders,
                order_location_scope,
                Order.created_at >= start_sql,
                Order.created_at <= end_sql,
            ),
        )
        or 0,
    )

    op_exists = exists(
        select(ChatLog.id).where(
            ChatLog.user_id == Order.user_id,
            ChatLog.role == "operator",
            ChatLog.created_at <= func.coalesce(Order.updated_at, Order.created_at),
        ),
    )
    auto_cnt = int(
        await db.scalar(
            select(func.count(Order.id)).where(
                Order.status.in_(
                    [
                        OrderStatus.SENT_TO_IIKO.value,
                        OrderStatus.IN_TRANSIT.value,
                        OrderStatus.WAITING_PICKUP.value,
                        OrderStatus.COMPLETED.value,
                    ],
                ),
                not_cancelled,
                org_orders,
                order_location_scope,
                Order.created_at >= start_sql,
                Order.created_at <= end_sql,
                ~op_exists,
            ),
        )
        or 0,
    )
    automation_rate = (
        round(100.0 * auto_cnt / funnel_finished, 1) if funnel_finished else None
    )

    broad_total_cnt = int(
        await db.scalar(
            select(func.count(Order.id)).where(
                not_cancelled,
                org_orders,
                Order.created_at >= start_sql,
                Order.created_at <= end_sql,
            ),
        )
        or 0,
    )
    broad_auto_cnt = int(
        await db.scalar(
            select(func.count(Order.id)).where(
                not_cancelled,
                org_orders,
                Order.created_at >= start_sql,
                Order.created_at <= end_sql,
                ~op_exists,
            ),
        )
        or 0,
    )
    automation_broad_pct = (
        round(100.0 * broad_auto_cnt / broad_total_cnt, 1) if broad_total_cnt else None
    )

    menu_engineering = menu_engineering_rows(current_orders)
    delivery_geo = delivery_geo_rows(current_orders)

    hour_buckets: list[dict[str, float | int]] = [
        {"hour": h, "orders": 0, "revenue": 0.0} for h in range(24)
    ]
    for o in current_orders:
        dt_h = o.created_at
        if dt_h is None:
            continue
        if dt_h.tzinfo:
            dt_u = dt_h.astimezone(timezone.utc)
        else:
            dt_u = dt_h.replace(tzinfo=timezone.utc)
        hh = int(dt_u.hour)
        hour_buckets[hh]["orders"] = int(hour_buckets[hh]["orders"]) + 1
        hour_buckets[hh]["revenue"] = float(hour_buckets[hh]["revenue"]) + float(o.total_price or 0)

    heatmap_matrix = [[0 for _ in range(24)] for _ in range(7)]
    for o in current_orders:
        dt = o.created_at
        if dt is None:
            continue
        if dt.tzinfo:
            dt_utc = dt.astimezone(timezone.utc)
        else:
            dt_utc = dt.replace(tzinfo=timezone.utc)
        w = int(dt_utc.weekday())
        h = int(dt_utc.hour)
        heatmap_matrix[w][h] += 1

    def pct_change(curr: float, prev: float) -> float | None:
        if prev == 0:
            return None
        return round((curr - prev) / prev * 100, 1)

    # AI Profit (upsell revenue) — previous period only needs totals.
    prev_ai_profit_total = 0.0
    prev_orders_result = await db.execute(
        select(Order.items_json)
        .where(
            not_cancelled,
            org_orders,
            order_location_scope,
            Order.created_at >= prev_start_sql,
            Order.created_at < prev_end_sql,
        )
    )
    for (ij,) in prev_orders_result.all():
        items_json = ij if isinstance(ij, dict) else None
        _o, _a, rev = upsell_stats_from_items_json(items_json)
        if rev:
            prev_ai_profit_total += float(rev)

    return {
        "period": period,
        "location_scope": {
            "location_id": int(location_id) if location_id is not None else None,
            "source": _location_stats_source(location_scoped, _analytics_event_active),
        },
        "date_from": start.strftime("%Y-%m-%d"),
        "date_to": end.strftime("%Y-%m-%d"),
        "current": {
            "revenue": current_revenue,
            "orders": current_count,
            "avg_check": round(avg_check, 0),
        },
        "previous": {
            "revenue": prev_revenue,
            "orders": prev_count,
            "avg_check": round(prev_avg, 0),
        },
        "changes": {
            "revenue": pct_change(current_revenue, prev_revenue),
            "orders": pct_change(current_count, prev_count),
            "avg_check": pct_change(avg_check, prev_avg),
        },
        "daily": daily_data,
        "ai": {
            "profit": round(ai_profit_total, 2),
            "previous_profit": round(prev_ai_profit_total, 2),
            "change_pct": pct_change(ai_profit_total, prev_ai_profit_total),
            "daily_profit": [
                {"date": row["date"], "profit": round(float(row.get("ai_profit") or 0.0), 2)}
                for row in daily_data
            ],
        },
        "top_items": top_items,
        "funnel": {
            "dialogs": funnel_dialogs,
            "draft_orders": funnel_drafts,
            "finished_orders": funnel_finished,
        },
        "escalations": {
            "count": esc_count,
            "previous_count": prev_esc,
            "change_pct": (
                round((esc_count - prev_esc) / prev_esc * 100, 1)
                if prev_esc
                else None
            ),
        },
        "automation": {
            "rate_pct": automation_rate,
            "finished_without_operator": auto_cnt,
            "finished_total": funnel_finished,
            "broad_rate_pct": automation_broad_pct,
            "orders_without_operator": broad_auto_cnt,
            "orders_total": broad_total_cnt,
        },
        "menu_engineering": menu_engineering,
        "delivery_geo": delivery_geo,
        "heatmap": {
            "matrix": heatmap_matrix,
            "weekday_labels": ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"],
        },
        "sales_by_hour_utc": [
            {"hour": int(x["hour"]), "orders": int(x["orders"]), "revenue": round(float(x["revenue"]), 2)}
            for x in hour_buckets
        ],
    }


@router.get("/inbox/money-queue")
async def inbox_money_queue(
    request: Request,
    location_id: Annotated[int | None, Query(ge=1)] = None,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Единая money-at-risk очередь для Inbox: брошенные DRAFT, pending prepay, медленные чаты."""
    from app.services.money_queue import build_money_queue

    org_id = admin_org_from_session(request)
    allowed_location_ids, _ = await _location_scope_for_request(
        request,
        db,
        org_id,
        location_id,
    )
    return await build_money_queue(
        db,
        org_id,
        location_id=location_id,
        allowed_location_ids=allowed_location_ids,
    )


class ShiftActionBody(BaseModel):
    subtype: Literal["next", "skip", "complete", "reset_skips"] = Field(description="Тип действия смены")
    focus_id: str | None = Field(default=None, description="ID focus item из GET /shift/state")


class ShiftHeartbeatBody(BaseModel):
    focus_id: str = Field(description="ID focus item для продления lease")


@router.post("/shift/heartbeat")
async def shift_heartbeat(
    request: Request,
    body: ShiftHeartbeatBody,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """G10.2: продлить focus claim пока оператор на вкладке «Смена»."""
    from app.services.shift_state_engine import renew_focus_claim

    org_id = admin_org_from_session(request)
    renewed, _ = await renew_focus_claim(
        org_id,
        body.focus_id,
        request.session.get("staff_id"),
    )
    return {
        "ok": True,
        "renewed": renewed,
        "organization_id": org_id,
    }


@router.delete("/shift/heartbeat")
async def shift_heartbeat_release(
    request: Request,
    body: ShiftHeartbeatBody,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """G10.1: немедленно отпустить focus claim (уход с вкладки / pagehide)."""
    from app.services.shift_state_engine import release_focus_claim

    org_id = admin_org_from_session(request)
    released = await release_focus_claim(
        org_id,
        body.focus_id,
        request.session.get("staff_id"),
    )
    return {"ok": True, "released": released, "organization_id": org_id}


@router.get("/shift/state")
async def shift_state(
    request: Request,
    location_id: Annotated[int | None, Query(ge=1)] = None,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """G10 v1: детерминированное состояние смены (S0–S5) поверх G5–G8."""
    from app.services.shift_state_engine import build_shift_state

    org_id = admin_org_from_session(request)
    operator_id = request.session.get("staff_id")
    allowed_location_ids, _ = await _location_scope_for_request(
        request,
        db,
        org_id,
        location_id,
    )
    payload = await build_shift_state(
        db,
        org_id,
        location_id=location_id,
        allowed_location_ids=allowed_location_ids,
        operator_id=operator_id,
    )
    return {"ok": True, "organization_id": org_id, **payload}


@router.post("/shift/action")
async def shift_action_endpoint(
    request: Request,
    body: ShiftActionBody,
    location_id: Annotated[int | None, Query(ge=1)] = None,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """G10: next / skip / complete для focus item; возвращает свежий state."""
    from app.services.shift_state_engine import apply_shift_action, build_shift_state

    org_id = admin_org_from_session(request)
    operator_id = request.session.get("staff_id")
    allowed_location_ids, _ = await _location_scope_for_request(
        request,
        db,
        org_id,
        location_id,
    )
    await apply_shift_action(
        db,
        org_id,
        body.subtype,
        body.focus_id,
        location_id=location_id,
        operator_id=operator_id,
    )
    payload = await build_shift_state(
        db,
        org_id,
        location_id=location_id,
        allowed_location_ids=allowed_location_ids,
        operator_id=operator_id,
    )
    return {"ok": True, "organization_id": org_id, **payload}
