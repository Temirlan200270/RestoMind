"""
Диалоги WhatsApp в админке: список, история, triage, отправка оператором (E0.1).

Вынесено из ``app/api/admin/_monolith.py`` без изменения путей и контрактов ответов.

Маршруты:

* ``GET/POST .../chats``, ``/chats/{phone}``, ``/chats/{phone}/*`` — см. OpenAPI админки.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ChatLog, Organization, User
from app.db.session import get_db, redis_client
from app.integrations.whatsapp import send_message
from app.services.chat_delivery import finalize_outbound_delivery
from app.services.dialog_mgr import (
    UserState,
    get_user_state,
    set_user_state_durable,
)
from app.services.bot_sla_status import (
    SHORT_MODE_THRESHOLD,
    chat_live_pulse,
    get_slow_chat_count,
    is_chat_slow,
)
from app.services.events import publish_event
from app.services.intent_router import get_or_create_user
from app.services.system_events import BusinessEvent, emit_event
from app.services.tenant_scope import allowed_location_ids_for_staff, chat_logs_location_filter

from .deps import (
    _session_is_superadmin,
    _session_staff_user,
    admin_actor_key,
    admin_org_from_session,
    require_admin_session_active,
)
from .schemas import TextRequest

logger = logging.getLogger(__name__)

chats_router = APIRouter(dependencies=[Depends(require_admin_session_active)])


def _parse_dt(value: object) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _chat_triage_from_meta(meta: object) -> dict[str, Any]:
    src = dict(meta or {}) if isinstance(meta, dict) else {}
    raw = src.get("chat_triage")
    triage = dict(raw) if isinstance(raw, dict) else {}
    state = str(triage.get("state") or "active").lower()
    if state not in {"active", "closed"}:
        state = "active"
    return {
        "state": state,
        "assignee": str(triage.get("assignee") or ""),
        "snoozed_until": triage.get("snoozed_until"),
        "snooze_reason": str(triage.get("snooze_reason") or ""),
        "closed_at": triage.get("closed_at"),
        "closed_by": str(triage.get("closed_by") or ""),
    }


async def _user_for_chat(db: AsyncSession, org_id: int, phone: str) -> User:
    user = await db.scalar(select(User).where(User.phone == phone, User.organization_id == org_id))
    if user is None:
        raise HTTPException(status_code=404, detail="Chat user not found")
    return user


async def _save_chat_triage(
    request: Request,
    db: AsyncSession,
    phone: str,
    patch: dict[str, Any],
) -> dict:
    org_id = admin_org_from_session(request)
    user = await _user_for_chat(db, org_id, phone)
    meta = dict(user.meta_json or {}) if isinstance(user.meta_json, dict) else {}
    triage = _chat_triage_from_meta(meta)
    triage.update(patch)
    meta["chat_triage"] = triage
    user.meta_json = meta
    await db.commit()
    await publish_event("chat_triage_updated", {"phone": phone, "organization_id": org_id, "triage": triage})
    return {"ok": True, "phone": phone, "triage": triage}


@chats_router.get("/chats")
async def list_chats_sidebar(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    cursor_at: str | None = Query(None, description="Cursor: lastAt ISO (for infinite scroll)"),
    cursor_id: int | None = Query(None, ge=1, description="Cursor: last message id (tie-breaker)"),
    mode: Literal["active", "mine", "closed", "snoozed", "all"] = Query("active"),
    location_id: int | None = Query(None, ge=1),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Список диалогов для боковой панели админки: телефон, превью последнего сообщения, время.
    Infinite scroll: курсор по (lastAt, last_id). Без full-scan chat_logs.
    """
    org_id = admin_org_from_session(request)
    staff = await _session_staff_user(request, db)
    is_super = await _session_is_superadmin(request, db)
    allowed_location_ids = await allowed_location_ids_for_staff(
        db,
        staff=staff,
        org_id=org_id,
        is_superadmin=is_super,
    )
    if location_id is not None and allowed_location_ids is not None and int(location_id) not in allowed_location_ids:
        raise HTTPException(status_code=403, detail="Location is not allowed")

    cur_dt: datetime | None = None
    if cursor_at:
        try:
            cur_dt = datetime.fromisoformat(cursor_at.replace("Z", "+00:00"))
            if cur_dt.tzinfo is None:
                cur_dt = cur_dt.replace(tzinfo=timezone.utc)
            else:
                cur_dt = cur_dt.astimezone(timezone.utc)
        except Exception:
            cur_dt = None

    # last message per user via window function (portable for Postgres/SQLite).
    base_sq = (
        select(
            ChatLog.id.label("log_id"),
            ChatLog.user_id.label("user_id"),
            ChatLog.created_at.label("last_at"),
            ChatLog.content.label("content"),
            ChatLog.role.label("last_role"),
            User.phone.label("phone"),
            User.name.label("user_name"),
            User.meta_json.label("user_meta"),
            func.row_number()
            .over(
                partition_by=ChatLog.user_id,
                order_by=(ChatLog.created_at.desc(), ChatLog.id.desc()),
            )
            .label("rn"),
        )
        .join(User, User.id == ChatLog.user_id)
        .where(
            User.organization_id == org_id,
            chat_logs_location_filter(allowed_location_ids, location_id),
        )
        .subquery()
    )

    stmt = (
        select(base_sq)
        .where(base_sq.c.rn == 1)
    )
    if cur_dt is not None and cursor_id is not None:
        stmt = stmt.where(
            or_(
                base_sq.c.last_at < cur_dt,
                and_(base_sq.c.last_at == cur_dt, base_sq.c.log_id < int(cursor_id)),
            )
        )
    elif cur_dt is not None:
        stmt = stmt.where(base_sq.c.last_at < cur_dt)
    elif cursor_id is not None:
        stmt = stmt.where(base_sq.c.log_id < int(cursor_id))

    stmt = stmt.order_by(base_sq.c.last_at.desc(), base_sq.c.log_id.desc()).limit(limit + 1)

    result = await db.execute(stmt)
    rows = result.all()

    has_more = len(rows) > limit
    rows = rows[:limit]
    chats: list[dict] = []
    next_cursor: dict[str, object] | None = None
    staff_key = admin_actor_key(request)
    now = datetime.now(timezone.utc)
    slow_chats_count = await get_slow_chat_count(redis_client, org_id, location_id)
    bot_short_mode = slow_chats_count > SHORT_MODE_THRESHOLD
    for r in rows:
        phone = r.phone
        if not phone:
            continue
        chat_slow = await is_chat_slow(redis_client, org_id, phone, location_id)
        live = chat_live_pulse(r.last_role, r.last_at, now=now, chat_slow=chat_slow)
        triage = _chat_triage_from_meta(r.user_meta)
        snoozed_until = _parse_dt(triage.get("snoozed_until"))
        is_snoozed = snoozed_until is not None and snoozed_until > now
        is_closed = triage.get("state") == "closed"
        assignee = str(triage.get("assignee") or "")
        if mode == "active" and (is_closed or is_snoozed):
            continue
        if mode == "mine" and assignee != staff_key:
            continue
        if mode == "closed" and not is_closed:
            continue
        if mode == "snoozed" and not is_snoozed:
            continue
        chats.append(
            {
                "phone": phone,
                "lastMessage": (r.content or "")[:80],
                "lastAt": r.last_at.isoformat() if r.last_at else None,
                "state": "chatting",
                "unread": False,
                "userName": r.user_name,
                "triage": triage,
                "triageState": triage.get("state") or "active",
                "assignee": assignee,
                "snoozedUntil": triage.get("snoozed_until"),
                "bot_short_mode": bot_short_mode,
                "slow_chats": slow_chats_count,
                "last_role": live["last_role"],
                "wait_seconds": live["wait_seconds"],
                "pulse": live["pulse"],
                "sla_status": live["pulse"],
                "chat_slow": chat_slow,
            }
        )
    chats.sort(
        key=lambda c: (
            {"red": 0, "amber": 1, "green": 2}.get(str(c.get("pulse") or "green"), 2),
            -(int(c.get("wait_seconds") or 0)),
        ),
    )
    if chats:
        last = rows[-1]
        if last.last_at is not None:
            next_cursor = {"cursor_at": last.last_at.isoformat(), "cursor_id": int(last.log_id)}
        else:
            next_cursor = {"cursor_at": None, "cursor_id": int(last.log_id)}

    return {
        "chats": chats,
        "has_more": has_more,
        "next_cursor": next_cursor,
        "bot_short_mode": bot_short_mode,
        "slow_chats": slow_chats_count,
        "location_id": location_id,
    }


