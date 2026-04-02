"""
Админ-панель API.
REST-эндпоинты для просмотра заказов, диалогов, аналитики и синхронизации меню.
"""

import asyncio
import csv
import io
import json
import logging
import secrets
import uuid
from collections import defaultdict
from datetime import date, datetime, time as dt_time, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect
from sqlalchemy import delete as sql_delete
from sqlalchemy import exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from pydantic import BaseModel, Field

from app.core.config import settings
from app.db.models import (
    Booking,
    ChatLog,
    EscalationEvent,
    FailedTask,
    IntegrationEvent,
    IntegrationHealth,
    KnowledgeItem,
    MenuItem,
    Order,
    OrderStatus,
    PackagingRule,
    PaymentEvent,
    User,
)
from app.db.session import async_session_factory, get_db, redis_client
from app.integrations.whatsapp import send_message
from app.services.admin_tokens import create_admin_ws_token, parse_admin_ws_token
from app.services.ai_brain import call_gemini
from app.services.demo_data import clear_demo_data, demo_data_exists, seed_demo_data
from app.services.integration_health import (
    build_status_payload,
    list_integration_events,
    record_menu_sync,
    record_stoplist_sync,
)
from app.services.chat_log_retention import count_chat_logs_eligible_for_purge, purge_old_chat_logs
from app.services.dialog_mgr import (
    UserState,
    append_to_history,
    clear_pending_order,
    get_chat_history,
    get_pending_order,
    get_user_state,
    purge_all_session_keys_for_phone,
    set_pending_booking,
    set_pending_order,
    set_user_state,
)
from app.services.events import publish_event, subscribe_events
from app.services.booking_halls import BOOKING_HALL_KEYS, BOOKING_HALL_VIP, vip_slot_occupied
from app.services.intent_router import (
    confirm_order,
    get_open_draft_order,
    get_or_create_user,
    route_intent,
)
from app.services.menu_sync import sync_menu_from_iiko, sync_stop_lists
from app.services.knowledge_context import load_knowledge_context_block
from app.schemas.ai_schemas import AIBrainResponse, BookingDetails, OrderItem, PaymentSplit
from app.services.order_logic import (
    build_menu_context,
    classify_packaging_kind,
    finalize_order_draft,
    format_draft_order_context_for_prompt,
    load_available_menu,
    load_packaging_rules,
    validate_mixed_payment_total,
    validate_order,
)

logger = logging.getLogger(__name__)


def _credentials_ok(username: str, password: str) -> bool:
    u_ok = secrets.compare_digest(username, settings.admin_username)
    p_ok = secrets.compare_digest(password, settings.admin_password)
    return u_ok and p_ok


def require_admin_session(request: Request) -> None:
    """Доступ только после успешного входа (cookie-сессия)."""
    if not request.session.get("admin_ok"):
        raise HTTPException(status_code=401, detail="Требуется вход в админку")


# ─── Публичные эндпоинты входа (без сессии) ──────────────

auth_router = APIRouter(prefix="/admin/auth", tags=["Admin Auth"])


class LoginBody(BaseModel):
    """Данные формы входа в админку."""

    username: str = ""
    password: str = ""


@auth_router.post("/login")
async def admin_login(request: Request, body: LoginBody) -> dict:
    """Установить сессию администратора и выдать токен для WebSocket."""
    if not _credentials_ok(body.username.strip(), body.password):
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")
    request.session.clear()
    request.session["admin_ok"] = True
    request.session["admin_user"] = body.username.strip()
    ws_token = create_admin_ws_token(body.username.strip())
    return {"ok": True, "username": body.username.strip(), "ws_token": ws_token}


@auth_router.post("/logout")
async def admin_logout(request: Request) -> dict:
    """Завершить сессию."""
    request.session.clear()
    return {"ok": True}


@auth_router.get("/me")
async def admin_me(request: Request) -> dict:
    """Проверка сессии и перевыпуск ws_token для переподключения."""
    if not request.session.get("admin_ok"):
        raise HTTPException(status_code=401, detail="Не авторизован")
    user = request.session.get("admin_user") or settings.admin_username
    return {
        "authenticated": True,
        "username": user,
        "ws_token": create_admin_ws_token(str(user)),
    }


# ─── Защищённый REST API ─────────────────────────────────

router = APIRouter(
    prefix="/admin",
    tags=["Admin Panel"],
    dependencies=[Depends(require_admin_session)],
)


# WebSocket без cookie-сессии (браузер ограничен) — только подписанный токен
ws_router = APIRouter(prefix="/admin", tags=["Admin Panel"])


def _make_naive(dt: datetime | None) -> datetime | None:
    """Убираем tzinfo для корректного сравнения с naive-датами."""
    if dt is None:
        return None
    return dt.replace(tzinfo=None) if dt.tzinfo else dt


# ─── WebSocket (Live-события для админки) ────────────────

@ws_router.websocket("/ws")
async def admin_websocket(ws: WebSocket, token: str = "") -> None:
    """
    WebSocket для real-time уведомлений в админке.
    Авторизация: query ?token= — подписанный токен из POST /auth/login или GET /auth/me.
    """
    username = parse_admin_ws_token(token)
    if not username or not secrets.compare_digest(username, settings.admin_username):
        await ws.close(code=4003, reason="Unauthorized")
        return
    await ws.accept()
    logger.info("Admin WebSocket подключён")
    # Сигнал клиенту: сокет принят, дальше — цикл подписки (Redis / in-memory).
    try:
        await ws.send_text(json.dumps({"type": "ws_ready", "v": 1}, ensure_ascii=False))
    except Exception:
        return
    try:
        async for event_json in subscribe_events():
            await ws.send_text(event_json)
    except WebSocketDisconnect:
        logger.info("Admin WebSocket отключён")
    except Exception as exc:
        logger.error("WebSocket error: %s", exc)


# ─── Заказы ─────────────────────────────────────────────

def _order_items_count(items_json: dict | None) -> int:
    if not items_json:
        return 0
    items = items_json.get("items")
    return len(items) if isinstance(items, list) else 0


def _order_meta_from_items_json(items_json: dict | None) -> dict:
    """Метаданные v2 (тип заказа, оплата, адрес) из items_json."""
    if not isinstance(items_json, dict):
        return {}
    raw = items_json.get("order_meta")
    return raw if isinstance(raw, dict) else {}


def _check_mixed_payment_split(items_json: dict | None, total_price: float, *, tol: float = 1.0) -> str | None:
    """Проверяет, совпадает ли сумма частей смешанной оплаты с итогом. None = ОК."""
    meta = _order_meta_from_items_json(items_json)
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


def _ai_brain_from_order_meta(meta: dict) -> AIBrainResponse:
    """Восстанавливает AIBrainResponse из сохранённого order_meta (пересборка черновика в админке)."""
    ot = str(meta.get("order_type") or "delivery")
    if ot not in ("delivery", "pickup", "hall"):
        ot = "delivery"
    pm_mode = str(meta.get("payment_mode") or "single")
    if pm_mode not in ("single", "mixed"):
        pm_mode = "single"
    pay_m = str(meta.get("payment_method") or "cash")
    if pay_m not in ("cash", "card", "remote", ""):
        pay_m = "cash"
    ps = PaymentSplit()
    if pm_mode == "mixed":
        pd = meta.get("payment_details") if isinstance(meta.get("payment_details"), dict) else {}
        sp = pd.get("split") if isinstance(pd.get("split"), dict) else {}
        ps = PaymentSplit(
            cash=float(sp.get("cash") or 0),
            card=float(sp.get("card") or 0),
            remote=float(sp.get("remote") or 0),
        )
    bd: BookingDetails | None = None
    snap = meta.get("booking_snapshot")
    if isinstance(snap, dict) and snap:
        hall = snap.get("hall")
        hall_ok = hall if hall in ("hall_1", "hall_2", "vip") else "hall_1"
        bd = BookingDetails(
            date=str(snap.get("date") or ""),
            time=str(snap.get("time") or ""),
            guests=int(snap.get("guests") or 2),
            hall=hall_ok,
            comment="",
        )
    return AIBrainResponse(
        intent="order",
        reply_text="",
        items=[],
        order_type=ot,
        payment_method=pay_m,
        payment_mode=pm_mode,
        payment_split=ps,
        is_preorder=bool(meta.get("is_preorder")),
        booking_time=meta.get("booking_time"),
        delivery_address=str(meta.get("delivery_address") or ""),
        pickup_time_note=str(meta.get("pickup_time_note") or ""),
        booking_details=bd,
    )


def _merge_preserved_order_meta_keys(
    new_meta: dict[str, object],
    preserved: dict[str, object] | None,
) -> dict[str, object]:
    """Сохраняет recommendation / trace и прочие не-логистические ключи после пересборки."""
    if not preserved:
        return new_meta
    for key in ("recommendation", "recommendation_trace"):
        if key in preserved and preserved[key] is not None:
            new_meta[key] = preserved[key]
    return new_meta


def _booking_public(b: Booking | None) -> dict | None:
    """Краткие данные брони для админки (предзаказ в зале)."""
    if b is None:
        return None
    return {
        "id": b.id,
        "date": b.booking_date.isoformat(),
        "time": b.booking_time.isoformat(),
        "guests": b.guests,
        "hall": b.hall,
        "status": b.status,
        "comment": b.comment or "",
    }


