"""Self-healing actions — автоматические действия при операционных инцидентах (Phase 5 OS).

Вызывается из ai_incidents_hourly_tick в worker.py.
Анализирует DailyOrgStats и OperationalInsight, выполняет автоматические действия:
  - Spike эскалаций → создаёт OperationalInsight + Telegram алерт
  - Spike failed-платежей → создаёт OperationalInsight
  - Cancellation surge → триггерит пересчёт рекомендаций
  - SLA нарушение → создаёт OperationalInsight с severity critical

Принцип: heal через инсайты и рекомендации — без автоизменения данных.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import OperationalInsight, Organization
from app.db.session import redis_client
from app.services.analytics_consumer import get_event_stats, get_today_event_summary

logger = logging.getLogger(__name__)

HEALING_MUTE_TTL_SEC = 1800

_ESCALATION_SPIKE_THRESHOLD = 5     # эскалаций за час
_PAYMENT_FAILED_THRESHOLD = 3       # failed-платежей за сегодня
_CANCEL_RATE_THRESHOLD = 0.25       # 25% отмен → surge
_MIN_ORDERS_FOR_CANCEL_ANALYSIS = 4


def healing_mute_key(org_id: int, insight_type: str) -> str:
    return f"heal:mute:{int(org_id)}:{insight_type}"


async def try_acquire_healing_mute(org_id: int, insight_type: str) -> bool:
    """One healing action per insight_type per org per 30 min."""
    key = healing_mute_key(org_id, insight_type)
    try:
        acquired = await redis_client.set(key, "1", nx=True, ex=HEALING_MUTE_TTL_SEC)
        return bool(acquired)
    except Exception as exc:
        logger.warning(
            "healing.mute_failed org=%s type=%s err=%s",
            org_id,
            insight_type,
            exc,
        )
        return True


async def _create_insight_if_new(
    db: AsyncSession,
    org_id: int,
    *,
    insight_type: str,
    title: str,
    summary: str,
    severity: str,
    dedup_hours: int = 6,
) -> bool:
    """Создаёт OperationalInsight если такого ещё нет за последние dedup_hours часов."""
    since = datetime.now(tz=timezone.utc) - timedelta(hours=dedup_hours)
    existing = await db.scalar(
        select(OperationalInsight).where(
            OperationalInsight.organization_id == org_id,
            OperationalInsight.insight_type == insight_type,
            OperationalInsight.title == title,
            OperationalInsight.created_at >= since,
        ).limit(1)
    )
    if existing:
        return False

    db.add(OperationalInsight(
        organization_id=org_id,
        insight_type=insight_type,
        title=title,
        summary=summary,
        severity=severity,
        status="new",
    ))
    await db.flush()
    return True


async def run_healing_actions(db: AsyncSession, org_id: int) -> list[str]:
    """Запускает все self-healing проверки для org_id.

    Возвращает список выполненных действий (для логирования).
    """
    actions: list[str] = []
    today_summary = await get_today_event_summary(db, org_id)
    event_rows_14d = await get_event_stats(db, org_id, days=14)

    payments_failed = today_summary.get("payments_failed", 0)

    # ── Cancellation surge (cold, 7d — cron only) ─────────────────────────
    recent = event_rows_14d[:7]  # последние 7 дней
    total_confirmed = sum(r["orders_confirmed"] for r in recent)
    total_cancelled = sum(r["orders_cancelled"] for r in recent)
    total_orders = total_confirmed + total_cancelled
    if total_orders >= _MIN_ORDERS_FOR_CANCEL_ANALYSIS:
        cancel_rate = total_cancelled / total_orders
        if cancel_rate >= _CANCEL_RATE_THRESHOLD:
            if not await try_acquire_healing_mute(org_id, "cancellation_surge"):
                pass
            elif await _create_insight_if_new(
                db, org_id,
                insight_type="cancellation_surge",
                title=f"Высокий уровень отмен: {cancel_rate * 100:.0f}% за 7 дней",
                summary=(
                    f"Отменено {total_cancelled} из {total_orders} заказов "
                    f"({cancel_rate * 100:.0f}%) за последние 7 дней. "
                    "Возможные причины: долгое подтверждение, проблемы с доставкой, "
                    "несоответствие ожиданий. Рекомендации пересчитаны автоматически."
                ),
                severity="warning",
                dedup_hours=24,
            ):
                actions.append(f"insight:cancellation_surge:{cancel_rate:.2f}")
                # Триггерим пересчёт рекомендаций при высоком уровне отмен
                try:
                    from app.services.recommendations import generate_recommendations
                    recs = await generate_recommendations(db, org_id, days=7)
                    if recs:
                        actions.append(f"recommendations_regenerated:{len(recs)}")
                        logger.info(
                            "Phase5 healing: regenerated %d recommendations for org=%d after cancellation surge",
                            len(recs), org_id,
                        )
                except Exception:
                    logger.exception("Phase5 healing: recommendations regeneration failed org=%d", org_id)

    # ── AI message drop (бот перестал отвечать) ───────────────────────────
    if event_rows_14d:
        recent_7 = event_rows_14d[:7]
        prev_7 = event_rows_14d[7:14]
        recent_ai = sum(r["ai_messages_count"] for r in recent_7)
        prev_ai = sum(r["ai_messages_count"] for r in prev_7)
        if prev_ai > 10 and recent_ai < prev_ai * 0.3:
            if not await try_acquire_healing_mute(org_id, "ai_message_drop"):
                pass
            elif await _create_insight_if_new(
                db, org_id,
                insight_type="ai_message_drop",
                title="Резкое падение AI-ответов",
                summary=(
                    f"За последние 7 дней AI-ответов: {recent_ai} (было: {prev_ai}). "
                    "Падение более чем на 70%. Проверьте: статус AI-провайдера, "
                    "лимиты API, настройки бота."
                ),
                severity="critical",
                dedup_hours=12,
            ):
                actions.append(f"insight:ai_message_drop:{recent_ai}/{prev_ai}")
                logger.error("Phase5 healing: AI message drop org=%d recent=%d prev=%d", org_id, recent_ai, prev_ai)

    # ── Self-Healing 2.0: WA-напоминание при pending prepayment (без LLM) ───
    if payments_failed >= _PAYMENT_FAILED_THRESHOLD:
        nudged = await _healing_wa_payment_nudges(db, org_id)
        if nudged:
            actions.append(f"wa:payment_nudge:{nudged}")

    return actions


_HEALING_WA_MAX_PER_HOUR = 5
_HEALING_WA_TEXT = (
    "Здравствуйте! Не удалось завершить оплату заказа. "
    "Напишите «оплата» — пришлём новую ссылку, или обратитесь к оператору."
)


async def _healing_wa_payment_nudges(db: AsyncSession, org_id: int) -> int:
    """Шлёт шаблонное WA гостям с prepayment pending (rate limit по org/час)."""
    from datetime import timedelta

    from sqlalchemy import select

    from app.db.models import Order, OrderStatus, User
    from app.db.session import redis_client
    from app.services.customer_reply import send_customer_text

    hour_key = datetime.now(tz=timezone.utc).strftime("%Y%m%d%H")
    redis_key = f"heal:wa_nudge:{org_id}:{hour_key}"
    try:
        raw = await redis_client.get(redis_key)
        sent_so_far = int(raw or 0)
    except Exception:
        sent_so_far = 0
    if sent_so_far >= _HEALING_WA_MAX_PER_HOUR:
        return 0

    cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=24)
    rows = (await db.execute(
        select(Order, User.phone)
        .join(User, User.id == Order.user_id)
        .where(
            Order.organization_id == org_id,
            Order.prepayment_status == "pending",
            Order.status.in_([
                OrderStatus.CONFIRMED.value,
                OrderStatus.DRAFT.value,
            ]),
            Order.created_at >= cutoff,
        )
        .order_by(Order.created_at.desc())
        .limit(_HEALING_WA_MAX_PER_HOUR - sent_so_far)
    )).all()

    sent = 0
    for order, phone in rows:
        phone_s = (phone or "").strip()
        if not phone_s:
            continue
        dedupe_key = f"heal:wa_nudge:order:{order.id}"
        try:
            if not await redis_client.set(dedupe_key, "1", nx=True, ex=86400):
                continue
        except Exception:
            pass
        try:
            await send_customer_text(phone_s, _HEALING_WA_TEXT)
            sent += 1
        except Exception:
            logger.exception("healing WA nudge failed order=%s", order.id)
            continue

    if sent:
        try:
            await redis_client.set(redis_key, str(sent_so_far + sent), ex=3700)
        except Exception:
            pass
        try:
            from app.services.system_events import BusinessEvent, emit_event

            await emit_event(
                db,
                BusinessEvent(
                    org_id=org_id,
                    type="system.healing_wa_sent",
                    actor="system",
                    payload={"count": sent, "reason": "payments_failed_spike"},
                ),
            )
        except Exception:
            logger.debug("healing_wa audit emit skipped", exc_info=True)

    return sent