@chats_router.get("/chats/{phone}")
async def get_chat_log(
    request: Request,
    phone: str,
    limit: int = Query(50, ge=1, le=200),
    before_id: int | None = Query(None, ge=1, description="Cursor: load older messages with id < before_id"),
    location_id: int | None = Query(None, ge=1),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Просмотр истории диалога с клиентом по номеру телефона."""
    org_id = admin_org_from_session(request)
    result = await db.execute(
        select(User).where(User.phone == phone, User.organization_id == org_id),
    )
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(status_code=404, detail=f"Пользователь с номером {phone} не найден")

    staff = _session_staff_user(request)
    allowed_location_ids = await allowed_location_ids_for_staff(
        db,
        org_id=org_id,
        staff=staff,
        is_superadmin=_session_is_superadmin(request),
        is_demo=False,
    )
    if location_id is not None and allowed_location_ids is not None and int(location_id) not in allowed_location_ids:
        raise HTTPException(status_code=403, detail="location_forbidden")

    stmt = select(ChatLog).where(
        ChatLog.user_id == user.id,
        chat_logs_location_filter(allowed_location_ids, location_id),
    )
    if before_id is not None:
        stmt = stmt.where(ChatLog.id < int(before_id))
    # Cursor-based: stable by primary key.
    stmt = stmt.order_by(ChatLog.id.desc()).limit(limit + 1)
    logs_result = await db.execute(stmt)
    logs = logs_result.scalars().all()
    has_more = len(logs) > limit
    logs = logs[:limit]
    next_before_id = int(logs[-1].id) if (has_more and logs) else None

    return {
        "phone": phone,
        "user_name": user.name,
        "count": len(logs),
        "has_more": has_more,
        "next_before_id": next_before_id,
        "location_id": location_id,
        "messages": [
            {
                "id": log.id,
                "role": log.role,
                "content": log.content,
                "created_at": log.created_at.isoformat() if log.created_at else None,
                "location_id": log.location_id,
                "meta": log.meta_json if isinstance(log.meta_json, dict) else None,
                "provider_message_id": log.provider_message_id,
                "delivery_status": log.delivery_status,
                "error_details": log.error_details if isinstance(log.error_details, dict) else None,
                "status_updated_at": log.status_updated_at.isoformat() if log.status_updated_at else None,
            }
            for log in reversed(list(logs))
        ],
    }


# ─── Human Override (Перехват диалога) ───────────────────


@chats_router.post("/chats/{phone}/takeover")
async def takeover_chat(request: Request, phone: str, db: AsyncSession = Depends(get_db)) -> dict:
    """
    Перехватить диалог — AI замолкает, оператор ведёт общение вручную.
    Устанавливает флаг HUMAN_MODE в Redis.
    """
    org_id = admin_org_from_session(request)
    await set_user_state_durable(
        redis_client,
        phone=phone,
        organization_id=org_id,
        new_state=UserState.HUMAN_MODE,
        source="admin.chats",
        reason="operator_takeover",
    )
    from app.services.trace_context import publish_state_event
    await publish_state_event(phone=phone, state=UserState.HUMAN_MODE.value, organization_id=org_id)
    try:
        # emit_event внутри try: если _user_for_chat не найдёт пользователя (404),
        # оба изменения откатятся вместе — событие не повиснет без triage update.
        await emit_event(
            db,
            BusinessEvent(
                org_id=org_id,
                type="operator.took_over",
                actor="operator",
                entity_type="user",
                entity_id=phone,
                payload={"phone": phone, "source": "admin.chats"},
            ),
        )
        await _save_chat_triage(
            request,
            db,
            phone,
            {"state": "active", "assignee": admin_actor_key(request), "snoozed_until": None, "snooze_reason": ""},
        )
    except HTTPException:
        pass
    logger.info("Оператор перехватил диалог: %s", phone)
    return {"status": "ok", "phone": phone, "mode": "human"}


@chats_router.post("/chats/{phone}/release")
async def release_chat(request: Request, phone: str) -> dict:
    """
    Вернуть управление боту — AI снова отвечает на сообщения.
    Возвращает состояние в CHATTING.
    """
    org_id = admin_org_from_session(request)
    await set_user_state_durable(
        redis_client,
        phone=phone,
        organization_id=org_id,
        new_state=UserState.CHATTING,
        source="admin.chats",
        reason="operator_release",
    )
    from app.services.trace_context import publish_state_event
    await publish_state_event(phone=phone, state=UserState.CHATTING.value, organization_id=org_id)
    logger.info("Оператор вернул бота: %s", phone)
    return {"status": "ok", "phone": phone, "mode": "bot"}


class ChatAiSnoozeBody(BaseModel):
    """Временная или постоянная пауза ответов ИИ для номера (БД + при таймере — Redis CHATTING)."""

    preset: Literal["30m", "2h", "until_tomorrow", "forever", "off"]


@chats_router.post("/chats/{phone}/ai-snooze")
async def set_chat_ai_snooze(
    request: Request,
    phone: str,
    body: ChatAiSnoozeBody,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Отключить ИИ на время (или навсегда через ai_paused).
    Не путать с POST /chats/{phone}/snooze — там triage очереди оператора.
    """
    org_id = admin_org_from_session(request)
    user = await db.scalar(select(User).where(User.phone == phone, User.organization_id == org_id))
    if user is None:
        raise HTTPException(
            status_code=404,
            detail="Пользователь не найден для этого номера в филиале",
        )
    org = await db.get(Organization, org_id)
    tz = getattr(org, "timezone", None) if org else None
    from app.services.ai_snooze import snooze_until_for_preset

    preset = body.preset
    if preset == "off":
        user.ai_snoozed_until = None
        await db.commit()
        return {
            "ok": True,
            "ai_paused": bool(user.ai_paused),
            "ai_snoozed_until": None,
        }
    if preset == "forever":
        user.ai_snoozed_until = None
        user.ai_paused = True
        await db.commit()
        await set_user_state_durable(
            redis_client,
            phone=phone,
            organization_id=org_id,
            new_state=UserState.HUMAN_MODE,
            source="admin.chats",
            reason="ai_pause_forever",
        )
        from app.services.trace_context import publish_state_event
        await publish_state_event(phone=phone, state=UserState.HUMAN_MODE.value, organization_id=org_id)
        return {"ok": True, "ai_paused": True, "ai_snoozed_until": None}

    user.ai_paused = False
    until = snooze_until_for_preset(preset, tz)  # type: ignore[arg-type]
    user.ai_snoozed_until = until
    await db.commit()
    await set_user_state_durable(
        redis_client,
        phone=phone,
        organization_id=org_id,
        new_state=UserState.CHATTING,
        source="admin.chats",
        reason="ai_pause_temporary",
    )
    from app.services.trace_context import publish_state_event
    await publish_state_event(phone=phone, state=UserState.CHATTING.value, organization_id=org_id)
    return {
        "ok": True,
        "ai_paused": False,
        "ai_snoozed_until": until.isoformat() if until else None,
    }


class ChatSnoozeBody(BaseModel):
    minutes: int = Field(30, ge=1, le=60 * 24 * 14)
    reason: str = Field("", max_length=240)


@chats_router.post("/chats/{phone}/assign-me")
async def assign_chat_to_me(request: Request, phone: str, db: AsyncSession = Depends(get_db)) -> dict:
    return await _save_chat_triage(
        request,
        db,
        phone,
        {
            "state": "active",
            "assignee": admin_actor_key(request),
            "snoozed_until": None,
            "snooze_reason": "",
        },
    )


@chats_router.post("/chats/{phone}/snooze")
async def snooze_chat(
    request: Request,
    phone: str,
    body: ChatSnoozeBody,
    db: AsyncSession = Depends(get_db),
) -> dict:
    until = datetime.now(timezone.utc) + timedelta(minutes=int(body.minutes))
    return await _save_chat_triage(
        request,
        db,
        phone,
        {
            "state": "active",
            "assignee": admin_actor_key(request),
            "snoozed_until": until.isoformat(),
            "snooze_reason": (body.reason or "").strip(),
        },
    )


@chats_router.post("/chats/{phone}/close")
async def close_chat(request: Request, phone: str, db: AsyncSession = Depends(get_db)) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return await _save_chat_triage(
        request,
        db,
        phone,
        {
            "state": "closed",
            "assignee": admin_actor_key(request),
            "snoozed_until": None,
            "snooze_reason": "",
            "closed_at": now,
            "closed_by": admin_actor_key(request),
        },
    )


@chats_router.post("/chats/{phone}/reopen")
async def reopen_chat(request: Request, phone: str, db: AsyncSession = Depends(get_db)) -> dict:
    return await _save_chat_triage(
        request,
        db,
        phone,
        {
            "state": "active",
            "assignee": admin_actor_key(request),
            "snoozed_until": None,
            "snooze_reason": "",
            "closed_at": None,
            "closed_by": "",
        },
    )


@chats_router.post("/chats/{phone}/send_message")
async def admin_send_message(
    request: Request,
    phone: str,
    body: TextRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Отправить сообщение клиенту от имени оператора.
    Сохраняется в ChatLog и отправляется через WhatsApp.
    """
    org_id = admin_org_from_session(request)
    user = await get_or_create_user(db, phone, org_id)
    now = datetime.now(timezone.utc)
    from app.services.trace_context import (
        build_trace_id,
        publish_chat_event,
        trace_context,
        trace_log_prefix,
        trace_payload,
    )
    from app.services.trace_timeline import latest_trace_for_phone

    existing_trace, conversation_id = await latest_trace_for_phone(
        db,
        org_id=org_id,
        phone=phone,
    )
    trace_id = existing_trace or build_trace_id(f"operator:{org_id}:{phone}:{int(now.timestamp())}")
    op_log = ChatLog(
        user_id=user.id,
        organization_id=org_id,
        role="operator",
        content=body.text,
        delivery_status="sending",
        status_updated_at=now,
        meta_json=trace_payload(trace_id=trace_id, conversation_id=conversation_id),
    )
    db.add(op_log)
    await db.commit()
    await db.refresh(op_log)
    log_id = int(op_log.id)

    with trace_context(trace_id, conversation_id):
        await publish_chat_event(
            phone=phone,
            role="operator",
            content=body.text,
            organization_id=org_id,
            chat_log_id=log_id,
            trace_id=trace_id,
            conversation_id=conversation_id,
        )
        wa = await send_message(phone, body.text)

    # finalize после внешнего I/O, но в той же сессии (важно для тестов/фикстур)
    evt = await finalize_outbound_delivery(
        db, log_id, wa.ok, provider_message_id=wa.message_id, error_details=wa.error,
    )
    await db.commit()
    if evt is not None:
        await publish_event("message_status_updated", evt)

    if wa.ok:
        from app.services.message_accounting import schedule_log_message
        schedule_log_message(org_id, "outbound", "operator", "text")

    logger.info(
        "%sОператор отправил сообщение в %s: %s",
        trace_log_prefix(),
        phone,
        body.text[:50],
    )
    return {
        "status": "sent" if wa.ok else "failed",
        "phone": phone,
        "chat_log_id": log_id,
        "trace_id": trace_id,
    }


@chats_router.post("/chats/{phone}/messages/{chat_log_id}/resend")
async def resend_failed_chat_message(
    request: Request,
    phone: str,
    chat_log_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Повторная отправка текста той же записи ChatLog после статуса failed (WhatsApp)."""
    org_id = admin_org_from_session(request)
    log = await db.get(ChatLog, chat_log_id)
    if log is None:
        raise HTTPException(status_code=404, detail="Сообщение не найдено")
    user = await db.get(User, log.user_id)
    if user is None or int(user.organization_id) != org_id or user.phone != phone:
        raise HTTPException(status_code=404, detail="Сообщение не найдено")
    if (log.delivery_status or "").lower() != "failed":
        raise HTTPException(status_code=400, detail="Переотправка доступна только для failed")
    if (log.role or "") not in ("operator", "assistant", "system"):
        raise HTTPException(status_code=400, detail="Неподдерживаемая роль для переотправки")
    text = (log.content or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Пустой текст сообщения")

    now = datetime.now(timezone.utc)
    log.delivery_status = "sending"
    log.error_details = None
    log.status_updated_at = now
    await db.commit()
    from app.services.trace_context import publish_chat_event
    await publish_chat_event(
        phone=phone, role=log.role, content=text,
        organization_id=org_id, chat_log_id=chat_log_id,
    )
    wa = await send_message(phone, text)
    evt = await finalize_outbound_delivery(
        db, chat_log_id, wa.ok, provider_message_id=wa.message_id, error_details=wa.error,
    )
    await db.commit()
    if evt is not None:
        await publish_event("message_status_updated", evt)
    return {"status": "sent" if wa.ok else "failed", "phone": phone, "chat_log_id": chat_log_id}


@chats_router.get("/chats/{phone}/state")
async def get_chat_state(
    request: Request,
    phone: str,
    location_id: int | None = Query(None, ge=1),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Получить текущее состояние диалога (CHATTING, CONFIRMING_ORDER, HUMAN_MODE)."""
    org_id = admin_org_from_session(request)
    staff = await _session_staff_user(request, db)
    is_super = await _session_is_superadmin(request, db)
    allowed_location_ids = await allowed_location_ids_for_staff(
        db,
        staff=staff,
        org_id=org_id,
        is_superadmin=is_super,
    )
    if location_id is not None and allowed_location_ids is not None and int(location_id) not in allowed_location_ids:
        raise HTTPException(status_code=403, detail="Location is not allowed")
    state = await get_user_state(redis_client, phone, organization_id=org_id)
    slow_chats_count = await get_slow_chat_count(redis_client, org_id, location_id)
    bot_short_mode = slow_chats_count > SHORT_MODE_THRESHOLD
    chat_slow = await is_chat_slow(redis_client, org_id, phone, location_id)
    ai_snoozed_until: str | None = None
    u = await db.scalar(select(User).where(User.phone == phone, User.organization_id == org_id))
    if u is not None:
        from app.services.ai_snooze import ai_snooze_is_active, clear_ai_snooze_if_expired

        await clear_ai_snooze_if_expired(db, u)
        await db.commit()
        if ai_snooze_is_active(u) and u.ai_snoozed_until is not None:
            ai_snoozed_until = u.ai_snoozed_until.isoformat()
    return {
        "phone": phone,
        "state": state.value,
        "ai_snoozed_until": ai_snoozed_until,
        "bot_short_mode": bot_short_mode,
        "slow_chats": slow_chats_count,
        "location_id": location_id,
        "sla_status": "red" if chat_slow else ("amber" if bot_short_mode else "green"),
        "chat_slow": chat_slow,
    }
