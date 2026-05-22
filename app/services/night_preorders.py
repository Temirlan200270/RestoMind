"""
Ночные предзаказы и Telegram «на смене».

Когда ресторан закрыт — новые заказы сохраняются с kind='night_preorder'.
ARQ cron morning_preorders_tick запускается каждые 5 мин:
  - если только что открылись (0–30 мин) и есть ночные заказы → сводка в Telegram
  - если через SHIFT_ALERT_TIMEOUT_MIN никто не нажал «Я на смене» → алерт суперадмину.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def get_pending_night_preorders(db: AsyncSession, org_id: int) -> list[Any]:
    """Черновые ночные предзаказы для организации."""
    from app.db.models import Order
    result = await db.execute(
        select(Order).where(
            Order.organization_id == org_id,
            Order.kind == "night_preorder",
            Order.status == "draft",
        )
    )
    return result.scalars().all()


async def activate_night_preorders(db: AsyncSession, org_id: int) -> int:
    """
    Активирует все ночные предзаказы организации:
    - kind → 'regular'
    - Отправляет каждому клиенту WA-сообщение с резюме + кнопки ✅/❌.
    Возвращает количество активированных заказов.
    """
    from app.db.models import User
    from app.integrations.whatsapp import send_interactive_buttons, send_message

    orders = await get_pending_night_preorders(db, org_id)
    count = 0
    for order in orders:
        try:
            order.kind = "regular"
            await db.flush()

            # Загрузить телефон клиента
            user = await db.get(User, order.user_id)
            if not user:
                continue
            phone = user.phone

            # Резюме заказа
            items_data = (order.items_json or {}).get("items", [])
            items_lines = []
            for it in items_data[:8]:
                name = it.get("name", "?")
                qty = it.get("quantity", 1)
                total = it.get("item_total", 0)
                items_lines.append(f"• {name} × {qty} — {int(total):,} ₸")
            total_price = int(order.total_price or 0)
            summary = "\n".join(items_lines) if items_lines else "— состав не указан"
            text = (
                f"🌅 Доброе утро! Мы открылись и готовы принять ваш ночной заказ:\n\n"
                f"{summary}\n\n"
                f"💰 Итого: {total_price:,} ₸\n\n"
                "Подтвердите или отмените заказ:"
            )
            buttons = [
                {"id": "confirm", "title": "✅ Подтвердить"},
                {"id": "cancel", "title": "❌ Отменить"},
            ]
            try:
                await send_interactive_buttons(phone, text, buttons)
            except Exception:
                await send_message(phone, text)
            count += 1
        except Exception as exc:
            logger.warning("activate_night_preorders: ошибка для order_id=%s: %s", order.id, exc)

    await db.commit()
    return count


async def send_shift_summary_to_telegram(
    org_id: int,
    orders: list[Any],
) -> None:
    """Отправляет сводку ночных предзаказов в Telegram с кнопкой «Я на смене»."""
    from app.integrations.telegram import send_ops_notification_html

    if not orders:
        return

    lines = [f"☀️ <b>Доброе утро! Ночные предзаказы ({len(orders)} шт.):</b>", ""]
    for i, order in enumerate(orders, 1):
        items_data = (order.items_json or {}).get("items", [])
        first_item = items_data[0].get("name", "?") if items_data else "—"
        more = f" (+{len(items_data) - 1})" if len(items_data) > 1 else ""
        total = int(order.total_price or 0)
        lines.append(f"{i}. {first_item}{more} — {total:,} ₸")

    lines += ["", "Нажмите кнопку, чтобы активировать предзаказы и уведомить клиентов."]

    reply_markup: dict = {
        "inline_keyboard": [[
            {"text": "🟢 Я на смене", "callback_data": f"on_shift:{org_id}"},
        ]]
    }
    await send_ops_notification_html(
        "\n".join(lines),
        organization_id=org_id,
        reply_markup=reply_markup,
    )


async def morning_preorders_tick(ctx: dict) -> None:
    """
    ARQ cron — каждые 5 мин.
    Проверяет каждую активную org: только что открылись (0–30 мин) + есть ночные предзаказы?
    """
    from app.core.config import settings
    from app.db.models import Organization
    from app.db.session import async_session_factory

    try:
        from app.integrations.redis_client import get_redis_client
        redis = await get_redis_client()
    except Exception:
        redis = None

    async with async_session_factory() as db:
        result = await db.execute(
            select(Organization).where(Organization.is_active.is_(True))
        )
        orgs = result.scalars().all()

    today_str = date.today().isoformat()

    for org in orgs:
        try:
            await _process_org_morning_tick(org, today_str, redis, settings)
        except Exception as exc:
            logger.warning("morning_preorders_tick: org_id=%s error: %s", org.id, exc)


async def _process_org_morning_tick(
    org: Any,
    today_str: str,
    redis: Any,
    settings: Any,
) -> None:
    from app.db.session import async_session_factory
    from app.services.time_context import check_operational_status
    from app.integrations.telegram import send_ops_notification_html

    op = check_operational_status(
        org.timezone,
        org.schedule_json,
        force_closed_until=org.force_closed_until,
    )
    if not op.is_business_open:
        return

    # Определяем, сколько минут прошло с открытия
    minutes_since_open = _minutes_since_opening(org)
    if minutes_since_open is None or minutes_since_open > 30:
        # Проверяем алерт суперадмину если истёк таймаут
        if redis and minutes_since_open is not None:
            pending_key = f"rm:shift:pending:{org.id}:{today_str}"
            try:
                pending = await redis.get(pending_key)
                if pending is not None:
                    # pending существует, но мы не активировали — значит не истёк
                    pass
                elif minutes_since_open > settings.shift_alert_timeout_min:
                    # Проверяем, был ли алерт уже отправлен
                    alert_key = f"rm:shift:alert:{org.id}:{today_str}"
                    already_alerted = await redis.get(alert_key)
                    if not already_alerted:
                        async with async_session_factory() as db:
                            orders = await get_pending_night_preorders(db, org.id)
                        if orders:
                            await send_ops_notification_html(
                                f"⚠️ Прошло {minutes_since_open} мин после открытия, "
                                f"но никто не нажал «Я на смене». "
                                f"Ожидают активации {len(orders)} ночных предзаказов.",
                                organization_id=org.id,
                            )
                            await redis.set(alert_key, "1", ex=86400)
            except Exception as exc:
                logger.debug("shift alert redis error: %s", exc)
        return

    # 0–30 минут после открытия
    sent_key = f"rm:shift:sent:{org.id}:{today_str}"
    if redis:
        try:
            already_sent = await redis.get(sent_key)
            if already_sent:
                return
        except Exception:
            pass

    async with async_session_factory() as db:
        orders = await get_pending_night_preorders(db, org.id)

    if not orders:
        return

    await send_shift_summary_to_telegram(org.id, orders)

    if redis:
        try:
            await redis.set(sent_key, "1", ex=86400)
            # pending key: TTL = shift_alert_timeout_min
            pending_key = f"rm:shift:pending:{org.id}:{today_str}"
            await redis.set(pending_key, "1", ex=settings.shift_alert_timeout_min * 60)
        except Exception as exc:
            logger.debug("morning tick redis set error: %s", exc)


def _minutes_since_opening(org: Any) -> int | None:
    """Сколько минут прошло с момента открытия сегодня (None — не можем определить)."""
    from app.services.time_context import _WEEKDAY_KEYS
    from app.services.timezones import zoneinfo_or_default

    schedule = org.schedule_json
    if not schedule:
        return None

    tz_norm = zoneinfo_or_default(org.timezone)
    now_local = datetime.now(tz_norm.zone)
    weekday_key = _WEEKDAY_KEYS[now_local.weekday()]
    day_sched = schedule.get(weekday_key) or {}
    open_str = day_sched.get("open") or ""
    if not open_str or not day_sched.get("is_open", True):
        return None

    try:
        oh, om = map(int, open_str.split(":"))
    except Exception:
        return None

    open_minutes = oh * 60 + om
    now_minutes = now_local.hour * 60 + now_local.minute
    diff = now_minutes - open_minutes
    # Обработка полуночных переходов
    if diff < -60:
        diff += 24 * 60
    return diff if diff >= 0 else None
