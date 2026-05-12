"""
Маркетинговые рассылки по клиентам через WhatsApp.

Сегменты: inactive_30d (не заказывали >30 дней), frequent (>3 заказов за 90 дней),
all_active (все активные клиенты).

send_blast_batch — ARQ-задача (fire via worker.py).
Рейтинг: settings.marketing_blast_rate_per_min сообщений/мин.
Уважает marketing_opt_out.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def get_segment_phones(
    db: AsyncSession, org_id: int, segment_type: str,
) -> list[tuple[int | None, str]]:
    """
    Возвращает [(user_id, phone), ...] для сегмента.
    Исключает пользователей с marketing_opt_out=True.
    """
    from app.db.models import Order, User
    now = datetime.now(timezone.utc)

    base_q = (
        select(User.id, User.phone)
        .where(
            User.organization_id == org_id,
            User.is_active.is_(True),
            User.marketing_opt_out.is_(False),
        )
    )

    if segment_type == "inactive_30d":
        cutoff = now - timedelta(days=30)
        subq = (
            select(Order.user_id)
            .where(
                Order.organization_id == org_id,
                Order.created_at >= cutoff,
                Order.status.notin_(["cancelled", "draft"]),
            )
            .distinct()
        )
        base_q = base_q.where(User.id.notin_(subq))
    elif segment_type == "frequent":
        cutoff = now - timedelta(days=90)
        subq = (
            select(Order.user_id)
            .where(
                Order.organization_id == org_id,
                Order.created_at >= cutoff,
                Order.status.notin_(["cancelled", "draft"]),
            )
            .group_by(Order.user_id)
            .having(func.count(Order.id) >= 3)
            .subquery()
        )
        base_q = base_q.join(subq, User.id == subq.c.user_id)
    # else: "all_active" — без дополнительных фильтров

    rows = (await db.execute(base_q)).all()
    return [(r[0], r[1]) for r in rows]


async def create_blast(
    db: AsyncSession,
    org_id: int,
    *,
    name: str,
    segment_type: str,
    message_text: str,
    template_name: str | None = None,
    scheduled_for: datetime | None = None,
) -> Any:
    """Создаёт черновик рассылки и наполняет список получателей."""
    from app.db.models import MarketingBlast, MarketingBlastRecipient

    recipients = await get_segment_phones(db, org_id, segment_type)
    blast = MarketingBlast(
        organization_id=org_id,
        name=name,
        segment_type=segment_type,
        message_text=message_text,
        template_name=template_name,
        scheduled_for=scheduled_for,
        status="draft",
        total_recipients=len(recipients),
    )
    db.add(blast)
    await db.flush()

    for user_id, phone in recipients:
        rec = MarketingBlastRecipient(
            blast_id=blast.id,
            user_id=user_id,
            phone=phone,
            status="pending",
        )
        db.add(rec)

    await db.commit()
    return blast


async def run_send_blast_batch(*, blast_id: int) -> None:
    """
    ARQ-задача. Читает pending recipients и отправляет сообщения с rate limit.
    Вызывается из worker.py::send_blast_batch.
    """
    from app.core.config import settings
    from app.db.models import MarketingBlast, MarketingBlastRecipient
    from app.db.session import async_session_factory
    from app.integrations.whatsapp import send_message, send_template

    delay_between = 60.0 / max(1, settings.marketing_blast_rate_per_min)

    async with async_session_factory() as db:
        blast = await db.get(MarketingBlast, blast_id)
        if blast is None or blast.status == "cancelled":
            return

        blast.status = "sending"
        blast.sent_at = datetime.now(timezone.utc)
        await db.commit()

    sent = 0
    failed = 0

    async with async_session_factory() as db:
        blast = await db.get(MarketingBlast, blast_id)
        if blast is None:
            return

        recipients_q = (
            select(MarketingBlastRecipient)
            .where(
                MarketingBlastRecipient.blast_id == blast_id,
                MarketingBlastRecipient.status == "pending",
            )
        )
        recs = (await db.execute(recipients_q)).scalars().all()

        for rec in recs:
            try:
                if blast.template_name:
                    ok = await send_template(rec.phone, blast.template_name)
                else:
                    await send_message(rec.phone, blast.message_text)
                    ok = True
                rec.status = "sent" if ok else "failed"
                rec.sent_at = datetime.now(timezone.utc)
                if ok:
                    sent += 1
                else:
                    failed += 1
            except Exception as exc:
                logger.warning("blast %s: send to %s failed: %s", blast_id, rec.phone, exc)
                rec.status = "failed"
                failed += 1

            await asyncio.sleep(delay_between)

        blast.sent_count = sent
        blast.failed_count = failed
        blast.status = "done"
        await db.commit()

    logger.info("blast_id=%s done: sent=%d failed=%d", blast_id, sent, failed)


async def run_scheduled_blasts() -> None:
    """Cron-задача: запускает рассылки, у которых scheduled_for <= now и статус draft."""
    from app.db.models import MarketingBlast
    from app.db.session import async_session_factory

    now = datetime.now(timezone.utc)
    async with async_session_factory() as db:
        from sqlalchemy import select
        rows = (await db.execute(
            select(MarketingBlast)
            .where(
                MarketingBlast.status == "draft",
                MarketingBlast.scheduled_for.isnot(None),
                MarketingBlast.scheduled_for <= now,
            )
        )).scalars().all()

    for blast in rows:
        logger.info("scheduled blast firing: blast_id=%s", blast.id)
        try:
            await run_send_blast_batch(blast_id=blast.id)
        except Exception as exc:
            logger.error("scheduled blast failed: blast_id=%s error=%s", blast.id, exc)
