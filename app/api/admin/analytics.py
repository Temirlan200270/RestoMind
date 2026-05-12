"""Analytics admin API (E0.1 split from _monolith.py)."""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import (
    Booking,
    ChatLog,
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
from app.services.org_iiko import resolve_org_iiko_credentials
from app.services.owner_roi import aggregate_org_window, build_achievements_week, build_today_narrative_ru
from app.services.readiness import build_admin_readiness_payload
from app.services.tenant_scope import (
    failed_tasks_tenant_clause as _failed_tasks_tenant_clause,
    orders_tenant_clause as _orders_tenant_clause,
)
from .deps import (
    _bookings_tenant_clause,
    _escalation_tenant_clause,
    _session_is_superadmin,
    admin_org_from_session,
    require_admin_session_active,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/admin",
    tags=["Analytics"],
    dependencies=[Depends(require_admin_session_active)],
)


# ─── Helpers ──────────────────────────────────────────────


def _iiko_env_configured() -> bool:
    return bool(str(settings.iiko_api_login or "").strip() and str(settings.iiko_organization_id or "").strip())


async def _iiko_effective_configured(db: AsyncSession, org_id: int) -> bool:
    c = await resolve_org_iiko_credentials(db, org_id)
    if c is not None:
        return True
    return _iiko_env_configured()


def _whatsapp_env_configured() -> bool:
    return bool(
        str(settings.whatsapp_api_token or "").strip()
        and str(settings.whatsapp_phone_number_id or "").strip()
    )


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
) -> dict:
    """Единый центр того, что требует внимания оператора или владельца платформы."""
    summary_mode = (mode or "full").strip().lower() == "summary"
    org_id = admin_org_from_session(request)
    is_superadmin = await _session_is_superadmin(request, db)
    now_utc = datetime.now(tz=timezone.utc)
    whatsapp_since = _sql_dt_for_filter(now_utc - timedelta(days=INCIDENT_WHATSAPP_LOOKBACK_DAYS))
    payment_since = _sql_dt_for_filter(now_utc - timedelta(days=INCIDENT_PAYMENT_LOOKBACK_DAYS))
    not_cancelled = Order.status != OrderStatus.CANCELLED.value
    org_orders = _orders_tenant_clause(org_id)

    groups: list[dict[str, Any]] = []

    iiko_where = [
        User.organization_id == org_id,
        org_orders,
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

    iiko_configured = await _iiko_effective_configured(db, org_id)
    integ = await build_status_payload(
        db,
        organization_id=int(org_id),
        iiko_configured=iiko_configured,
        whatsapp_configured=_whatsapp_env_configured(),
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
    for key, title in (("last_menu_sync", "Синхронизация меню iiko"), ("last_stoplist", "Синхронизация стоп-листа iiko")):
        slot = integ.get(key) if isinstance(integ, dict) else None
        if slot and slot.get("at") and not slot.get("ok"):
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
    ai_provider = (settings.ai_provider or "openai").strip().lower()
    ai_configured = (
        bool(str(settings.gemini_api_key or "").strip())
        if ai_provider == "gemini"
        else bool(str(settings.openai_api_key or "").strip())
    )
    if not ai_configured:
        integration_items.append(
            {
                "id": "integration:ai_config",
                "title": "AI не настроен",
                "subtitle": "Бот не сможет стабильно разбирать заявки и отвечать гостям.",
                "detail": f"Активный провайдер: {ai_provider or 'openai'}",
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
) -> dict:
    """Статистика для дашборда: выручка за сегодня, общие счётчики."""
    org_id = admin_org_from_session(request)
    now_utc = datetime.now(tz=timezone.utc)
    today_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    ts_lo = _sql_dt_for_filter(today_start)
    ts_hi = _sql_dt_for_filter(now_utc)
    ys_lo = _sql_dt_for_filter(today_start - timedelta(days=1))

    not_cancelled = Order.status != OrderStatus.CANCELLED
    org_orders = _orders_tenant_clause(org_id)

    total_q = await db.execute(
        select(func.count(Order.id), func.coalesce(func.sum(Order.total_price), 0))
        .where(not_cancelled, org_orders)
    )
    total_row = total_q.one()
    total_orders = total_row[0]
    total_revenue = float(total_row[1])

    today_q = await db.execute(
        select(func.count(Order.id), func.coalesce(func.sum(Order.total_price), 0))
        .where(
            not_cancelled,
            org_orders,
            Order.created_at >= ts_lo,
            Order.created_at <= ts_hi,
        )
    )
    today_row = today_q.one()
    today_orders = today_row[0]
    today_revenue = float(today_row[1])

    yesterday_q = await db.execute(
        select(func.count(Order.id), func.coalesce(func.sum(Order.total_price), 0))
        .where(
            not_cancelled,
            org_orders,
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

    # 7 дней по UTC: корзины в Python (7× SQL по суткам на SQLite с naive created_at давали нули при живых KPI)
    valid_keys: list[str] = [
        (today_start - timedelta(days=6 - i)).strftime("%Y-%m-%d") for i in range(7)
    ]
    valid_set = set(valid_keys)
    bucket: dict[str, dict[str, float | int]] = defaultdict(
        lambda: {"revenue": 0.0, "orders": 0, "ai_profit": 0.0},
    )
    week_floor_sql = _sql_dt_for_filter(today_start - timedelta(days=6))
    week_rows = await db.execute(
        select(Order.created_at, Order.total_price, Order.items_json)
        .where(
            not_cancelled,
            org_orders,
            Order.created_at.isnot(None),
            Order.created_at >= week_floor_sql,
        )
    )
    for created_at, total_price, items_json in week_rows:
        dk = _order_day_key_utc(created_at)
        if dk and dk in valid_set:
            bucket[dk]["revenue"] += float(total_price or 0)
            bucket[dk]["orders"] += 1
            _off, _acc, rev_ai = upsell_stats_from_items_json(
                items_json if isinstance(items_json, dict) else None,
            )
            bucket[dk]["ai_profit"] += float(rev_ai or 0.0)
    daily_series = [
        {
            "date": k,
            "revenue": float(bucket[k]["revenue"]),
            "orders": int(bucket[k]["orders"]),
            "ai_profit": round(float(bucket[k]["ai_profit"]), 2),
        }
        for k in valid_keys
    ]

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

    ai_messages_today = int(
        await db.scalar(
            select(func.count(ChatLog.id)).where(
                ChatLog.organization_id == org_id,
                ChatLog.role == "assistant",
                ChatLog.created_at >= ts_lo,
                ChatLog.created_at <= ts_hi,
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

    return {
        "total_orders": total_orders,
        "today_orders": today_orders,
        "today_revenue": today_revenue,
        "total_revenue": total_revenue,
        "yesterday_orders": yesterday_orders,
        "yesterday_revenue": yesterday_revenue,
        "revenue_change_pct": _pct_change(today_revenue, yesterday_revenue),
        "orders_change_pct": _pct_change(float(today_orders), float(yesterday_orders)),
        "daily_series": daily_series,
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
async def roi_today_summary(request: Request, db: AsyncSession = Depends(get_db)) -> dict[str, object]:
    """
    ROI-нарратив за сегодня (UTC, как /stats) + «достижения» за последние 7 дней в TZ организации.
    """
    org_id = admin_org_from_session(request)
    org = await db.get(Organization, org_id)
    now_utc = datetime.now(tz=timezone.utc)
    today_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    cur = (getattr(org, "currency", None) or "KZT") if org is not None else "KZT"
    tz = (getattr(org, "timezone", None) or "UTC") if org is not None else "UTC"
    try:
        metrics = await aggregate_org_window(db, org_id, today_start, now_utc)
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
    }


@router.get("/activity")
async def dashboard_activity(
    request: Request,
    limit: int = Query(25, ge=5, le=100),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Activity feed для CEO/Staff: последние события, читаемые "за 1 секунду".
    Возвращает единый список, отсортированный по времени (desc).
    """
    org_id = admin_org_from_session(request)
    now_utc = datetime.now(tz=timezone.utc)
    since = _sql_dt_for_filter(now_utc - timedelta(days=7))

    items: list[dict[str, Any]] = []

    # Последние заказы
    o_rows = await db.execute(
        select(Order.id, Order.status, Order.total_price, Order.created_at).where(
            _orders_tenant_clause(org_id),
            Order.created_at.isnot(None),
            Order.created_at >= since,
        )
        .order_by(Order.created_at.desc())
        .limit(limit),
    )
    for oid, st, total, ts in o_rows.all():
        if ts is None:
            continue
        items.append(
            {
                "ts": _dt_as_utc(ts).isoformat(),
                "kind": "order",
                "title": f"Новый заказ #{oid}",
                "subtitle": f"{OrderStatus(st).value if hasattr(st, 'value') else st} · {float(total or 0):.0f} ₸",
                "ref": {"tab": "orders", "order_id": int(oid)},
            }
        )

    # Эскалации (бот попросил помощи)
    e_rows = await db.execute(
        select(EscalationEvent.phone, EscalationEvent.created_at, EscalationEvent.reason).where(
            _escalation_tenant_clause(org_id),
            EscalationEvent.created_at.isnot(None),
            EscalationEvent.created_at >= since,
        )
        .order_by(EscalationEvent.created_at.desc())
        .limit(limit),
    )
    for phone, ts, reason in e_rows.all():
        if ts is None:
            continue
        items.append(
            {
                "ts": _dt_as_utc(ts).isoformat(),
                "kind": "help",
                "title": "Нужна помощь клиенту",
                "subtitle": f"{(phone or '').strip()} · {(reason or '')[:80]}".strip(" ·"),
                "ref": {"tab": "operator_queue", "phone": (phone or "").strip()},
            }
        )

    # Не доставленные сообщения (failed)
    f_rows = await db.execute(
        select(ChatLog.user_id, ChatLog.id, ChatLog.created_at).where(
            ChatLog.organization_id == org_id,
            ChatLog.delivery_status == "failed",
            ChatLog.created_at.isnot(None),
            ChatLog.created_at >= since,
        )
        .order_by(ChatLog.created_at.desc())
        .limit(limit),
    )
    for _uid, cid, ts in f_rows.all():
        if ts is None:
            continue
        items.append(
            {
                "ts": _dt_as_utc(ts).isoformat(),
                "kind": "delivery_failed",
                "title": "Сообщение не доставлено",
                "subtitle": f"ID сообщения #{int(cid)}",
                "ref": {"tab": "chats"},
            }
        )

    # Бронирования
    b_rows = await db.execute(
        select(Booking.id, Booking.created_at).where(
            _bookings_tenant_clause(org_id),
            Booking.created_at.isnot(None),
            Booking.created_at >= since,
        )
        .order_by(Booking.created_at.desc())
        .limit(limit),
    )
    for bid, ts in b_rows.all():
        if ts is None:
            continue
        items.append(
            {
                "ts": _dt_as_utc(ts).isoformat(),
                "kind": "booking",
                "title": f"Новое бронирование #{int(bid)}",
                "subtitle": "",
                "ref": {"tab": "bookings"},
            }
        )

    items.sort(key=lambda x: x.get("ts") or "", reverse=True)
    items = items[: int(limit)]
    return {"items": items}


# ─── Аналитика ──────────────────────────────────────────


@router.get("/analytics")
async def analytics(
    request: Request,
    response: Response,
    period: str = Query("week", description="day, week, month, year, custom"),
    date_from: str | None = Query(None, description="Начало периода (YYYY-MM-DD)"),
    date_to: str | None = Query(None, description="Конец периода (YYYY-MM-DD)"),
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
    org_orders = _orders_tenant_clause(org_id)

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

    # Агрегаты текущего периода (SQL)
    cur_q = await db.execute(
        select(
            func.count(Order.id),
            func.coalesce(func.sum(Order.total_price), 0),
        )
        .where(
            not_cancelled,
            org_orders,
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
        entry = daily.get(key, {"revenue": 0.0, "orders": 0})
        daily_data.append(
            {
                "date": key,
                "revenue": entry["revenue"],
                "orders": entry["orders"],
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
