"""Draft Recovery — WA-nudge для брошенных DRAFT-заказов (Money Core)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import Order, OrderStatus, Organization, User
from app.db.session import redis_client
from app.services.dialog_mgr import UserState, set_pending_order, set_user_state, update_user_session_fields_in_db
from app.services.order_logic import build_summary_text_from_stored_items
from app.services.system_events import BusinessEvent, emit_event

logger = logging.getLogger(__name__)

DRAFT_RECOVERY_MIN_AGE = timedelta(minutes=45)
DRAFT_RECOVERY_DEDUPE_SEC = 86400
DRAFT_RECOVERY_MAX_PER_ORG_HOUR = 10
_REDIS_DEDUPE_PREFIX = "draft_recovery:order:"
_REDIS_HOUR_PREFIX = "draft_recovery:org:"


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _sql_dt(dt: datetime) -> datetime:
    u = _utc(dt)
    return u.replace(tzinfo=None) if settings.db_mode == "sqlite" else u


def _dedupe_key(order_id: int) -> str:
    return f"{_REDIS_DEDUPE_PREFIX}{int(order_id)}"


def _hour_bucket_key(org_id: int) -> str:
    hour_key = datetime.now(tz=timezone.utc).strftime("%Y%m%d%H")
    return f"{_REDIS_HOUR_PREFIX}{int(org_id)}:{hour_key}"


def _build_nudge_text(order: Order) -> str:
    total = float(order.total_price or 0)
    items_json = order.items_json if isinstance(order.items_json, dict) else {}
    summary = build_summary_text_from_stored_items(items_json)
    lines = [
        f"Вы начали заказ #{order.id}, но не завершили оформление.",
    ]
    if summary:
        lines.append("")
        lines.append(summary.strip())
    if total > 0:
        lines.append("")
        lines.append(f"Итого: {total:,.0f} ₸".replace(",", " "))
    lines.append("")
    lines.append("Оформить заказ?")
    return "\n".join(lines)


async def _restore_confirming_state(
    db: AsyncSession,
    *,
    org_id: int,
    phone: str,
    order_id: int,
) -> None:
    await update_user_session_fields_in_db(
        db,
        phone=phone,
        organization_id=org_id,
        current_state=UserState.CONFIRMING_ORDER.value,
        current_pending_order_id=int(order_id),
        transition_source="draft_recovery",
        transition_reason="stale_draft_nudge",
        transition_context={"order_id": int(order_id)},
    )
    await set_pending_order(redis_client, phone, int(order_id), organization_id=org_id)
    await set_user_state(
        redis_client,
        phone,
        UserState.CONFIRMING_ORDER,
        organization_id=org_id,
    )


async def send_draft_recovery_nudge(
    db: AsyncSession,
    *,
    order: Order,
    phone: str,
    org_id: int,
) -> bool:
    """Отправляет WA-кнопки «Оформить?» / «Отменить»; dedupe 1 раз / 24ч на заказ."""
    from app.integrations.whatsapp import send_interactive_buttons, send_message

    phone_s = (phone or "").strip()
    if not phone_s:
        return False

    try:
        if not await redis_client.set(_dedupe_key(order.id), "1", nx=True, ex=DRAFT_RECOVERY_DEDUPE_SEC):
            return False
    except Exception:
        logger.debug("draft_recovery dedupe skipped order=%s", order.id, exc_info=True)

    await _restore_confirming_state(db, org_id=org_id, phone=phone_s, order_id=int(order.id))

    text = _build_nudge_text(order)
    buttons = [
        {"id": "confirm", "title": "✅ Оформить"},
        {"id": "cancel", "title": "❌ Отменить"},
    ]
    try:
        result = await send_interactive_buttons(phone_s, text, buttons)
        if not result.ok:
            await send_message(phone_s, f"{text}\n\nОтветьте «Да» или «Нет».")
    except Exception:
        logger.exception("draft_recovery send failed order=%s phone=%s", order.id, phone_s)
        try:
            await redis_client.delete(_dedupe_key(order.id))
        except Exception:
            pass
        return False

    await emit_event(
        db,
        BusinessEvent(
            id=f"order.draft_recovery_sent:{order.id}",
            org_id=int(org_id),
            type="order.draft_recovery_sent",
            actor="system",
            entity_type="order",
            entity_id=int(order.id),
            payload={
                "order_id": int(order.id),
                "phone_last4": phone_s[-4:] if len(phone_s) >= 4 else phone_s,
                "total_price": float(order.total_price or 0),
            },
        ),
    )
    logger.info("draft_recovery nudge sent org=%s order=%s phone=%s", org_id, order.id, phone_s)
    return True


async def maybe_emit_draft_recovered(db: AsyncSession, order: Order) -> None:
    """G6: если заказ подтверждён после nudge — учитываем recovered $ (dedupe по order id)."""
    if order is None or not order.organization_id:
        return
    try:
        sent = await redis_client.get(_dedupe_key(int(order.id)))
        if not sent:
            return
    except Exception:
        return
    amount = round(float(order.total_price or 0), 2)
    if amount <= 0:
        return
    await emit_event(
        db,
        BusinessEvent(
            id=f"order.draft_recovered:{order.id}",
            org_id=int(order.organization_id),
            type="order.draft_recovered",
            actor="customer",
            entity_type="order",
            entity_id=int(order.id),
            payload={
                "order_id": int(order.id),
                "amount_kzt": amount,
                "total_price": amount,
            },
        ),
    )


async def run_draft_recovery_for_org(db: AsyncSession, org_id: int) -> int:
    if not getattr(settings, "draft_recovery_enabled", True):
        return 0

    try:
        raw = await redis_client.get(_hour_bucket_key(org_id))
        sent_hour = int(raw or 0)
    except Exception:
        sent_hour = 0
    if sent_hour >= DRAFT_RECOVERY_MAX_PER_ORG_HOUR:
        return 0

    cutoff = _sql_dt(datetime.now(tz=timezone.utc) - DRAFT_RECOVERY_MIN_AGE)
    rows = (
        await db.execute(
            select(Order, User.phone, User.current_state)
            .join(User, User.id == Order.user_id)
            .where(
                Order.organization_id == org_id,
                Order.status == OrderStatus.DRAFT.value,
                Order.updated_at <= cutoff,
            )
            .order_by(Order.updated_at.asc())
            .limit(DRAFT_RECOVERY_MAX_PER_ORG_HOUR - sent_hour)
        )
    ).all()

    sent = 0
    for order, phone, user_state in rows:
        if str(user_state or "").lower() == UserState.HUMAN_MODE.value:
            continue
        items = (order.items_json or {}).get("items") if isinstance(order.items_json, dict) else None
        if not items:
            continue
        if float(order.total_price or 0) <= 0:
            continue
        try:
            ok = await send_draft_recovery_nudge(
                db,
                order=order,
                phone=str(phone or ""),
                org_id=org_id,
            )
        except Exception:
            logger.exception("draft_recovery failed org=%s order=%s", org_id, order.id)
            continue
        if ok:
            sent += 1

    if sent:
        try:
            await redis_client.set(_hour_bucket_key(org_id), str(sent_hour + sent), ex=3700)
        except Exception:
            pass
    return sent


async def draft_recovery_scheduled_tick(_ctx: dict[str, Any] | None = None) -> None:
    """ARQ cron: ищет DRAFT >45 мин и шлёт WA-nudge (dedupe 24ч/заказ)."""
    from app.db.session import async_session_factory

    async with async_session_factory() as db:
        org_ids = list(
            (
                await db.execute(
                    select(Organization.id).where(Organization.is_active.is_(True))
                )
            ).scalars().all()
        )

    total = 0
    for org_id in org_ids:
        try:
            async with async_session_factory() as db:
                n = await run_draft_recovery_for_org(db, int(org_id))
                if n:
                    await db.commit()
                total += n
        except Exception:
            logger.exception("draft_recovery_scheduled_tick org=%s", org_id)

    if total:
        logger.info("draft_recovery_scheduled_tick: sent %d nudges across %d orgs", total, len(org_ids))
