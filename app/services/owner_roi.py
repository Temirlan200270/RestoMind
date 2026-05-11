"""
ROI-нарратив и «достижения» для владельца: агрегаты по окну дат + человекочитаемый текст.

Логика вынесена из FastAPI-роутов, чтобы переиспользовать в еженедельном Telegram-дайджесте.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import ChatLog, FailedTask, Order, OrderStatus, Organization
from app.services.timezones import zoneinfo_or_default
from app.services.intelligence_analytics import (
    list_recommendation_events,
    menu_engineering_rows,
    upsell_stats_from_items_json,
)
from app.services.tenant_scope import (
    failed_tasks_tenant_clause as _failed_tasks_tenant_clause,
    orders_tenant_clause as _orders_tenant_clause,
)

logger = logging.getLogger(__name__)


def _dt_as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _sql_dt_for_filter(dt: datetime) -> datetime:
    u = _dt_as_utc(dt)
    if settings.db_mode == "sqlite":
        return u.replace(tzinfo=None)
    return u

_WEEKDAYS_RU = (
    "понедельник",
    "вторник",
    "среда",
    "четверг",
    "пятница",
    "суббота",
    "воскресенье",
)


def _money_ru(amount: float, suffix: str) -> str:
    n = round(float(amount), 0)
    s = f"{n:,.0f}".replace(",", " ")
    return f"{s} {suffix}".strip()


def _fmt_hours_from_minutes(minutes: float) -> str:
    if minutes >= 60:
        h = minutes / 60.0
        if h >= 10:
            return f"{round(h)} часов"
        return f"{round(h, 1)} ч"
    return f"{int(round(minutes))} мин"


async def aggregate_org_window(
    db: AsyncSession,
    org_id: int,
    ts_lo_utc: datetime,
    ts_hi_utc: datetime,
) -> dict[str, Any]:
    """Сводка за полуинтервал [ts_lo, ts_hi] в UTC (как в /stats)."""
    not_cancelled = Order.status != OrderStatus.CANCELLED
    org_orders = _orders_tenant_clause(org_id)
    ts_lo = _sql_dt_for_filter(ts_lo_utc)
    ts_hi = _sql_dt_for_filter(ts_hi_utc)

    row = (
        await db.execute(
            select(func.count(Order.id), func.coalesce(func.sum(Order.total_price), 0)).where(
                not_cancelled,
                org_orders,
                Order.created_at >= ts_lo,
                Order.created_at <= ts_hi,
            ),
        )
    ).one()
    orders_count = int(row[0] or 0)
    revenue = float(row[1] or 0)

    upsell_offers = 0
    upsell_accepts = 0
    upsell_revenue = 0.0
    ij_rows = await db.execute(
        select(Order.items_json).where(
            not_cancelled,
            org_orders,
            Order.created_at >= ts_lo,
            Order.created_at <= ts_hi,
        ),
    )
    for (ij,) in ij_rows.all():
        o, a, rev = upsell_stats_from_items_json(ij if isinstance(ij, dict) else None)
        upsell_offers += o
        upsell_accepts += a
        upsell_revenue += float(rev or 0)

    active_guests = int(
        await db.scalar(
            select(func.count(func.distinct(ChatLog.user_id))).where(
                ChatLog.organization_id == org_id,
                ChatLog.created_at >= ts_lo,
                ChatLog.created_at <= ts_hi,
            ),
        )
        or 0,
    )

    ai_messages = int(
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
    minutes_per_message = 1.5
    time_saved_minutes = round(float(ai_messages) * minutes_per_message, 1)

    help_events = int(
        await db.scalar(
            select(func.count(FailedTask.id)).where(
                _failed_tasks_tenant_clause(org_id),
                FailedTask.created_at >= ts_lo,
                FailedTask.created_at <= ts_hi,
            ),
        )
        or 0,
    )

    return {
        "orders_count": orders_count,
        "revenue": round(revenue, 2),
        "upsell_offers": upsell_offers,
        "upsell_accepts": upsell_accepts,
        "upsell_revenue": round(upsell_revenue, 2),
        "active_guests": active_guests,
        "ai_messages": ai_messages,
        "ai_time_saved_minutes": time_saved_minutes,
        "help_events": help_events,
    }


def build_today_narrative_ru(metrics: dict[str, Any], currency: str) -> str:
    """Короткий абзац для дашборда (сегодня по UTC, как остальная аналитика)."""
    suf = (currency or "KZT").upper()
    sym = "₸" if suf == "KZT" else suf
    og = int(metrics.get("active_guests") or 0)
    oc = int(metrics.get("orders_count") or 0)
    rev = float(metrics.get("revenue") or 0)
    up = float(metrics.get("upsell_revenue") or 0)
    tm = float(metrics.get("ai_time_saved_minutes") or 0)

    parts: list[str] = []
    if og > 0 or oc > 0 or rev > 0 or up > 0:
        guest_bit = ""
        if og == 1:
            guest_bit = " Активных гостей в чате по данным сервера — 1."
        elif og > 1:
            guest_bit = f" Активных гостей в чате — {og}."
        if oc == 0:
            parts.append(
                "Сегодня заказов по данным сервера пока нет." + (guest_bit or " Переписок с гостями тоже не зафиксировано."),
            )
        else:
            parts.append(
                f"Сегодня оформлено {oc} заказов на сумму {_money_ru(rev, sym)}.{guest_bit}",
            )
        if up > 0:
            parts.append(f"Допродажи через ИИ — ещё {_money_ru(up, sym)}.")
        if tm > 0:
            parts.append(f"Это примерно {_fmt_hours_from_minutes(tm)} работы оператора на переписке.")
    else:
        parts.append("Сегодня по данным сервера пока тихо — как только пойдут заказы и переписки, здесь появится история успеха.")

    sub = int(getattr(settings, "owner_subscription_monthly_kzt", 0) or 0)
    if sub > 0 and up > 0:
        rough_month_mult = round((up * 30) / float(sub), 1) if sub else None
        if rough_month_mult and rough_month_mult >= 1:
            parts.append(
                f"Если экстраполировать допродажи ИИ на месяц примерно в том же темпе, они перекрывают подписку примерно в {rough_month_mult}× (оценка, без учёта себестоимости блюд)."
            )

    return " ".join(parts)


def _previous_week_bounds_local(org_tz: str) -> tuple[datetime, datetime]:
    """Пн 00:00 … Вс 23:59:59 предыдущей календарной недели в зоне org."""
    zi = zoneinfo_or_default(org_tz, default="Etc/GMT-5").zone
    now = datetime.now(zi)
    this_monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    last_monday = this_monday - timedelta(days=7)
    last_sunday_end = this_monday - timedelta(microseconds=1)
    return last_monday, last_sunday_end


def _bounds_to_utc_naive_pair(a: datetime, b: datetime) -> tuple[datetime, datetime]:
    """Для SQL: пара UTC-aware → naive UTC при sqlite."""
    au = a.astimezone(timezone.utc)
    bu = b.astimezone(timezone.utc)
    return _sql_dt_for_filter(au), _sql_dt_for_filter(bu)


async def build_week_digest_payload(db: AsyncSession, org: Organization) -> dict[str, Any] | None:
    """Статистика за прошедшую календарную неделю (локально для организации)."""
    lo_l, hi_l = _previous_week_bounds_local(org.timezone or "UTC")
    m = await aggregate_org_window(db, int(org.id), lo_l.astimezone(timezone.utc), hi_l.astimezone(timezone.utc))
    cur = (org.currency or "KZT").strip().upper() or "KZT"
    sym = "₸" if cur == "KZT" else cur
    upsell_share = None
    if m["revenue"] > 0 and m["upsell_revenue"] > 0:
        upsell_share = round(100.0 * float(m["upsell_revenue"]) / float(m["revenue"]), 1)
    base = (settings.public_base_url or "").strip().rstrip("/")
    dash_link = f"{base}/admin#dashboard" if base else "/admin#dashboard"
    text = (
        f"RestoMind за неделю ({lo_l.strftime('%d.%m')}–{hi_l.strftime('%d.%m')}): "
        f"{m['active_guests']} активных гостей в чате, {m['orders_count']} заказов на {_money_ru(m['revenue'], sym)}"
    )
    if m["upsell_revenue"] > 0:
        text += f", допродажи ИИ {_money_ru(m['upsell_revenue'], sym)}"
        if upsell_share is not None:
            text += f" ({upsell_share}% к выручке недели)"
    text += f", обращений «нужен человек» — {m['help_events']}. Подробнее: {dash_link}"
    return {"text": text, "metrics": m}


async def build_achievements_week(
    db: AsyncSession,
    org_id: int,
    org_tz: str,
) -> list[dict[str, str]]:
    """3–4 коротких bullet'а по прошлым 7 суткам (локальные даты для подписи дня)."""
    zi = zoneinfo_or_default(org_tz, default="Etc/GMT-5").zone
    now_local = datetime.now(zi)
    end_local = now_local.replace(hour=23, minute=59, second=59, microsecond=999999)
    start_local = (end_local - timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0)
    ts_lo, ts_hi = _bounds_to_utc_naive_pair(start_local, end_local)

    not_cancelled = Order.status != OrderStatus.CANCELLED
    org_orders = _orders_tenant_clause(org_id)
    o_rows = await db.execute(
        select(Order).where(
            not_cancelled,
            org_orders,
            Order.created_at >= ts_lo,
            Order.created_at <= ts_hi,
        ),
    )
    orders = list(o_rows.scalars().all())

    best_ev: dict[str, Any] | None = None
    for o in orders:
        ij = o.items_json if isinstance(o.items_json, dict) else None
        for ev in list_recommendation_events(ij):
            if ev.get("accepted") is not True:
                continue
            rev = float(ev.get("accepted_revenue_kzt") or 0)
            if best_ev is None or rev > float(best_ev.get("revenue") or 0):
                best_ev = {
                    "revenue": rev,
                    "label": (ev.get("offered") or "доп. позиция")[:80],
                    "when": o.created_at,
                }

    ach: list[dict[str, str]] = []
    if best_ev and float(best_ev["revenue"]) > 0:
        ts = best_ev.get("when")
        wd = ""
        if isinstance(ts, datetime):
            loc = _dt_as_utc(ts).astimezone(zi)
            wd = _WEEKDAYS_RU[loc.weekday()]
        cur = "₸"
        ach.append(
            {
                "title": "Крупнейшая допродажа недели",
                "body": f"+{_money_ru(float(best_ev['revenue']), cur)} — {best_ev['label']}" + (f" ({wd})" if wd else ""),
            },
        )

    # Сообщения ассистента по локальным дням
    day_counts: dict[str, int] = defaultdict(int)
    c_rows = await db.execute(
        select(ChatLog.created_at).where(
            ChatLog.organization_id == org_id,
            ChatLog.role == "assistant",
            ChatLog.created_at >= ts_lo,
            ChatLog.created_at <= ts_hi,
        ),
    )
    for (ts,) in c_rows.all():
        if ts is None:
            continue
        loc = _dt_as_utc(ts).astimezone(zi)
        dk = loc.strftime("%Y-%m-%d")
        day_counts[dk] += 1
    if day_counts:
        best_day = max(day_counts, key=lambda k: day_counts[k])
        best_n = day_counts[best_day]
        loc_d = datetime.strptime(best_day, "%Y-%m-%d").replace(tzinfo=zi)
        wd = _WEEKDAYS_RU[loc_d.weekday()]
        minutes = best_n * 1.5
        ach.append(
            {
                "title": "Пик нагрузки на бота",
                "body": f"Больше всего ответов ИИ в {wd}: сэкономили примерно {_fmt_hours_from_minutes(minutes)} ручной переписки.",
            },
        )

    rows = menu_engineering_rows(orders)
    best_conv = next((r for r in rows if int(r.get("offers") or 0) >= 3), None)
    if best_conv and float(best_conv.get("conversion_pct") or 0) > 0:
        ach.append(
            {
                "title": "Лучшая конверсия допродаж",
                "body": f"«{best_conv.get('label')}»: {best_conv.get('conversion_pct')}% приняли предложение (из {best_conv.get('offers')} показов).",
            },
        )

    if len(ach) < 3 and orders:
        ach.append(
            {
                "title": "Заказы за 7 дней",
                "body": f"Оформлено {len(orders)} заказов — держим связь с гостями и меню в актуальном виде.",
            },
        )

    return ach[:4]