@router.get("/orders")
async def list_orders(
    status: str | None = Query(None, description="Фильтр по статусу (draft, confirmed, ...)"),
    q: str | None = Query(None, description="Поиск по № заказа, телефону или имени клиента"),
    sum_min: float | None = Query(None, ge=0, description="Мин. сумма заказа"),
    sum_max: float | None = Query(None, ge=0, description="Макс. сумма заказа"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Список заказов с пагинацией; телефон/имя — из связанного пользователя (WhatsApp)."""
    query = (
        select(Order, User.phone, User.name)
        .join(User, Order.user_id == User.id)
        .options(joinedload(Order.booking))
    )
    if status:
        query = query.where(Order.status == status)
    if sum_min is not None:
        query = query.where(Order.total_price >= sum_min)
    if sum_max is not None:
        query = query.where(Order.total_price <= sum_max)
    if sum_min is not None and sum_max is not None and sum_min > sum_max:
        raise HTTPException(status_code=400, detail="sum_min не может быть больше sum_max")

    if q and q.strip():
        raw = q.strip()
        term = f"%{raw}%"
        clauses = [
            User.phone.ilike(term),
            func.coalesce(User.name, "").ilike(term),
        ]
        try:
            oid = int(raw)
            if 0 < oid < 2**31:
                clauses.append(Order.id == oid)
        except ValueError:
            pass
        query = query.where(or_(*clauses))

    query = query.order_by(Order.created_at.desc()).limit(limit).offset(offset)

    result = await db.execute(query)
    rows = result.all()

    out: list[dict] = []
    for o, phone, user_name in rows:
        items_json = o.items_json
        meta = _order_meta_from_items_json(items_json if isinstance(items_json, dict) else None)
        out.append(
            {
                "id": o.id,
                "user_id": o.user_id,
                "user_phone": phone,
                "user_name": user_name,
                "status": o.status,
                "items": items_json,
                "items_count": _order_items_count(items_json if isinstance(items_json, dict) else None),
                "total_price": float(o.total_price),
                "created_at": o.created_at.isoformat() if o.created_at else None,
                "updated_at": o.updated_at.isoformat() if o.updated_at else None,
                "iiko_last_error": o.iiko_last_error,
                "order_type": meta.get("order_type"),
                "payment_method": meta.get("payment_method"),
                "delivery_address": meta.get("delivery_address") or "",
                "booking_id": o.booking_id,
                "booking": _booking_public(o.booking),
                "prepayment_status": getattr(o, "prepayment_status", None) or "not_required",
                "payment_link_url": getattr(o, "payment_link_url", None),
                "row_version": int(getattr(o, "row_version", 1) or 1),
                "payment_split_warning": _check_mixed_payment_split(
                    items_json if isinstance(items_json, dict) else None, float(o.total_price),
                ),
            }
        )

    return {
        "count": len(out),
        "orders": out,
    }


class OrderPatchBody(BaseModel):
    """Смена статуса заказа из админки (канбан, ручное подтверждение)."""

    status: str = Field(..., description="draft | confirmed | sent_to_iiko | …")


class AdminFoodLineIn(BaseModel):
    """Позиции еды для пересборки черновика (админка)."""

    name: str = Field(min_length=1, max_length=240)
    quantity: int = Field(ge=1, le=99)
    iiko_item_id: str = ""
    packaging_plov_1kg: str = ""
    exclude_ingredients: list[str] = Field(default_factory=list)


class OrderRebuildDraftBody(BaseModel):
    food_lines: list[AdminFoodLineIn] = Field(min_length=1)
    expected_version: int | None = Field(
        default=None,
        description="Ожидаемая version строки заказа; при рассинхроне — 409.",
    )


class FailedTaskPatchBody(BaseModel):
    resolved: bool = False


PREPAYMENT_STATUS_KEYS = frozenset({"not_required", "pending", "paid", "waived"})


class OrderPaymentPatchBody(BaseModel):
    """Ручное обновление предоплаты (Kaspi-ссылка и т.д.) до подключения платёжного API."""

    prepayment_status: str | None = Field(default=None, description="not_required | pending | paid | waived")
    payment_link_url: str | None = Field(default=None, description="URL для оплаты или пустая строка чтобы сбросить")


@router.patch("/orders/{order_id}/payment")
async def patch_order_payment_meta(
    order_id: int,
    body: OrderPaymentPatchBody,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Проставить ссылку на оплату и статус предоплаты (оператор или будущий webhook)."""
    if body.prepayment_status is None and body.payment_link_url is None:
        raise HTTPException(status_code=400, detail="Укажите prepayment_status и/или payment_link_url")

    res = await db.execute(select(Order).where(Order.id == order_id))
    order = res.scalar_one_or_none()
    if order is None:
        raise HTTPException(status_code=404, detail="Заказ не найден")

    old_status = (order.prepayment_status or "").strip().lower()

    if body.prepayment_status is not None:
        st = (body.prepayment_status or "").strip().lower()
        if st not in PREPAYMENT_STATUS_KEYS:
            raise HTTPException(
                status_code=400,
                detail=f"Недопустимый prepayment_status (ожидается одно из: {', '.join(sorted(PREPAYMENT_STATUS_KEYS))})",
            )
        order.prepayment_status = st

        if st != old_status:
            event_type = {
                "paid": "prepayment_confirmed",
                "waived": "prepayment_waived",
                "pending": "manual_reset",
                "not_required": "manual_reset",
            }.get(st, "manual_reset")
            db.add(PaymentEvent(
                order_id=order.id,
                event_type=event_type,
                actor="admin",
                amount=float(order.total_price) if st == "paid" else None,
                note=f"{old_status} → {st}",
            ))

    if body.payment_link_url is not None:
        url = (body.payment_link_url or "").strip()
        order.payment_link_url = url if url else None

    await db.commit()
    return {
        "ok": True,
        "id": order.id,
        "prepayment_status": order.prepayment_status,
        "payment_link_url": order.payment_link_url,
    }


@router.patch("/orders/{order_id}")
async def patch_order_status(
    order_id: int,
    body: OrderPatchBody,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Обновление статуса (DnD канбан): draft→confirmed, confirmed→sent_to_iiko,
    confirmed→draft (откат ошибки оператора).
    Отправка в iiko только при переходе confirmed→sent_to_iiko (не из чата WhatsApp автоматически).
    """
    from app.api.webhooks import _send_order_to_iiko

    res = await db.execute(
        select(Order, User.phone).join(User, Order.user_id == User.id).where(Order.id == order_id),
    )
    row = res.one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Заказ не найден")
    order, phone = row

    want = body.status.strip().lower()
    cur = (order.status or "").lower()

    async def _emit(upd: Order, **extra) -> dict:
        await publish_event(
            "order_updated",
            {
                "order_id": upd.id,
                "status": upd.status,
                "phone": phone,
                "total_price": float(upd.total_price),
                "iiko_last_error": upd.iiko_last_error,
                **extra,
            },
        )
        return {
            "ok": True,
            "id": upd.id,
            "status": upd.status,
            "iiko_last_error": upd.iiko_last_error,
        }

    # Подтверждён → черновик (оператор вернул заказ на доработку)
    if cur == OrderStatus.CONFIRMED.value and want == OrderStatus.DRAFT.value:
        order.status = OrderStatus.DRAFT.value
        order.iiko_last_error = None
        await db.commit()
        return await _emit(order)

    if cur == OrderStatus.DRAFT.value and want == OrderStatus.CONFIRMED.value:
        split_warn = _check_mixed_payment_split(order.items_json, float(order.total_price))
        if split_warn:
            order.prepayment_status = "pending"
            await db.commit()
            raise HTTPException(status_code=409, detail=split_warn)
        o2 = await confirm_order(db, order_id)
        if not o2:
            raise HTTPException(status_code=400, detail="Нельзя подтвердить заказ")
        await db.commit()
        return await _emit(o2)

    # Подтверждён, но iiko не принял — повторная отправка на кухню
    if cur == OrderStatus.CONFIRMED.value and want == OrderStatus.SENT_TO_IIKO.value:
        sent_to_iiko, iiko_err = await _send_order_to_iiko(
            order_id=order.id,
            phone=phone,
            items_json=order.items_json,
        )
        if sent_to_iiko:
            order.status = OrderStatus.SENT_TO_IIKO.value
            order.iiko_last_error = None
            await db.commit()
            return await _emit(order)
        if iiko_err:
            order.iiko_last_error = iiko_err
            await db.commit()
            return await _emit(order)
        raise HTTPException(status_code=502, detail="Не удалось отправить в iiko")

    raise HTTPException(
        status_code=400,
        detail=f"Переход {cur!r} → {want!r} не поддерживается",
    )


def _failed_task_public(t: FailedTask) -> dict:
    return {
        "id": t.id,
        "phone": t.phone,
        "message_text": t.message_text or "",
        "error": t.error or "",
        "attempts": int(t.attempts or 0),
        "resolved": bool(t.resolved),
        "created_at": t.created_at.isoformat() if t.created_at else None,
    }


@router.get("/failed-tasks")
async def list_failed_tasks(
    resolved: str | None = Query(None, description="true | false | all"),
    phone: str | None = Query(None, max_length=24),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Очередь ошибок обработки сообщений (retry исчерпан)."""
    q = select(FailedTask)
    cnt_q = select(func.count(FailedTask.id))
    if resolved == "true":
        q = q.where(FailedTask.resolved.is_(True))
        cnt_q = cnt_q.where(FailedTask.resolved.is_(True))
    elif resolved == "false":
        q = q.where(FailedTask.resolved.is_(False))
        cnt_q = cnt_q.where(FailedTask.resolved.is_(False))
    if phone and phone.strip():
        term = f"%{phone.strip()}%"
        q = q.where(FailedTask.phone.ilike(term))
        cnt_q = cnt_q.where(FailedTask.phone.ilike(term))
    q = q.order_by(FailedTask.created_at.desc()).limit(limit).offset(offset)
    rows = (await db.execute(q)).scalars().all()
    total = int((await db.execute(cnt_q)).scalar() or 0)
    return {"count": len(rows), "total": total, "tasks": [_failed_task_public(x) for x in rows]}


@router.patch("/failed-tasks/{task_id}")
async def patch_failed_task(
    task_id: int,
    body: FailedTaskPatchBody,
    db: AsyncSession = Depends(get_db),
) -> dict:
    res = await db.execute(select(FailedTask).where(FailedTask.id == task_id))
    t = res.scalar_one_or_none()
    if t is None:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    t.resolved = body.resolved
    await db.commit()
    await db.refresh(t)
    return {"ok": True, "task": _failed_task_public(t)}


@router.post("/orders/{order_id}/rebuild-draft")
async def rebuild_order_draft(
    order_id: int,
    body: OrderRebuildDraftBody,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Пересборка позиций черновика: validate_order → finalize_order_draft.
    Сохраняет тип доставки/оплату из order_meta; recommendation не затирается.
    """
    res = await db.execute(select(Order).where(Order.id == order_id))
    order = res.scalar_one_or_none()
    if order is None:
        raise HTTPException(status_code=404, detail="Заказ не найден")
    if (order.status or "").lower() != OrderStatus.DRAFT.value:
        raise HTTPException(status_code=400, detail="Пересборка доступна только для статуса draft")
    if body.expected_version is not None and int(order.row_version) != int(body.expected_version):
        raise HTTPException(
            status_code=409,
            detail="Заказ изменился в другом окне. Обновите список и повторите правку.",
        )
    old_ij = order.items_json if isinstance(order.items_json, dict) else {}
    old_meta = _order_meta_from_items_json(old_ij)
    preserved_rec: dict[str, object] = {
        k: old_meta[k]
        for k in ("recommendation", "recommendation_trace")
        if k in old_meta and old_meta[k] is not None
    }
    items_in: list[OrderItem] = []
    for line in body.food_lines:
        pkg_raw = (line.packaging_plov_1kg or "").strip()
        if pkg_raw not in ("", "tabak", "foil_kazan"):
            raise HTTPException(
                status_code=400,
                detail=f"Недопустимое packaging_plov_1kg для «{line.name}» (ожидается tabak, foil_kazan или пусто).",
            )
        items_in.append(
            OrderItem(
                name=line.name.strip(),
                quantity=line.quantity,
                iiko_item_id=(line.iiko_item_id or "").strip(),
                packaging_plov_1kg=pkg_raw,  # type: ignore[arg-type]
                exclude_ingredients=list(line.exclude_ingredients or []),
            ),
        )
    validated = await validate_order(items_in, db=db)
    if validated.unknown_items:
        raise HTTPException(
            status_code=400,
            detail=f"Не найдено в меню: {', '.join(validated.unknown_items)}",
        )
    for vi in validated.valid_items:
        pk = classify_packaging_kind(str(vi.get("name", "")), str(vi.get("category", "")))
        if pk == "plov_1kg":
            ch = (vi.get("packaging_plov_1kg") or "").strip()
            if ch not in ("tabak", "foil_kazan"):
                raise HTTPException(
                    status_code=400,
                    detail="Для плова 1 кг укажите упаковку: tabak или foil_kazan в поле packaging_plov_1kg.",
                )
    ai = _ai_brain_from_order_meta(old_meta)
    rules = await load_packaging_rules(db)
    merged, grand_total = finalize_order_draft(validated, ai, packaging_rules=rules)
    mix_err = validate_mixed_payment_total(ai, grand_total)
    if mix_err:
        raise HTTPException(status_code=400, detail=mix_err)
    om = merged.get("order_meta")
    if isinstance(om, dict):
        merged["order_meta"] = _merge_preserved_order_meta_keys(om, preserved_rec)
    order.items_json = merged
    order.total_price = grand_total
    order.row_version = int(order.row_version) + 1
    await db.commit()
    await db.refresh(order)
    split_warn = _check_mixed_payment_split(merged, float(grand_total))
    return {
        "ok": True,
        "id": order.id,
        "total_price": float(order.total_price),
        "row_version": int(order.row_version),
        "items_json": merged,
        "payment_split_warning": split_warn,
    }


@router.get("/search")
async def global_search(
    q: str = Query(..., min_length=1, max_length=80),
    limit: int = Query(12, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Поиск по телефону/имени: заказы, чаты, бронирования (для Ctrl+K)."""
    raw = q.strip()
    term = f"%{raw}%"
    out_orders: list[dict] = []
    o_clauses = [
        User.phone.ilike(term),
        func.coalesce(User.name, "").ilike(term),
    ]
    try:
        oid = int(raw)
        if 0 < oid < 2**31:
            o_clauses.append(Order.id == oid)
    except ValueError:
        pass
    oq = (
        select(Order, User.phone, User.name)
        .join(User, Order.user_id == User.id)
        .where(or_(*o_clauses))
        .order_by(Order.created_at.desc())
        .limit(limit)
    )

    r1 = await db.execute(oq)
    for o, p, nm in r1.all():
        meta = _order_meta_from_items_json(o.items_json if isinstance(o.items_json, dict) else None)
        out_orders.append(
            {
                "id": o.id,
                "status": o.status,
                "user_phone": p,
                "user_name": nm,
                "total_price": float(o.total_price),
                "created_at": o.created_at.isoformat() if o.created_at else None,
                "order_type": meta.get("order_type"),
                "payment_method": meta.get("payment_method"),
            },
        )

    chats_out: list[dict] = []
    cq = (
        select(User.phone, User.name, func.max(ChatLog.created_at))
        .join(ChatLog, ChatLog.user_id == User.id)
        .where(or_(User.phone.ilike(term), func.coalesce(User.name, "").ilike(term)))
        .group_by(User.id, User.phone, User.name)
        .order_by(func.max(ChatLog.created_at).desc())
        .limit(limit)
    )
    r2 = await db.execute(cq)
    for p, nm, last_at in r2.all():
        chats_out.append(
            {
                "phone": p,
                "user_name": nm,
                "last_at": last_at.isoformat() if last_at else None,
            },
        )

    book_out: list[dict] = []
    bq = (
        select(Booking, User.phone, User.name)
        .join(User, Booking.user_id == User.id)
        .where(User.phone.ilike(term))
        .order_by(Booking.created_at.desc())
        .limit(limit)
    )
    r3 = await db.execute(bq)
    for b, p, nm in r3.all():
        book_out.append(
            {
                "id": b.id,
                "user_phone": p,
                "user_name": nm,
                "date": b.booking_date.isoformat(),
                "time": b.booking_time.isoformat(),
                "hall": b.hall,
                "status": b.status,
            },
        )

    return {"q": raw, "orders": out_orders, "chats": chats_out, "bookings": book_out}


def _iiko_env_configured() -> bool:
    return bool(str(settings.iiko_api_login or "").strip() and str(settings.iiko_organization_id or "").strip())


def _whatsapp_env_configured() -> bool:
    return bool(
        str(settings.whatsapp_api_token or "").strip()
        and str(settings.whatsapp_phone_number_id or "").strip()
    )


@router.get("/integrations/status")
async def integrations_status(db: AsyncSession = Depends(get_db)) -> dict:
    """Состояние интеграций для админки: индикаторы iiko / WhatsApp."""
    base = await build_status_payload(
        db,
        iiko_configured=_iiko_env_configured(),
        whatsapp_configured=_whatsapp_env_configured(),
    )
    pub = (settings.public_base_url or "").strip().rstrip("/")
    webhook_url = f"{pub}/api/whatsapp/webhook" if pub else None
    base["webhook_url"] = webhook_url
    base["whatsapp_verify_token_hint"] = (
        settings.whatsapp_verify_token[:4] + "…"
        if settings.whatsapp_verify_token
        else None
    )
    base["gemini_configured"] = bool(str(settings.gemini_api_key or "").strip())
    base["whatsapp_voice_replies_enabled"] = bool(settings.whatsapp_voice_replies)
    return base


@router.get("/integrations/events")
async def integrations_events(
    limit: int = Query(40, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Журнал последних событий синхронизации (меню, стоп-листы)."""
    events = await list_integration_events(db, limit=limit)
    return {"events": events}


@router.post("/integrations/sync")
async def integrations_sync_now(db: AsyncSession = Depends(get_db)) -> dict:
    """
    Принудительно: номенклатура + стоп-листы из iiko (учётные данные из .env).
    """
    if not _iiko_env_configured():
        raise HTTPException(
            status_code=400,
            detail="Задайте IIKO_API_LOGIN и IIKO_ORGANIZATION_ID в .env",
        )

    menu_block: dict = {"ok": False, "stats": None, "error": None}
    stop_block: dict = {"ok": False, "stats": None, "error": None}

    try:
        stats_m = await sync_menu_from_iiko(
            db, settings.iiko_api_login, settings.iiko_organization_id,
        )
        menu_block = {"ok": True, "stats": stats_m, "error": None}
        sk = stats_m.get("skipped")
        detail_m = (
            f"Синхронизация меню: успешно "
            f"(всего {stats_m.get('total', 0)}, новых {stats_m.get('created', 0)}, обновлено {stats_m.get('updated', 0)}"
            + (f", пропущено {sk}" if sk else "")
            + ")"
        )
        await record_menu_sync(db, True, None, detail=detail_m)
    except Exception as exc:
        err = str(exc)
        logger.error("Ручная синхронизация меню iiko: %s", exc, exc_info=True)
        menu_block = {"ok": False, "stats": None, "error": err}
        await record_menu_sync(db, False, err, detail=f"Синхронизация меню: ошибка — {err[:400]}")

    try:
        stats_s = await sync_stop_lists(
            db, settings.iiko_api_login, settings.iiko_organization_id,
        )
        stop_block = {"ok": True, "stats": stats_s, "error": None}
        detail_s = (
            f"Стоп-листы: успешно "
            f"(в стопе: {stats_s.get('stopped', 0)}, восстановлено: {stats_s.get('restored', 0)})"
        )
        await record_stoplist_sync(db, True, None, detail=detail_s)
    except Exception as exc:
        err = str(exc)
        logger.error("Ручная синхронизация стоп-листов iiko: %s", exc, exc_info=True)
        stop_block = {"ok": False, "stats": None, "error": err}
        await record_stoplist_sync(db, False, err, detail=f"Стоп-листы: ошибка — {err[:400]}")

    if not menu_block["ok"] and not stop_block["ok"]:
        raise HTTPException(
            status_code=502,
            detail=f"Меню: {menu_block['error']}; стоп-листы: {stop_block['error']}",
        )

    snap = await build_status_payload(
        db,
        iiko_configured=True,
        whatsapp_configured=_whatsapp_env_configured(),
    )
    logger.info(
        "Ручная синхронизация iiko завершена: меню ok=%s %s; стоп-листы ok=%s %s",
        menu_block["ok"],
        menu_block.get("stats") or menu_block.get("error"),
        stop_block["ok"],
        stop_block.get("stats") or stop_block.get("error"),
    )
    return {
        "ok": True,
        "menu": menu_block,
        "stop_lists": stop_block,
        "status": snap,
    }


# ─── Бронирования ───────────────────────────────────────

@router.get("/bookings")
async def list_bookings(
    status: str | None = Query(None, description="Фильтр по статусу (pending, confirmed, cancelled)"),
    q: str | None = Query(None, description="Поиск по телефону клиента"),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Список бронирований."""
    query = (
        select(Booking, User.phone, User.name, Order.id)
        .join(User, Booking.user_id == User.id)
        .outerjoin(Order, Order.booking_id == Booking.id)
        .order_by(Booking.created_at.desc())
    )
    if status:
        query = query.where(Booking.status == status)
    if q and q.strip():
        query = query.where(User.phone.ilike(f"%{q.strip()}%"))
    query = query.limit(limit)

    result = await db.execute(query)
    rows = result.all()

    return {
        "count": len(rows),
        "bookings": [
            {
                "id": b.id,
                "user_id": b.user_id,
                "user_phone": phone,
                "user_name": name,
                "date": b.booking_date.isoformat(),
                "time": b.booking_time.isoformat(),
                "guests": b.guests,
                "hall": b.hall,
                "comment": b.comment,
                "status": b.status,
                "created_at": b.created_at.isoformat() if b.created_at else None,
                "linked_order_id": linked_oid,
            }
            for b, phone, name, linked_oid in rows
        ],
    }


BOOKING_STATUS_KEYS = frozenset({"draft", "pending", "confirmed", "cancelled"})


class BookingPatch(BaseModel):
    """Частичное обновление брони: зал и/или статус."""

    hall: str | None = Field(default=None, description="hall_1 | hall_2 | vip")
    status: str | None = Field(default=None, description="draft | pending | confirmed | cancelled")


@router.patch("/bookings/{booking_id}")
async def patch_booking(
    booking_id: int,
    body: BookingPatch,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Обновить зал и/или статус.
    VIP — не более одной активной брони на дату+время (кроме отменённых).
    """
    if body.hall is None and body.status is None:
        raise HTTPException(status_code=400, detail="Укажите поле hall и/или status")

    result = await db.execute(select(Booking).where(Booking.id == booking_id))
    booking = result.scalar_one_or_none()
    if not booking:
        raise HTTPException(status_code=404, detail="Бронирование не найдено")

    if body.hall is not None:
        h = (body.hall or "").strip()
        if h not in BOOKING_HALL_KEYS:
            raise HTTPException(status_code=400, detail="Недопустимый зал (ожидается hall_1, hall_2 или vip)")
        booking.hall = h

    if body.status is not None:
        st = (body.status or "").strip()
        if st not in BOOKING_STATUS_KEYS:
            raise HTTPException(
                status_code=400,
                detail="Недопустимый статус (draft, pending, confirmed, cancelled)",
            )
        booking.status = st

    if booking.hall == BOOKING_HALL_VIP and booking.status != "cancelled":
        if await vip_slot_occupied(
            db, booking.booking_date, booking.booking_time, booking.id,
        ):
            raise HTTPException(
                status_code=409,
                detail="VIP зал на это время уже занят — выберите другое время или другой зал",
            )

    await db.commit()
    return {
        "status": "ok",
        "id": booking_id,
        "hall": booking.hall,
        "booking_status": booking.status,
    }


# ─── Диалоги ────────────────────────────────────────────


@router.get("/chats")
async def list_chats_sidebar(
    limit: int = Query(120, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Список диалогов для боковой панели админки: телефон, превью последнего сообщения, время.
    Берём пользователей по убыванию времени последнего сообщения в chat_logs.
    """
    result = await db.execute(
        select(ChatLog, User.phone, User.name)
        .join(User, User.id == ChatLog.user_id)
        .order_by(ChatLog.created_at.desc()),
    )
    rows = result.all()
    chats: list[dict] = []
    seen: set[str] = set()
    for log, phone, name in rows:
        if not phone or phone in seen:
            continue
        seen.add(phone)
        chats.append(
            {
                "phone": phone,
                "lastMessage": (log.content or "")[:80],
                "lastAt": log.created_at.isoformat() if log.created_at else None,
                "state": "chatting",
                "unread": False,
                "userName": name,
            },
        )
        if len(chats) >= limit:
            break
    return {"chats": chats}


@router.get("/chats/{phone}")
async def get_chat_log(
    phone: str,
    limit: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Просмотр истории диалога с клиентом по номеру телефона."""
    result = await db.execute(select(User).where(User.phone == phone))
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(status_code=404, detail=f"Пользователь с номером {phone} не найден")

    logs_result = await db.execute(
        select(ChatLog)
        .where(ChatLog.user_id == user.id)
        .order_by(ChatLog.created_at.desc())
        .limit(limit)
    )
    logs = logs_result.scalars().all()

    return {
        "phone": phone,
        "user_name": user.name,
        "count": len(logs),
        "messages": [
            {
                "id": log.id,
                "role": log.role,
                "content": log.content,
                "created_at": log.created_at.isoformat() if log.created_at else None,
                "meta": log.meta_json if isinstance(log.meta_json, dict) else None,
            }
            for log in reversed(list(logs))
        ],
    }


class CustomerNoteBody(BaseModel):
    """Тело запроса: заметка оператора о клиенте."""

    note: str = ""


@router.get("/customers/{phone}/summary")
async def customer_summary(phone: str, db: AsyncSession = Depends(get_db)) -> dict:
    """
    Сводка по клиенту для панели оператора: заказы, выручка, заметка, «чёрный список» (is_active).
    Если пользователя с таким телефоном ещё нет в БД — возвращаются нули (диалог только откроют вручную).
    """
    user = await db.scalar(select(User).where(User.phone == phone))
    if user is None:
        return {
            "user_exists": False,
            "phone": phone,
            "name": None,
            "total_orders": 0,
            "revenue_orders": 0,
            "total_spent": 0.0,
            "avg_check": 0.0,
            "is_blocked": False,
            "ai_paused": False,
            "operator_note": "",
        }

    not_cancelled = Order.status != OrderStatus.CANCELLED.value
    cnt_all = await db.scalar(
        select(func.count(Order.id)).where(Order.user_id == user.id, not_cancelled),
    ) or 0

    revenue_statuses = (
        OrderStatus.CONFIRMED.value,
        OrderStatus.SENT_TO_IIKO.value,
        OrderStatus.COMPLETED.value,
    )
    rev_row = await db.execute(
        select(
            func.count(Order.id),
            func.coalesce(func.sum(Order.total_price), 0),
        ).where(Order.user_id == user.id, Order.status.in_(revenue_statuses)),
    )
    rev = rev_row.one()
    rev_count = int(rev[0] or 0)
    total_spent = float(rev[1] or 0)
    avg_check = (total_spent / rev_count) if rev_count else 0.0

    return {
        "user_exists": True,
        "phone": user.phone,
        "name": user.name,
        "total_orders": int(cnt_all),
        "revenue_orders": rev_count,
        "total_spent": total_spent,
        "avg_check": round(avg_check, 2),
        "is_blocked": not user.is_active,
        "ai_paused": bool(getattr(user, "ai_paused", False)),
        "operator_note": user.operator_note or "",
    }


@router.post("/customers/{phone}/note")
async def save_customer_note(
    phone: str,
    body: CustomerNoteBody,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Сохранить внутреннюю заметку оператора о клиенте."""
    user = await db.scalar(select(User).where(User.phone == phone))
    if user is None:
        raise HTTPException(
            status_code=404,
            detail="Пользователь не найден — заметку можно сохранить после первого контакта клиента с ботом",
        )
    user.operator_note = body.note[:8000] if body.note else ""
    await db.flush()
    return {"ok": True}


class AiPauseBody(BaseModel):
    """Отключить или снова включить ИИ для клиента (персистентно + Redis)."""

    paused: bool = True


@router.post("/customers/{phone}/ai-pause")
async def set_customer_ai_pause(
    phone: str,
    body: AiPauseBody,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Заблокировать ИИ для номера: бот не отвечает, пока не снять блокировку.
    Дублирует смысл «перехвата», но сохраняется в БД (переживает рестарт Redis).
    """
    user = await db.scalar(select(User).where(User.phone == phone))
    if user is None:
        raise HTTPException(
            status_code=404,
            detail="Пользователь не найден — сначала должен быть диалог или заказ",
        )
    user.ai_paused = body.paused
    await db.flush()
    await db.commit()

    if body.paused:
        await set_user_state(redis_client, phone, UserState.HUMAN_MODE)
        await publish_event(
            "state_changed",
            {"phone": phone, "state": UserState.HUMAN_MODE.value},
        )
    else:
        await set_user_state(redis_client, phone, UserState.CHATTING)
        await publish_event(
            "state_changed",
            {"phone": phone, "state": UserState.CHATTING.value},
        )
    return {"ok": True, "ai_paused": user.ai_paused}


# ─── Меню ────────────────────────────────────────────────


def _menu_item_dict(item: MenuItem) -> dict:
    """Сериализация позиции меню для API и админки."""
    return {
        "id": item.id,
        "iiko_id": item.iiko_id,
        "name": item.name,
        "category": item.category or "",
        "description": item.description or "",
        "price": float(item.price),
        "is_available": item.is_available,
        "image_url": item.image_url,
    }


class MenuItemPatchBody(BaseModel):
    """Частичное обновление позиции (только переданные поля)."""

    name: str | None = Field(None, min_length=1, max_length=255)
    category: str | None = Field(None, max_length=100)
    description: str | None = None
    price: float | None = Field(None, ge=0)
    is_available: bool | None = None
    image_url: str | None = Field(None, max_length=500)


class ClearMenuBody(BaseModel):
    """Подтверждение полной очистки таблицы меню (например, на деплое без Shell)."""

    confirm: bool = Field(False, description="Должно быть true")


class MenuItemCreateBody(BaseModel):
    """Создание позиции вручную (без iiko)."""

    name: str = Field(..., min_length=1, max_length=255)
    category: str = Field(default="", max_length=100)
    description: str = ""
    price: float = Field(0, ge=0)
    is_available: bool = True
    image_url: str | None = Field(None, max_length=500)


@router.get("/menu")
async def list_menu(
    category: str | None = Query(None, description="Фильтр по категории"),
    available_only: bool = Query(True),
    stopped_only: bool = Query(
        False,
        description="Только позиции в стопе (is_available=false); при True игнорируется available_only",
    ),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Список позиций меню."""
    query = select(MenuItem).order_by(MenuItem.category, MenuItem.name)
    if category:
        query = query.where(MenuItem.category == category)
    if stopped_only:
        query = query.where(MenuItem.is_available.is_(False))
    elif available_only:
        query = query.where(MenuItem.is_available.is_(True))

    result = await db.execute(query)
    items = result.scalars().all()

    return {
        "count": len(items),
        "items": [_menu_item_dict(item) for item in items],
    }


@router.post("/menu")
async def create_menu_item(
    body: MenuItemCreateBody,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Добавить позицию меню вручную (iiko_id генерируется локально)."""
    item = MenuItem(
        name=body.name.strip(),
        category=(body.category or "").strip(),
        description=(body.description or "").strip(),
        price=body.price,
        is_available=body.is_available,
        image_url=(body.image_url or "").strip() or None,
    )
    item.iiko_id = str(uuid.uuid4())
    db.add(item)
    await db.flush()
    return {"ok": True, "item": _menu_item_dict(item)}


@router.patch("/menu/{item_id}")
async def patch_menu_item(
    item_id: int,
    body: MenuItemPatchBody,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Изменить поля позиции меню (цена, стоп-лист, название и т.д.)."""
    item = await db.get(MenuItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Позиция не найдена")

    data = body.model_dump(exclude_unset=True)
    if "name" in data and data["name"] is not None:
        data["name"] = data["name"].strip()
    if "category" in data and data["category"] is not None:
        data["category"] = data["category"].strip()
    if "description" in data and data["description"] is not None:
        data["description"] = data["description"].strip()
    if "image_url" in data:
        url = (data["image_url"] or "").strip()
        data["image_url"] = url if url else None

    for key, value in data.items():
        setattr(item, key, value)

    await db.flush()
    return {"ok": True, "item": _menu_item_dict(item)}


@router.post("/menu/clear")
async def clear_all_menu_items(
    body: ClearMenuBody,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Удалить **все** строки из ``menu_items`` (заказы в БД не трогаются — позиции в ``items_json`` сохраняются).

    Требуется ``{"confirm": true}`` — защита от случайного вызова.
    """
    if not body.confirm:
        raise HTTPException(
            status_code=400,
            detail="Для очистки меню передайте в теле JSON: {\"confirm\": true}",
        )
    cnt = await db.scalar(select(func.count()).select_from(MenuItem)) or 0
    await db.execute(sql_delete(MenuItem))
    await db.flush()
    logger.warning("Админ: полная очистка menu_items, удалено позиций: %d", cnt)
    return {"ok": True, "deleted": int(cnt)}


@router.delete("/menu/{item_id}")
async def delete_menu_item(
    item_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Удалить позицию из меню (осторожно: старые заказы ссылаются на названия в JSON)."""
    item = await db.get(MenuItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Позиция не найдена")
    await db.execute(sql_delete(MenuItem).where(MenuItem.id == item_id))
    await db.flush()
    return {"ok": True, "id": item_id}


# ─── База знаний (FAQ заведения для промпта Gemini) ──────


def _knowledge_item_dict(row: KnowledgeItem) -> dict:
    return {
        "id": row.id,
        "organization_id": row.organization_id,
        "category": row.category or "",
        "question": row.question,
        "answer": row.answer,
        "is_active": row.is_active,
        "sort_order": row.sort_order,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


class KnowledgeItemCreateBody(BaseModel):
    category: str = Field(default="", max_length=120)
    question: str = Field(..., min_length=1, max_length=500)
    answer: str = Field(..., min_length=1, max_length=50_000)
    is_active: bool = True
    sort_order: int = Field(0, ge=-10_000, le=10_000)
    organization_id: int | None = Field(None, description="NULL — общая запись для всех организаций")


class KnowledgeItemPatchBody(BaseModel):
    category: str | None = Field(None, max_length=120)
    question: str | None = Field(None, min_length=1, max_length=500)
    answer: str | None = Field(None, min_length=1, max_length=50_000)
    is_active: bool | None = None
    sort_order: int | None = Field(None, ge=-10_000, le=10_000)
    organization_id: int | None = None


@router.get("/knowledge")
async def list_knowledge_items(
    db: AsyncSession = Depends(get_db),
    active_only: bool = Query(False, description="Только is_active=true"),
) -> dict:
    """Список записей базы знаний для админки."""
    q = select(KnowledgeItem).order_by(KnowledgeItem.sort_order, KnowledgeItem.id)
    if active_only:
        q = q.where(KnowledgeItem.is_active.is_(True))
    result = await db.execute(q)
    rows = list(result.scalars().all())
    return {"items": [_knowledge_item_dict(r) for r in rows]}


@router.post("/knowledge")
async def create_knowledge_item(
    body: KnowledgeItemCreateBody,
    db: AsyncSession = Depends(get_db),
) -> dict:
    row = KnowledgeItem(
        organization_id=body.organization_id,
        category=(body.category or "").strip(),
        question=body.question.strip(),
        answer=body.answer.strip(),
        is_active=body.is_active,
        sort_order=body.sort_order,
    )
    db.add(row)
    await db.flush()
    return {"ok": True, "item": _knowledge_item_dict(row)}


@router.patch("/knowledge/{item_id}")
async def patch_knowledge_item(
    item_id: int,
    body: KnowledgeItemPatchBody,
    db: AsyncSession = Depends(get_db),
) -> dict:
    row = await db.get(KnowledgeItem, item_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    data = body.model_dump(exclude_unset=True)
    if "category" in data and data["category"] is not None:
        data["category"] = data["category"].strip()
    if "question" in data and data["question"] is not None:
        data["question"] = data["question"].strip()
    if "answer" in data and data["answer"] is not None:
        data["answer"] = data["answer"].strip()
    for key, value in data.items():
        setattr(row, key, value)
    await db.flush()
    return {"ok": True, "item": _knowledge_item_dict(row)}


@router.delete("/knowledge/{item_id}")
async def delete_knowledge_item(
    item_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await _delete_knowledge_item_impl(item_id, db)


@router.post("/knowledge/{item_id}/delete")
async def delete_knowledge_item_post(
    item_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    То же, что DELETE /knowledge/{id}: часть хостингов/прокси режет метод DELETE.
    """
    return await _delete_knowledge_item_impl(item_id, db)


async def _delete_knowledge_item_impl(item_id: int, db: AsyncSession) -> dict:
    row = await db.get(KnowledgeItem, item_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    await db.execute(sql_delete(KnowledgeItem).where(KnowledgeItem.id == item_id))
    await db.flush()
    return {"ok": True, "id": item_id}


@router.post("/menu/sync")
async def sync_menu(
    api_login: str | None = Query(
        None,
        description="API-логин iiko (если не задан — берётся IIKO_API_LOGIN из .env)",
    ),
    organization_id: str | None = Query(
        None,
        description="ID организации в iiko (если не задан — IIKO_ORGANIZATION_ID из .env)",
    ),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Синхронизация номенклатуры iiko → ``menu_items`` (цены и UUID для бота).
    Учётные данные из query или из .env. Совпадение по ``iiko_id``, не по первичному ключу БД.
    """
    login = (api_login or settings.iiko_api_login or "").strip()
    org = (organization_id or settings.iiko_organization_id or "").strip()
    if not login or not org:
        raise HTTPException(
            status_code=400,
            detail="Задайте IIKO_API_LOGIN и IIKO_ORGANIZATION_ID в .env или передайте api_login и organization_id в query.",
        )
    try:
        stats = await sync_menu_from_iiko(db, login, org)
        sk = stats.get("skipped")
        detail_m = (
            f"Синхронизация меню: успешно "
            f"(всего {stats.get('total', 0)}, новых {stats.get('created', 0)}, обновлено {stats.get('updated', 0)}"
            + (f", пропущено {sk}" if sk else "")
            + ")"
        )
        await record_menu_sync(db, True, None, detail=detail_m)
        return {"ok": True, "status": "ok", **stats}
    except Exception as exc:
        err = str(exc)
        logger.error("Ошибка синхронизации меню: %s", exc, exc_info=True)
        await record_menu_sync(db, False, err, detail=f"Синхронизация меню: ошибка — {err[:400]}")
        raise HTTPException(status_code=502, detail=f"Ошибка при обращении к iiko: {err}")


@router.post("/menu/stop-lists")
async def sync_stop_lists_endpoint(
    api_login: str = Query(..., description="API-логин iiko"),
    organization_id: str = Query(..., description="ID организации в iiko"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Принудительная синхронизация стоп-листов из iiko.
    Ставит is_available=False для позиций, которых нет в наличии.
    """
    try:
        stats = await sync_stop_lists(db, api_login, organization_id)
        return {"status": "ok", **stats}
    except Exception as exc:
        logger.error("Ошибка синхронизации стоп-листов: %s", exc, exc_info=True)
        raise HTTPException(status_code=502, detail=f"Ошибка при обращении к iiko: {exc}")


@router.post("/stop-lists/sync")
async def sync_stop_lists_from_env(
    api_login: str | None = Query(
        None,
        description="API-логин iiko (если не задан — IIKO_API_LOGIN из .env)",
    ),
    organization_id: str | None = Query(
        None,
        description="ID организации (если не задан — IIKO_ORGANIZATION_ID из .env)",
    ),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Синхронизация стоп-листов iiko → флаги is_available в menu_items (учётные данные из .env или query).
    """
    login = (api_login or settings.iiko_api_login or "").strip()
    org = (organization_id or settings.iiko_organization_id or "").strip()
    if not login or not org:
        raise HTTPException(
            status_code=400,
            detail="Задайте IIKO_API_LOGIN и IIKO_ORGANIZATION_ID в .env или передайте api_login и organization_id в query.",
        )
    try:
        stats = await sync_stop_lists(db, login, org)
        detail_s = (
            f"Стоп-листы: успешно "
            f"(в стопе: {stats.get('stopped', 0)}, восстановлено: {stats.get('restored', 0)})"
        )
        await record_stoplist_sync(db, True, None, detail=detail_s)
        snap = await build_status_payload(
            db,
            iiko_configured=True,
            whatsapp_configured=_whatsapp_env_configured(),
        )
        logger.info("Синхронизация стоп-листов из админки (.env): %s", stats)
        return {"ok": True, "status": "ok", **stats, "integration_status": snap}
    except Exception as exc:
        err = str(exc)
        logger.error("Ошибка синхронизации стоп-листов (.env): %s", exc, exc_info=True)
        await record_stoplist_sync(db, False, err, detail=f"Стоп-листы: ошибка — {err[:400]}")
        raise HTTPException(status_code=502, detail=f"Ошибка при обращении к iiko: {err}")


# ─── Демо-данные (админка) ──────────────────────────────


@router.get("/demo/status")
async def demo_status(db: AsyncSession = Depends(get_db)) -> dict:
    """Есть ли в БД пакет демо-пользователей (префикс телефона)."""
    return {"has_demo": await demo_data_exists(db)}


@router.post("/demo/seed")
async def demo_seed(db: AsyncSession = Depends(get_db)) -> dict:
    """Заполнить БД фальшивыми заказами, бронями и чатами (идемпотентно)."""
    stats = await seed_demo_data(db)
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


async def _demo_delete_core(db: AsyncSession) -> dict:
    """Общая логика удаления демо (БД + Redis-ключи сессий)."""
    if not await demo_data_exists(db):
        raise HTTPException(status_code=404, detail="Демо-данных нет")
    cleared = await clear_demo_data(db)
    return {"ok": True, **cleared}


@router.delete("/demo")
async def demo_delete(db: AsyncSession = Depends(get_db)) -> dict:
    """Удалить всех демо-пользователей и связанные заказы/брони/логи."""
    return await _demo_delete_core(db)


@router.post("/demo/delete")
async def demo_delete_post(db: AsyncSession = Depends(get_db)) -> dict:
    """
    То же, что DELETE /admin/demo.
    Нужен для сред, где HTTP DELETE режется прокси/CDN (удаление «не работает», а POST проходит).
    """
    return await _demo_delete_core(db)


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


def _sql_delete_rowcount(res) -> int:
    n = res.rowcount
    return int(n) if n is not None and n >= 0 else 0


async def _clear_redis_pending_if_matches(phone: str | None, order_id: int) -> None:
    """Если в Redis висит черновик этого заказа — снять, чтобы клиент не застрял на мёртвом id."""
    if not phone:
        return
    try:
        pid = await get_pending_order(redis_client, phone)
        if pid == order_id:
            await clear_pending_order(redis_client, phone)
    except Exception:
        logger.exception("Redis: не удалось сбросить pending_order для %s", phone)


@router.post("/settings/purge-operational-data")
async def purge_operational_data(
    body: PurgeOperationalBody,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Удалить **все** операционные записи: ``chat_logs``, ``orders``, ``bookings``,
    ``escalation_events``, ``integration_events``.

    Таблицы ``users``, ``menu_items``, ``organizations`` **не** трогаются.
    Сбрасывается строка ``integration_health`` (id=1), если есть.
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

    r_chat = await db.execute(sql_delete(ChatLog))
    r_ord = await db.execute(sql_delete(Order))
    r_book = await db.execute(sql_delete(Booking))
    r_esc = await db.execute(sql_delete(EscalationEvent))
    r_int = await db.execute(sql_delete(IntegrationEvent))

    row = await db.get(IntegrationHealth, 1)
    if row is not None:
        row.last_stoplist_at = None
        row.last_stoplist_ok = False
        row.last_stoplist_error = ""
        row.last_menu_sync_at = None
        row.last_menu_sync_ok = False
        row.last_menu_sync_error = ""

    await db.commit()
    logger.warning(
        "Админ: полный сброс операционных данных (чаты/заказы/брони/эскалации/журнал интеграций)",
    )
    return {
        "ok": True,
        "chat_logs_deleted": _sql_delete_rowcount(r_chat),
        "orders_deleted": _sql_delete_rowcount(r_ord),
        "bookings_deleted": _sql_delete_rowcount(r_book),
        "escalation_events_deleted": _sql_delete_rowcount(r_esc),
        "integration_events_deleted": _sql_delete_rowcount(r_int),
    }


@router.post("/settings/clear-menu-and-stop-snapshot")
async def clear_menu_and_stop_snapshot(
    body: ClearMenuBody,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Удалить все строки ``menu_items`` и сбросить в UI блок «последняя синхронизация стоп-листа»
    (поля ``integration_health`` для стопа). Отдельной таблицы стоп-листа в БД нет.
    """
    if not body.confirm:
        raise HTTPException(
            status_code=400,
            detail='Для очистки передайте в теле JSON: {"confirm": true}',
        )
    cnt = await db.scalar(select(func.count()).select_from(MenuItem)) or 0
    await db.execute(sql_delete(MenuItem))
    row = await db.get(IntegrationHealth, 1)
    if row is not None:
        row.last_stoplist_at = None
        row.last_stoplist_ok = False
        row.last_stoplist_error = ""
    await db.commit()
    logger.warning(
        "Админ: очистка menu_items и сброс снимка стоп-листа в integration_health, позиций: %d",
        int(cnt),
    )
    return {"ok": True, "menu_items_deleted": int(cnt), "stop_snapshot_reset": True}


@router.post("/orders/bulk-delete")
async def bulk_delete_orders(
    body: DeleteOrdersBulkBody,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Удалить заказы по списку id. Клиенты (users) не удаляются."""
    if not body.confirm:
        raise HTTPException(
            status_code=400,
            detail='Передайте {"confirm": true, "order_ids": [1, 2, ...]}',
        )
    ids = sorted({int(x) for x in body.order_ids if int(x) > 0})
    if not ids:
        raise HTTPException(status_code=400, detail="Список order_ids пуст")

    # outerjoin: заказ должен удаляться даже при битом user_id (INNER JOIN давал бы «не найден»).
    res = await db.execute(
        select(Order, User.phone)
        .outerjoin(User, Order.user_id == User.id)
        .where(Order.id.in_(ids)),
    )
    rows = res.all()
    found = {o.id for o, _p in rows}
    missing = sorted(set(ids) - found)
    if missing:
        raise HTTPException(
            status_code=404,
            detail=f"Заказы не найдены: {missing}",
        )

    for order, phone in rows:
        await _clear_redis_pending_if_matches(phone, order.id)

    r_del = await db.execute(sql_delete(Order).where(Order.id.in_(ids)))
    await db.commit()
    deleted = _sql_delete_rowcount(r_del)
    for oid in ids:
        await publish_event("order_deleted", {"order_id": oid})
    logger.warning("Админ: удалено заказов (bulk): %s", ids)
    return {"ok": True, "deleted": deleted, "order_ids": ids}


@router.post("/orders/{order_id}/delete")
async def delete_single_order(
    order_id: int,
    body: DeleteSingleOrderBody,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Удалить один заказ по id."""
    if not body.confirm:
        raise HTTPException(
            status_code=400,
            detail='Передайте {"confirm": true}',
        )
    res = await db.execute(
        select(Order, User.phone)
        .outerjoin(User, Order.user_id == User.id)
        .where(Order.id == order_id),
    )
    row = res.one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Заказ не найден")
    order, phone = row
    await _clear_redis_pending_if_matches(phone, order.id)
    await db.execute(sql_delete(Order).where(Order.id == order_id))
    await db.commit()
    await publish_event("order_deleted", {"order_id": order_id})
    logger.warning("Админ: удалён заказ #%s", order_id)
    return {"ok": True, "id": order_id}


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

    confirm: bool = Field(False, description="Должно быть true")
    phone: str = Field(..., min_length=8, max_length=32, description="Телефон как в WhatsApp (E.164)")


class RetentionRunBody(BaseModel):
    confirm: bool = Field(True, description="Должно быть true")


@router.get("/settings/environment")
async def settings_environment(db: AsyncSession = Depends(get_db)) -> dict:
    """
    Безопасный снимок окружения для админки (без секретов и полных токенов).
    """
    elig = await count_chat_logs_eligible_for_purge(db)
    integ = await build_status_payload(
        db,
        iiko_configured=_iiko_env_configured(),
        whatsapp_configured=_whatsapp_env_configured(),
    )
    return {
        "app_name": settings.app_name,
        "app_version": settings.app_version,
        "app_debug": settings.app_debug,
        "db_mode": settings.db_mode,
        "redis_enabled": settings.redis_enabled,
        "redis_backend": "redis" if settings.redis_enabled else "in_memory",
        "integrations": {
            "iiko": {
                "configured": _iiko_env_configured(),
                "terminal_group_id_set": bool(str(settings.iiko_terminal_group_id or "").strip()),
            },
            "whatsapp": {
                "configured": _whatsapp_env_configured(),
                "phone_number_id_set": bool(str(settings.whatsapp_phone_number_id or "").strip()),
            },
            "telegram": {
                "configured": bool(
                    str(settings.telegram_bot_token or "").strip()
                    and str(settings.telegram_admin_chat_id or "").strip(),
                ),
            },
            "gemini": {"configured": bool(str(settings.gemini_api_key or "").strip())},
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
    }


@router.post("/settings/redis-purge-phone")
async def redis_purge_phone(body: RedisPurgePhoneBody) -> dict:
    """Удалить из Redis/in-memory ключи chat:history, user:state, pending_order/booking для номера."""
    if not body.confirm:
        raise HTTPException(
            status_code=400,
            detail='Передайте {"confirm": true, "phone": "+7700..."}',
        )
    phone = (body.phone or "").strip()
    if not phone:
        raise HTTPException(status_code=400, detail="Укажите телефон")
    await purge_all_session_keys_for_phone(redis_client, phone)
    logger.warning("Админ: сброшена Redis-сессия для %s", phone[:6] + "…")
    return {"ok": True, "phone": phone}


@router.post("/settings/chat-logs/run-retention")
async def run_chat_log_retention_manual(
    body: RetentionRunBody,
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


@router.post("/orders/bulk-cancel")
async def bulk_cancel_orders(
    body: DeleteOrdersBulkBody,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Перевести заказы в статус cancelled (строки в БД сохраняются)."""
    if not body.confirm:
        raise HTTPException(
            status_code=400,
            detail='Передайте {"confirm": true, "order_ids": [1, 2, ...]}',
        )
    ids = sorted({int(x) for x in body.order_ids if int(x) > 0})
    if not ids:
        raise HTTPException(status_code=400, detail="Список order_ids пуст")

    res = await db.execute(
        select(Order, User.phone)
        .join(User, Order.user_id == User.id)
        .where(Order.id.in_(ids)),
    )
    rows = res.all()
    found = {o.id for o, _p in rows}
    missing = sorted(set(ids) - found)
    if missing:
        raise HTTPException(status_code=404, detail=f"Заказы не найдены: {missing}")

    cancelled = 0
    skipped = 0
    to_emit: list[tuple[int, str, float]] = []
    for order, phone in rows:
        if order.status == OrderStatus.CANCELLED.value:
            skipped += 1
            continue
        order.status = OrderStatus.CANCELLED.value
        order.iiko_last_error = None
        await _clear_redis_pending_if_matches(phone, order.id)
        cancelled += 1
        to_emit.append((order.id, phone, float(order.total_price)))

    await db.commit()
    for oid, phone, total in to_emit:
        await publish_event(
            "order_updated",
            {
                "order_id": oid,
                "status": OrderStatus.CANCELLED.value,
                "phone": phone,
                "total_price": total,
                "iiko_last_error": None,
            },
        )
    logger.warning("Админ: массовая отмена заказов: ids=%s, отменено=%d, уже были отменены=%d", ids, cancelled, skipped)
    return {"ok": True, "cancelled": cancelled, "skipped_already_cancelled": skipped, "order_ids": ids}


@router.get("/export/orders")
async def export_orders_csv(
    date_from: date | None = Query(None, description="Начало периода (UTC, дата)"),
    date_to: date | None = Query(None, description="Конец периода включительно (UTC, дата)"),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """CSV заказов за период (UTF-8 с BOM для Excel)."""
    lo, hi_excl = _export_range_utc(date_from, date_to)
    lo_sql = _sql_dt_for_filter(lo)
    hi_sql = _sql_dt_for_filter(hi_excl)

    res = await db.execute(
        select(Order, User.phone, User.name)
        .join(User, Order.user_id == User.id)
        .where(Order.created_at >= lo_sql, Order.created_at < hi_sql)
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
        meta = _order_meta_from_items_json(o.items_json if isinstance(o.items_json, dict) else None)
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
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """CSV сообщений chat_logs за период (роль, телефон клиента)."""
    lo, hi_excl = _export_range_utc(date_from, date_to)
    lo_sql = _sql_dt_for_filter(lo)
    hi_sql = _sql_dt_for_filter(hi_excl)

    res = await db.execute(
        select(ChatLog, User.phone)
        .join(User, ChatLog.user_id == User.id)
        .where(ChatLog.created_at >= lo_sql, ChatLog.created_at < hi_sql)
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


# ─── Статистика дашборда ────────────────────────────────

@router.get("/stats")
async def dashboard_stats(
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Статистика для дашборда: выручка за сегодня, общие счётчики."""
    now_utc = datetime.now(tz=timezone.utc)
    today_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    ts_lo = _sql_dt_for_filter(today_start)
    ts_hi = _sql_dt_for_filter(now_utc)
    ys_lo = _sql_dt_for_filter(today_start - timedelta(days=1))

    not_cancelled = Order.status != OrderStatus.CANCELLED

    total_q = await db.execute(
        select(func.count(Order.id), func.coalesce(func.sum(Order.total_price), 0))
        .where(not_cancelled)
    )
    total_row = total_q.one()
    total_orders = total_row[0]
    total_revenue = float(total_row[1])

    today_q = await db.execute(
        select(func.count(Order.id), func.coalesce(func.sum(Order.total_price), 0))
        .where(
            not_cancelled,
            Order.created_at >= ts_lo,
            Order.created_at <= ts_hi,
        )
    )
    today_row = today_q.one()
    today_orders = today_row[0]
    today_revenue = float(today_row[1])

    yesterday_start = today_start - timedelta(days=1)
    yesterday_q = await db.execute(
        select(func.count(Order.id), func.coalesce(func.sum(Order.total_price), 0))
        .where(
            not_cancelled,
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
    rows = await db.execute(
        select(Order.created_at, Order.total_price).where(
            not_cancelled,
            Order.created_at.isnot(None),
        ),
    )
    bucket: dict[str, dict[str, float | int]] = defaultdict(
        lambda: {"revenue": 0.0, "orders": 0},
    )
    for created_at, total_price in rows.all():
        dk = _order_day_key_utc(created_at)
        if dk and dk in valid_set:
            bucket[dk]["revenue"] += float(total_price or 0)
            bucket[dk]["orders"] += 1
    daily_series = [
        {
            "date": k,
            "revenue": float(bucket[k]["revenue"]),
            "orders": int(bucket[k]["orders"]),
        }
        for k in valid_keys
    ]

    bookings_result = await db.execute(select(func.count(Booking.id)))
    bookings_count = bookings_result.scalar() or 0

    menu_result = await db.execute(select(func.count(MenuItem.id)))
    menu_count = menu_result.scalar() or 0

    failed_open = int(
        await db.scalar(select(func.count(FailedTask.id)).where(FailedTask.resolved.is_(False))) or 0,
    )

    upsell_rows = await db.execute(
        select(Order.items_json).where(
            not_cancelled,
            Order.created_at >= ts_lo,
            Order.created_at <= ts_hi,
        ),
    )
    upsell_offered_today = 0
    upsell_accepted_today = 0
    for (ij,) in upsell_rows.all():
        meta = _order_meta_from_items_json(ij if isinstance(ij, dict) else None)
        rec = meta.get("recommendation") if isinstance(meta.get("recommendation"), dict) else {}
        off = str(rec.get("offered") or "").strip()
        if not off:
            continue
        upsell_offered_today += 1
        items = (ij or {}).get("items") if isinstance(ij, dict) else None
        if isinstance(items, list):
            off_l = off.lower()
            for it in items:
                if not isinstance(it, dict):
                    continue
                nm = str(it.get("name") or "").lower()
                if off_l and off_l in nm:
                    upsell_accepted_today += 1
                    break

    upsell_conversion_pct: float | None = None
    if upsell_offered_today > 0:
        upsell_conversion_pct = round(upsell_accepted_today / upsell_offered_today * 100, 1)

    iiko_errors_today = int(
        await db.scalar(
            select(func.count(Order.id)).where(
                Order.created_at >= ts_lo,
                Order.created_at <= ts_hi,
                Order.iiko_last_error.isnot(None),
                func.coalesce(Order.iiko_last_error, "") != "",
            ),
        )
        or 0,
    )

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
        "upsell_conversion_pct": upsell_conversion_pct,
        "iiko_errors_today": iiko_errors_today,
    }


# ─── Аналитика ──────────────────────────────────────────


@router.get("/analytics")
async def analytics(
    response: Response,
    period: str = Query("week", description="day, week, month, custom"),
    date_from: str | None = Query(None, description="Начало периода (YYYY-MM-DD)"),
    date_to: str | None = Query(None, description="Конец периода (YYYY-MM-DD)"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Аналитика: выручка, количество заказов, средний чек по дням.
    Поддерживает период: day, week, month, custom (с date_from/date_to).

    Границы и дневные корзины — **UTC** (как в GET /stats), ключ дня — календарная дата в UTC.
    """
    response.headers["Cache-Control"] = "no-store"

    now = datetime.now(tz=timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    period = (period or "week").strip().lower()
    if period not in ("day", "week", "month", "custom"):
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

    esc_count = int(
        await db.scalar(
            select(func.count(EscalationEvent.id)).where(
                EscalationEvent.created_at >= start_sql,
                EscalationEvent.created_at <= end_sql,
            ),
        )
        or 0,
    )
    prev_esc = int(
        await db.scalar(
            select(func.count(EscalationEvent.id)).where(
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
        .where(not_cancelled, Order.created_at >= start_sql, Order.created_at <= end_sql)
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
            Order.created_at >= prev_start_sql,
            Order.created_at < prev_end_sql,
        )
    )
    prev_row = prev_q.one()
    prev_count = prev_row[0]
    prev_revenue = float(prev_row[1])

    avg_check = current_revenue / current_count if current_count else 0
    prev_avg = prev_revenue / prev_count if prev_count else 0

    # Загружаем только заказы текущего периода (для daily + top_items)
    cur_orders_result = await db.execute(
        select(Order)
        .where(not_cancelled, Order.created_at >= start_sql, Order.created_at <= end_sql)
    )
    current_orders = cur_orders_result.scalars().all()

    daily: dict[str, dict] = defaultdict(
        lambda: {"revenue": 0.0, "orders": 0}
    )
    for o in current_orders:
        dk = _order_day_key_utc(o.created_at)
        if dk:
            daily[dk]["revenue"] += float(o.total_price or 0)
            daily[dk]["orders"] += 1

    # Все календарные дни от start до end включительно (UTC), без off-by-one по .days
    start_d = _dt_as_utc(start).date()
    end_d = _dt_as_utc(end).date()
    daily_data: list[dict] = []
    walk = start_d
    while walk <= end_d:
        key = walk.isoformat()
        entry = daily.get(key, {"revenue": 0.0, "orders": 0})
        daily_data.append({
            "date": key,
            "revenue": entry["revenue"],
            "orders": entry["orders"],
        })
        walk += timedelta(days=1)

    # Топ позиций
    item_stats: dict[str, dict] = defaultdict(
        lambda: {"quantity": 0, "revenue": 0.0}
    )
    for o in current_orders:
        items_data = o.items_json or {}
        for item in items_data.get("items", []):
            name = item.get("name", "?")
            qty = item.get("quantity", 0)
            total = item.get("item_total", 0)
            item_stats[name]["quantity"] += qty
            item_stats[name]["revenue"] += float(total)

    top_items = sorted(
        [{"name": k, **v} for k, v in item_stats.items()],
        key=lambda x: x["revenue"],
        reverse=True,
    )[:10]

    # Воронка: активность в чатах → черновики → «закрытые» в iiko/завершённые
    chat_users_sq = (
        select(ChatLog.user_id)
        .where(ChatLog.created_at >= start_sql, ChatLog.created_at <= end_sql)
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
                    [OrderStatus.SENT_TO_IIKO.value, OrderStatus.COMPLETED.value],
                ),
                not_cancelled,
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
                    [OrderStatus.SENT_TO_IIKO.value, OrderStatus.COMPLETED.value],
                ),
                not_cancelled,
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
        },
        "heatmap": {
            "matrix": heatmap_matrix,
            "weekday_labels": ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"],
        },
    }


# ─── Human Override (Перехват диалога) ───────────────────


@router.post("/chats/{phone}/takeover")
async def takeover_chat(phone: str) -> dict:
    """
    Перехватить диалог — AI замолкает, оператор ведёт общение вручную.
    Устанавливает флаг HUMAN_MODE в Redis.
    """
    await set_user_state(redis_client, phone, UserState.HUMAN_MODE)
    await publish_event("state_changed", {
        "phone": phone, "state": UserState.HUMAN_MODE,
    })
    logger.info("Оператор перехватил диалог: %s", phone)
    return {"status": "ok", "phone": phone, "mode": "human"}


@router.post("/chats/{phone}/release")
async def release_chat(phone: str) -> dict:
    """
    Вернуть управление боту — AI снова отвечает на сообщения.
    Возвращает состояние в CHATTING.
    """
    await set_user_state(redis_client, phone, UserState.CHATTING)
    await publish_event("state_changed", {
        "phone": phone, "state": UserState.CHATTING,
    })
    logger.info("Оператор вернул бота: %s", phone)
    return {"status": "ok", "phone": phone, "mode": "bot"}


class TextRequest(BaseModel):
    """Тело запроса с текстовым сообщением."""
    text: str


@router.post("/chats/{phone}/send_message")
async def admin_send_message(
    phone: str,
    body: TextRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Отправить сообщение клиенту от имени оператора.
    Сохраняется в ChatLog и отправляется через WhatsApp.
    """
    user = await get_or_create_user(db, phone)
    db.add(ChatLog(user_id=user.id, role="operator", content=body.text))

    await send_message(phone, body.text)
    await publish_event("new_message", {
        "phone": phone, "role": "operator", "content": body.text,
    })
    logger.info("Оператор отправил сообщение в %s: %s", phone, body.text[:50])
    return {"status": "sent", "phone": phone}


@router.get("/chats/{phone}/state")
async def get_chat_state(phone: str) -> dict:
    """Получить текущее состояние диалога (CHATTING, CONFIRMING_ORDER, HUMAN_MODE)."""
    state = await get_user_state(redis_client, phone)
    return {"phone": phone, "state": state.value}


# ─── Тест бота (без WhatsApp) ────────────────────────────

@router.post("/test-bot")
async def test_bot(body: TextRequest) -> dict:
    """
    Тестовый endpoint: эмулирует диалог с ботом без WhatsApp.
    Использует фиктивный номер 'test-admin', проходит полный цикл AI.
    """
    from app.api.webhooks import (
        handle_booking_confirmation,
        handle_confirmation,
        handle_order_payment_choice,
    )

    phone = "test-admin"
    message_text = body.text

    state = await get_user_state(redis_client, phone)

    if state == UserState.HUMAN_MODE:
        return {"reply": "[HUMAN_MODE — AI отключён]", "state": state.value, "intent": None}

    if state == UserState.AWAITING_ORDER_PAYMENT:
        reply = await handle_order_payment_choice(phone, message_text)
        await append_to_history(redis_client, phone, "user", message_text)
        await append_to_history(redis_client, phone, "assistant", reply)
        new_state = await get_user_state(redis_client, phone)
        return {"reply": reply, "state": new_state.value, "intent": None}

    if state == UserState.CONFIRMING_ORDER:
        reply = await handle_confirmation(phone, message_text)
        await append_to_history(redis_client, phone, "user", message_text)
        await append_to_history(redis_client, phone, "assistant", reply)
        new_state = await get_user_state(redis_client, phone)
        return {"reply": reply, "state": new_state.value, "intent": None}

    if state == UserState.CONFIRMING_BOOKING:
        reply = await handle_booking_confirmation(phone, message_text)
        await append_to_history(redis_client, phone, "user", message_text)
        await append_to_history(redis_client, phone, "assistant", reply)
        new_state = await get_user_state(redis_client, phone)
        return {"reply": reply, "state": new_state.value, "intent": None}

    history = await get_chat_history(redis_client, phone)
    await append_to_history(redis_client, phone, "user", message_text)

    async with async_session_factory() as db:
        menu_items = await load_available_menu(db)
        menu_context = build_menu_context(menu_items)
        u_row = await db.scalar(select(User).where(User.phone == phone))
        org_id = u_row.organization_id if u_row else None
        kb_context = await load_knowledge_context_block(db, org_id)
        draft_row = await get_open_draft_order(db, phone)
        draft_ctx = format_draft_order_context_for_prompt(
            draft_row.items_json if draft_row else None,
        )
        ai_response = await call_gemini(
            history,
            message_text,
            menu_context,
            kb_context,
            draft_order_context=draft_ctx,
        )
        result = await route_intent(
            db, phone, ai_response, menu_items=menu_items,
        )

        if result.new_state:
            await set_user_state(redis_client, phone, result.new_state)
        if result.pending_order_id:
            await set_pending_order(redis_client, phone, result.pending_order_id)
        if result.pending_booking_id:
            await set_pending_booking(redis_client, phone, result.pending_booking_id)

        await db.commit()

    await append_to_history(redis_client, phone, "assistant", result.reply_text)

    new_state = await get_user_state(redis_client, phone)
    return {
        "reply": result.reply_text,
        "state": new_state.value,
        "intent": ai_response.intent,
        "items": [item.model_dump() for item in ai_response.items] if ai_response.items else [],
    }


# ─── Packaging Rules CRUD ──────────────────────────────────


def _packaging_rule_dict(r: PackagingRule) -> dict:
    return {
        "id": r.id,
        "kind": r.kind,
        "name": r.name,
        "price": float(r.price),
        "iiko_product_id": r.iiko_product_id or "",
        "keywords": r.keywords or "",
        "option_key": r.option_key or "",
        "is_active": r.is_active,
        "sort_order": r.sort_order,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    }


class PackagingRuleCreateBody(BaseModel):
    kind: str = Field(..., min_length=1, max_length=60)
    name: str = Field(..., min_length=1, max_length=200)
    price: float = Field(0, ge=0)
    iiko_product_id: str | None = None
    keywords: str = ""
    option_key: str = ""
    is_active: bool = True
    sort_order: int = 0


class PackagingRulePatchBody(BaseModel):
    kind: str | None = Field(None, min_length=1, max_length=60)
    name: str | None = Field(None, min_length=1, max_length=200)
    price: float | None = Field(None, ge=0)
    iiko_product_id: str | None = None
    keywords: str | None = None
    option_key: str | None = None
    is_active: bool | None = None
    sort_order: int | None = None


@router.get("/packaging-rules")
async def list_packaging_rules(db: AsyncSession = Depends(get_db)) -> dict:
    """Все правила упаковки (включая неактивные). При пустой таблице — создание сидов."""
    from app.services.order_logic import load_packaging_rules, PACKAGING_SEED

    result = await db.execute(
        select(PackagingRule).order_by(PackagingRule.sort_order.desc(), PackagingRule.id)
    )
    rows = list(result.scalars().all())
    if not rows:
        for seed in PACKAGING_SEED:
            db.add(PackagingRule(**seed))
        await db.flush()
        result2 = await db.execute(
            select(PackagingRule).order_by(PackagingRule.sort_order.desc(), PackagingRule.id)
        )
        rows = list(result2.scalars().all())
    return {"items": [_packaging_rule_dict(r) for r in rows]}


@router.post("/packaging-rules")
async def create_packaging_rule(
    body: PackagingRuleCreateBody,
    db: AsyncSession = Depends(get_db),
) -> dict:
    existing = await db.scalar(select(PackagingRule).where(PackagingRule.kind == body.kind.strip()))
    if existing:
        raise HTTPException(status_code=409, detail=f"Правило с kind='{body.kind}' уже существует")
    row = PackagingRule(
        kind=body.kind.strip(),
        name=body.name.strip(),
        price=body.price,
        iiko_product_id=(body.iiko_product_id or "").strip() or None,
        keywords=(body.keywords or "").strip(),
        option_key=(body.option_key or "").strip(),
        is_active=body.is_active,
        sort_order=body.sort_order,
    )
    db.add(row)
    await db.flush()
    return {"ok": True, "item": _packaging_rule_dict(row)}


@router.patch("/packaging-rules/{rule_id}")
async def patch_packaging_rule(
    rule_id: int,
    body: PackagingRulePatchBody,
    db: AsyncSession = Depends(get_db),
) -> dict:
    row = await db.get(PackagingRule, rule_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Правило не найдено")
    data = body.model_dump(exclude_unset=True)
    for key in ("kind", "name", "keywords", "option_key"):
        if key in data and data[key] is not None:
            data[key] = data[key].strip()
    if "iiko_product_id" in data:
        data["iiko_product_id"] = (data["iiko_product_id"] or "").strip() or None
    for key, value in data.items():
        setattr(row, key, value)
    await db.flush()
    return {"ok": True, "item": _packaging_rule_dict(row)}


@router.delete("/packaging-rules/{rule_id}")
async def delete_packaging_rule(
    rule_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict:
    row = await db.get(PackagingRule, rule_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Правило не найдено")
    await db.delete(row)
    await db.flush()
    return {"ok": True}
