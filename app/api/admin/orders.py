"""Orders and failed-tasks admin API (E0.1 split from _monolith.py)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import and_, delete as sql_delete, func, or_, select, update
from sqlalchemy.orm import joinedload
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Booking, ChatLog, FailedTask, Order, OrderStatus, Organization, PaymentEvent, User
from app.db.session import async_session_factory, get_db, redis_client
from app.services.events import publish_event
from app.services.order_logic import (
    classify_packaging_kind,
    finalize_order_draft,
    load_available_menu,
    load_packaging_rules,
    merge_confidence_into_order_meta,
    validate_mixed_payment_total,
    validate_order,
)
from app.services.intent_router import confirm_order, get_open_draft_order, get_or_create_user
from app.services.payment_autoprint_iiko import run_auto_send_to_iiko_after_payment
from app.services.payment_notify import run_payment_received_customer_notify
from app.services.intelligence_analytics import order_meta_from_items_json
from app.services.tenant_scope import (
    failed_tasks_tenant_clause as _failed_tasks_tenant_clause,
    orders_tenant_clause as _orders_tenant_clause,
)
from app.schemas.ai_schemas import AIBrainResponse, BookingDetails, OrderItem, PaymentSplit
from app.api.webhooks import _normalize_phone_e164
from .deps import (
    _order_in_org,
    _pick_seed_menu_item,
    admin_actor_key,
    admin_org_from_session,
    require_admin_session_active,
    require_superadmin,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/admin",
    tags=["Orders"],
    dependencies=[Depends(require_admin_session_active)],
)


# ─── Helpers ──────────────────────────────────────────────


def _make_naive(dt: datetime | None) -> datetime | None:
    """Убираем tzinfo для корректного сравнения с naive-датами."""
    if dt is None:
        return None
    return dt.replace(tzinfo=None) if dt.tzinfo else dt


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
    if preserved.get("delivery_address_verified") is True:
        new_meta["delivery_address_verified"] = True
    return new_meta


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


def _sql_delete_rowcount(res) -> int:
    n = res.rowcount
    return int(n) if n is not None and n >= 0 else 0


async def _clear_redis_pending_if_matches(
    phone: str | None,
    order_id: int,
    organization_id: int | None = None,
) -> None:
    """Если в Redis висит черновик этого заказа — снять, чтобы клиент не застрял на мёртвом id."""
    from app.services.dialog_mgr import clear_pending_order, get_pending_order

    if not phone:
        return
    try:
        pid = await get_pending_order(redis_client, phone, organization_id=organization_id)
        if pid == order_id:
            await clear_pending_order(redis_client, phone, organization_id=organization_id)
    except Exception:
        logger.exception("Redis: не удалось сбросить pending_order для %s", phone)


def _timeline_payment_title(ev: PaymentEvent) -> str:
    et = (ev.event_type or "").strip().lower()
    mapping = {
        "prepayment_confirmed": "Предоплата подтверждена",
        "prepayment_waived": "Предоплата снята",
        "webhook_paid": "Вебхук: оплата получена",
        "webhook_failed": "Вебхук оплаты: ошибка / несоответствие",
        "manual_reset": "Предоплата сброшена вручную",
    }
    return mapping.get(et, f"Платёж: {et or 'событие'}")


# ─── Schemas ──────────────────────────────────────────────


class OrderPatchBody(BaseModel):
    """Смена статуса заказа из админки (канбан, ручное подтверждение)."""

    status: str = Field(..., description="draft | confirmed | sent_to_iiko | …")
    expected_version: int | None = Field(
        default=None,
        description="Ожидаемая row_version; при рассинхроне — 409.",
    )

    notify_customer: bool = False
    message_template: str | None = Field(default=None, max_length=1000)


class AdminFoodLineIn(BaseModel):
    """Позиции еды для пересборки черновика (админка)."""

    name: str = Field(min_length=1, max_length=240)
    quantity: int = Field(ge=1, le=99)
    iiko_item_id: str = ""
    packaging_plov_1kg: str = ""
    modifiers_ids: list[str] = Field(default_factory=list)
    modifiers: list[dict[str, Any]] = Field(default_factory=list)
    exclude_ingredients: list[str] = Field(default_factory=list)


class OrderRebuildDraftBody(BaseModel):
    food_lines: list[AdminFoodLineIn] = Field(min_length=1)
    expected_version: int | None = Field(
        default=None,
        description="Ожидаемая version строки заказа; при рассинхроне — 409.",
    )


class OrderPaymentSplitPatchBody(BaseModel):
    """Ручная правка способа оплаты в order_meta (после смены состава и т.п.)."""

    payment_mode: Literal["single", "mixed"]
    payment_method: Literal["cash", "card", "remote"] = "cash"
    split_cash: float = Field(0.0, ge=0)
    split_card: float = Field(0.0, ge=0)
    split_remote: float = Field(0.0, ge=0)
    expected_version: int | None = Field(
        default=None,
        description="Ожидаемая row_version; при рассинхроне — 409.",
    )


class AdminManualOrderBody(BaseModel):
    """Создание черновика заказа из админки (тесты и ручной ввод)."""

    phone: str = Field(..., min_length=8, max_length=32)
    order_type: Literal["delivery", "pickup", "hall"] = "pickup"
    payment_mode: Literal["single", "mixed"] = "single"
    payment_method: Literal["cash", "card", "remote"] = "cash"
    split_cash: float = Field(0.0, ge=0)
    split_card: float = Field(0.0, ge=0)
    split_remote: float = Field(0.0, ge=0)
    delivery_address: str = ""
    pickup_time_note: str = ""
    food_lines: list[AdminFoodLineIn] = Field(default_factory=list)


class FailedTaskPatchBody(BaseModel):
    resolved: bool = False


PREPAYMENT_STATUS_KEYS = frozenset({"not_required", "pending", "paid", "waived"})


class OrderPaymentPatchBody(BaseModel):
    """Ручное обновление предоплаты (Kaspi-ссылка и т.д.) до подключения платёжного API."""

    prepayment_status: str | None = Field(default=None, description="not_required | pending | paid | waived")
    payment_link_url: str | None = Field(default=None, description="URL для оплаты или пустая строка чтобы сбросить")


class DeleteOrdersBulkBody(BaseModel):
    """Удаление заказов по списку id (и сброс Redis pending_order при совпадении)."""

    confirm: bool = Field(False, description="Должно быть true")
    order_ids: list[int] = Field(..., min_length=1, max_length=80)


class DeleteSingleOrderBody(BaseModel):
    confirm: bool = Field(False, description="Должно быть true")


# ─── Routes: Orders ───────────────────────────────────────


ORDER_TIMELINE_CHAT_LIMIT = 15


@router.get("/orders")
async def list_orders(
    request: Request,
    status: str | None = Query(None, description="Фильтр по статусу (draft, confirmed, ...)"),
    q: str | None = Query(None, description="Поиск по № заказа, телефону или имени клиента"),
    sum_min: float | None = Query(None, ge=0, description="Мин. сумма заказа"),
    sum_max: float | None = Query(None, ge=0, description="Макс. сумма заказа"),
    # New pagination (Stripe-style)
    page: int | None = Query(None, ge=1),
    size: int | None = Query(None, ge=1, le=500),
    # Backward-compat aliases
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Список заказов с пагинацией; телефон/имя — из связанного пользователя (WhatsApp)."""
    from datetime import timedelta

    org_id = admin_org_from_session(request)
    base_query = (
        select(Order, User.phone, User.name)
        .join(User, Order.user_id == User.id)
        .options(joinedload(Order.booking))
        .where(
            User.organization_id == org_id,
            _orders_tenant_clause(org_id),
        )
    )
    if status:
        base_query = base_query.where(Order.status == status)
    if sum_min is not None:
        base_query = base_query.where(Order.total_price >= sum_min)
    if sum_max is not None:
        base_query = base_query.where(Order.total_price <= sum_max)
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
        base_query = base_query.where(or_(*clauses))

    # Resolve page/size (preferred) or limit/offset (legacy)
    eff_size = int(size) if size is not None else int(limit)
    eff_size = max(1, min(500, eff_size))
    if page is not None:
        eff_page = int(page)
        eff_offset = (eff_page - 1) * eff_size
    else:
        eff_offset = int(offset)
        eff_page = (eff_offset // eff_size) + 1 if eff_size else 1

    # total count (same filters)
    total = int(
        await db.scalar(select(func.count()).select_from(base_query.subquery())) or 0
    )
    pages = int((total + eff_size - 1) // eff_size) if eff_size else 1
    has_more = (eff_offset + eff_size) < total

    query = base_query.order_by(Order.created_at.desc()).limit(eff_size).offset(eff_offset)

    result = await db.execute(query)
    rows = result.all()

    # Batch-загрузка последних PaymentTransaction для найденных заказов
    from app.db.models import PaymentTransaction as _PTx
    _order_ids = [o.id for o, _, _ in rows]
    _tx_map: dict[int, _PTx] = {}
    if _order_ids:
        _txs = await db.scalars(
            select(_PTx)
            .where(_PTx.order_id.in_(_order_ids))
            .order_by(_PTx.order_id, _PTx.id.desc())
        )
        for _tx in _txs:
            if _tx.order_id not in _tx_map:
                _tx_map[_tx.order_id] = _tx

    # P1.5: число исходящих WhatsApp failed в окне ±1 ч от created_at заказа.
    # Считаем в Python: SQL datetime +/- timedelta компилируется по-разному в SQLite/Postgres.
    _failed_wa_map: dict[int, int] = {int(oid): 0 for oid in _order_ids}
    _orders_for_failed = [(o.id, o.user_id, _make_naive(o.created_at)) for o, _, _ in rows if o.created_at]
    if _orders_for_failed:
        _created_values = [dt for _, _, dt in _orders_for_failed if dt is not None]
        if _created_values:
            _min_dt = min(_created_values) - timedelta(hours=1)
            _max_dt = max(_created_values) + timedelta(hours=1)
            _user_ids = sorted({int(uid) for _, uid, _ in _orders_for_failed})
            _log_rows = (
                await db.execute(
                    select(ChatLog.user_id, ChatLog.created_at)
                    .where(
                        ChatLog.organization_id == org_id,
                        ChatLog.user_id.in_(_user_ids),
                        ChatLog.role != "user",
                        func.lower(func.coalesce(ChatLog.delivery_status, "")) == "failed",
                        ChatLog.created_at >= _min_dt,
                        ChatLog.created_at <= _max_dt,
                    )
                )
            ).all()
            for oid, uid, order_dt in _orders_for_failed:
                if order_dt is None:
                    continue
                start = order_dt - timedelta(hours=1)
                end = order_dt + timedelta(hours=1)
                _failed_wa_map[int(oid)] = sum(
                    1
                    for log_uid, log_dt_raw in _log_rows
                    if int(log_uid) == int(uid)
                    and (log_dt := _make_naive(log_dt_raw)) is not None
                    and start <= log_dt <= end
                )

    out: list[dict] = []
    for o, phone, user_name in rows:
        items_json = o.items_json
        meta = order_meta_from_items_json(items_json if isinstance(items_json, dict) else None)
        _ltx = _tx_map.get(o.id)
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
                "payment_provider": getattr(o, "payment_provider", None),
                "external_payment_id": getattr(o, "external_payment_id", None),
                "payment_amount_captured": (
                    float(o.payment_amount_captured)
                    if getattr(o, "payment_amount_captured", None) is not None
                    else None
                ),
                "row_version": int(getattr(o, "row_version", 1) or 1),
                "payment_split_warning": _check_mixed_payment_split(
                    items_json if isinstance(items_json, dict) else None, float(o.total_price),
                ),
                "failed_whatsapp_near_order": int(_failed_wa_map.get(int(o.id), 0)),
                "order_confidence": meta.get("confidence"),
                "low_confidence": bool((meta.get("confidence") or {}).get("low_confidence")),
                "latest_payment_tx": {
                    "id": _ltx.id,
                    "provider": _ltx.provider,
                    "status": _ltx.status,
                    "amount": float(_ltx.amount),
                    "currency": _ltx.currency,
                    "payment_url": _ltx.payment_url,
                    "expires_at": _ltx.expires_at.isoformat() if _ltx.expires_at else None,
                    "paid_at": _ltx.paid_at.isoformat() if _ltx.paid_at else None,
                    "failure_reason": _ltx.failure_reason,
                } if _ltx else None,
            }
        )

    return {
        # Standard response
        "items": out,
        "total": total,
        "page": eff_page,
        "pages": pages,
        "has_more": has_more,
        # Backward-compat (to be removed after frontend migration)
        "count": len(out),
        "orders": out,
        "limit": eff_size,
        "offset": eff_offset,
    }


@router.get("/orders/{order_id}/timeline")
async def admin_order_timeline(
    request: Request,
    order_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Хронология заказа: создание, оплаты, чат, ошибка iiko, текущий статус (без полной истории смен статусов в БД)."""
    org_id = admin_org_from_session(request)
    order = await _order_in_org(db, order_id, org_id)

    raw_events: list[tuple[datetime | None, dict[str, Any]]] = []

    if order.created_at:
        raw_events.append(
            (
                order.created_at,
                {
                    "kind": "order_created",
                    "title": "Заказ создан",
                    "detail": "",
                    "meta": {"status": order.status},
                },
            ),
        )

    pay_rows = (
        await db.execute(
            select(PaymentEvent)
            .where(PaymentEvent.order_id == order.id)
            .order_by(PaymentEvent.created_at.asc(), PaymentEvent.id.asc()),
        )
    ).scalars().all()
    for ev in pay_rows:
        note_s = (ev.note or "").strip()
        if len(note_s) > 280:
            note_s = note_s[:279] + "…"
        raw_events.append(
            (
                ev.created_at,
                {
                    "kind": "payment",
                    "title": _timeline_payment_title(ev),
                    "detail": note_s or f"{ev.event_type} · {ev.actor}",
                    "meta": {
                        "event_type": ev.event_type,
                        "actor": ev.actor,
                        "amount": float(ev.amount) if ev.amount is not None else None,
                    },
                },
            ),
        )

    chat_rows = (
        await db.execute(
            select(ChatLog)
            .where(
                ChatLog.user_id == order.user_id,
                ChatLog.organization_id == org_id,
            )
            .order_by(ChatLog.created_at.desc(), ChatLog.id.desc())
            .limit(ORDER_TIMELINE_CHAT_LIMIT),
        )
    ).scalars().all()
    for log in reversed(chat_rows):
        ct = (log.content or "").strip()
        if len(ct) > 160:
            ct = ct[:159] + "…"
        ds = (log.delivery_status or "").strip()
        extra = f" · {ds}" if ds and log.role != "user" else ""
        raw_events.append(
            (
                log.created_at,
                {
                    "kind": "chat",
                    "title": f"Чат ({log.role})",
                    "detail": ct + extra,
                    "meta": {"role": log.role, "delivery_status": log.delivery_status},
                },
            ),
        )

    err_txt = (order.iiko_last_error or "").strip()
    if err_txt:
        at_err = order.updated_at or order.created_at
        raw_events.append(
            (
                at_err,
                {
                    "kind": "iiko_error",
                    "title": "Ошибка отправки в iiko",
                    "detail": err_txt[:400],
                    "meta": {},
                },
            ),
        )

    def _sort_key(tup: tuple[datetime | None, Any]) -> datetime:
        dt = tup[0]
        if dt is None:
            return datetime.min.replace(tzinfo=timezone.utc)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

    raw_events.sort(key=_sort_key)

    snap_at = order.updated_at or order.created_at
    raw_events.append(
        (
            snap_at,
            {
                "kind": "current_status",
                "title": f"Текущее состояние: {order.status}",
                "detail": "Фактический статус строки заказа (история переходов в отдельной таблице не хранится).",
                "meta": {"status": order.status, "prepayment_status": getattr(order, "prepayment_status", None)},
            },
        ),
    )

    events_out: list[dict[str, Any]] = []
    for at, payload in raw_events:
        ev_out = dict(payload)
        ev_out["at"] = at.isoformat() if at else None
        events_out.append(ev_out)

    return {"ok": True, "order_id": int(order.id), "events": events_out}


@router.patch("/orders/{order_id}/payment")
async def patch_order_payment_meta(
    request: Request,
    order_id: int,
    body: OrderPaymentPatchBody,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Проставить ссылку на оплату и статус предоплаты (оператор или будущий webhook)."""
    if body.prepayment_status is None and body.payment_link_url is None:
        raise HTTPException(status_code=400, detail="Укажите prepayment_status и/или payment_link_url")

    order = await _order_in_org(db, order_id, admin_org_from_session(request))

    old_status = (order.prepayment_status or "").strip().lower()
    notify_paid = False

    if body.prepayment_status is not None:
        st = (body.prepayment_status or "").strip().lower()
        if st not in PREPAYMENT_STATUS_KEYS:
            raise HTTPException(
                status_code=400,
                detail=f"Недопустимый prepayment_status (ожидается одно из: {', '.join(sorted(PREPAYMENT_STATUS_KEYS))})",
            )
        order.prepayment_status = st

        if st == "paid" and old_status != "paid":
            notify_paid = True
            if not (order.external_payment_id or "").strip():
                order.payment_provider = "manual"
                order.payment_amount_captured = float(order.total_price or 0)

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
    if notify_paid:
        background_tasks.add_task(run_payment_received_customer_notify, order.id)
        background_tasks.add_task(run_auto_send_to_iiko_after_payment, order.id)
    return {
        "ok": True,
        "id": order.id,
        "prepayment_status": order.prepayment_status,
        "payment_link_url": order.payment_link_url,
    }


@router.patch("/orders/{order_id}")
async def patch_order_status(
    request: Request,
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
    from .chats import admin_send_message
    from .schemas import TextRequest

    org_id = admin_org_from_session(request)
    order = await _order_in_org(db, order_id, org_id)
    if body.expected_version is not None and int(order.row_version or 0) != int(body.expected_version):
        raise HTTPException(status_code=409, detail="Заказ изменился. Обновите список.")
    phone = await db.scalar(select(User.phone).where(User.id == order.user_id))
    phone_s = (phone or "").strip()

    want = body.status.strip().lower()
    cur = (order.status or "").lower()

    async def _emit(upd: Order, **extra) -> dict:
        created_at_iso = upd.created_at.isoformat() if getattr(upd, "created_at", None) else None
        await publish_event(
            "order_updated",
            {
                "order_id": upd.id,
                "status": upd.status,
                "phone": phone_s,
                "total_price": float(upd.total_price),
                "iiko_last_error": upd.iiko_last_error,
                "organization_id": org_id,
                **({"created_at": created_at_iso} if created_at_iso else {}),
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
        expected = (
            int(body.expected_version)
            if body.expected_version is not None
            else int(order.row_version or 0)
        )
        claimed = (
            await db.execute(
                update(Order)
                .where(
                    Order.id == order.id,
                    Order.status == OrderStatus.CONFIRMED.value,
                    Order.row_version == expected,
                )
                .values(
                    status=OrderStatus.SENDING_TO_IIKO.value,
                    row_version=Order.row_version + 1,
                    iiko_last_error=None,
                )
            )
        ).rowcount
        if claimed != 1:
            raise HTTPException(status_code=409, detail="Заказ уже изменён или отправляется в iiko")
        await db.commit()

        sent_to_iiko, iiko_err, iiko_raw = await _send_order_to_iiko(
            order_id=order.id,
            phone=phone_s,
            items_json=order.items_json,
            restaurant_organization_id=int(order.organization_id or org_id),
        )

        async with async_session_factory() as db2:
            locked = await _order_in_org(db2, order.id, org_id)
            if sent_to_iiko:
                locked.status = OrderStatus.SENT_TO_IIKO.value
                locked.iiko_last_error = None
                # Запрос отзыва через REVIEW_REQUEST_DELAY_SEC
                try:
                    from app.core.config import settings as _settings
                    from app.services.task_queue import enqueue_job
                    if _settings.review_requests_enabled:
                        await enqueue_job(
                            "send_review_request",
                            _defer_by=_settings.review_request_delay_sec,
                            org_id=org_id,
                            order_id=locked.id,
                            phone=phone_s,
                        )
                except Exception as _rev_exc:
                    logger.warning("review_request enqueue failed: %s", _rev_exc)
                if iiko_raw and isinstance(locked.items_json, dict):
                    ij = dict(locked.items_json)
                    raw_om = ij.get("order_meta")
                    om: dict = dict(raw_om) if isinstance(raw_om, dict) else {}
                    oi = iiko_raw.get("orderInfo") if isinstance(iiko_raw.get("orderInfo"), dict) else {}
                    om["iiko_last_send"] = {
                        "correlation_id": iiko_raw.get("correlationId"),
                        "iiko_order_id": oi.get("id"),
                        "external_number": str(locked.id),
                        "sent_at": datetime.now(timezone.utc).isoformat(),
                    }
                    ij["order_meta"] = om
                    locked.items_json = ij
            else:
                locked.status = OrderStatus.CONFIRMED.value
                locked.iiko_last_error = iiko_err or "iiko: неизвестная ошибка"
            locked.row_version = int(locked.row_version or 0) + 1
            await db2.commit()
            items_json = locked.items_json if isinstance(locked.items_json, dict) else None
            meta = order_meta_from_items_json(items_json)
            order_public = {
                "id": locked.id,
                "user_id": locked.user_id,
                "user_phone": phone_s,
                "user_name": None,
                "status": locked.status,
                "items": locked.items_json,
                "items_count": _order_items_count(items_json),
                "total_price": float(locked.total_price),
                "created_at": locked.created_at.isoformat() if locked.created_at else None,
                "updated_at": locked.updated_at.isoformat() if locked.updated_at else None,
                "iiko_last_error": locked.iiko_last_error,
                "order_type": meta.get("order_type"),
                "payment_method": meta.get("payment_method"),
                "delivery_address": meta.get("delivery_address") or "",
                "booking_id": locked.booking_id,
                "booking": _booking_public(locked.booking),
                "prepayment_status": getattr(locked, "prepayment_status", None) or "not_required",
                "payment_link_url": getattr(locked, "payment_link_url", None),
                "payment_provider": getattr(locked, "payment_provider", None),
                "external_payment_id": getattr(locked, "external_payment_id", None),
                "payment_amount_captured": (
                    float(locked.payment_amount_captured)
                    if getattr(locked, "payment_amount_captured", None) is not None
                    else None
                ),
                "row_version": int(getattr(locked, "row_version", 1) or 1),
                "payment_split_warning": _check_mixed_payment_split(items_json, float(locked.total_price)),
            }
            await publish_event(
                "order_updated",
                {
                    "order_id": locked.id,
                    "status": locked.status,
                    "phone": phone_s,
                    "total_price": float(locked.total_price),
                    "iiko_last_error": locked.iiko_last_error,
                    "organization_id": org_id,
                    "order": order_public,
                },
            )
            return {
                "ok": True,
                "id": locked.id,
                "status": locked.status,
                "iiko_last_error": locked.iiko_last_error,
            }

    fulfillment_allowed = {
        OrderStatus.SENT_TO_IIKO.value: {OrderStatus.IN_TRANSIT.value, OrderStatus.WAITING_PICKUP.value, OrderStatus.COMPLETED.value},
        OrderStatus.IN_TRANSIT.value: {OrderStatus.WAITING_PICKUP.value, OrderStatus.COMPLETED.value, OrderStatus.SENT_TO_IIKO.value},
        OrderStatus.WAITING_PICKUP.value: {OrderStatus.COMPLETED.value, OrderStatus.IN_TRANSIT.value, OrderStatus.SENT_TO_IIKO.value},
    }
    if want in fulfillment_allowed.get(cur, set()):
        order.status = want
        order.iiko_last_error = None
        order.row_version = int(order.row_version or 0) + 1
        ij = dict(order.items_json or {}) if isinstance(order.items_json, dict) else {}
        om = dict(ij.get("order_meta") or {}) if isinstance(ij.get("order_meta"), dict) else {}
        events = list(om.get("fulfillment_events") or []) if isinstance(om.get("fulfillment_events"), list) else []
        event = {"from": cur, "to": want, "at": datetime.now(timezone.utc).isoformat(), "actor": admin_actor_key(request)}
        if body.message_template:
            event["message_template"] = body.message_template
        events.append(event)
        om["fulfillment_events"] = events[-25:]
        ij["order_meta"] = om
        order.items_json = ij
        await db.commit()
        if body.notify_customer and phone_s and body.message_template:
            await admin_send_message(request, phone_s, TextRequest(text=body.message_template), db)
        return await _emit(order, fulfillment_event=event)

    raise HTTPException(
        status_code=400,
        detail=f"Переход {cur!r} → {want!r} не поддерживается",
    )


@router.post("/orders/{order_id}/rebuild-draft")
async def rebuild_order_draft(
    request: Request,
    order_id: int,
    body: OrderRebuildDraftBody,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Пересборка позиций заказа: validate_order → finalize_order_draft (fee_lines, доставка, и т.д.).
    Сохраняет тип доставки/оплату из order_meta; recommendation_trace не затирается.

    Доступно для **draft** и **confirmed** (правка до отправки в iiko). После **sent_to_iiko** — нельзя.
    """
    order = await _order_in_org(db, order_id, admin_org_from_session(request))
    st = (order.status or "").lower()
    if st not in (OrderStatus.DRAFT.value, OrderStatus.CONFIRMED.value):
        raise HTTPException(
            status_code=400,
            detail="Пересборка доступна только для черновика или подтверждённого заказа (до отправки в iiko).",
        )
    if body.expected_version is not None and int(order.row_version) != int(body.expected_version):
        raise HTTPException(
            status_code=409,
            detail="Заказ изменился в другом окне. Обновите список и повторите правку.",
        )
    old_ij = order.items_json if isinstance(order.items_json, dict) else {}
    old_meta = order_meta_from_items_json(old_ij)
    preserved_rec: dict[str, object] = {
        k: old_meta[k]
        for k in ("recommendation", "recommendation_trace")
        if k in old_meta and old_meta[k] is not None
    }
    if old_meta.get("delivery_address_verified") is True:
        preserved_rec["delivery_address_verified"] = True
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
                modifiers_ids=list(line.modifiers_ids or []),
                modifiers=list(line.modifiers or []),
                exclude_ingredients=list(line.exclude_ingredients or []),
            ),
        )
    validated = await validate_order(items_in, db=db, organization_id=int(order.organization_id))
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
    org_id = int(order.organization_id)
    org_ent = await db.get(Organization, org_id)
    prepay_enf = bool(org_ent.prepayment_enforced) if org_ent else True
    rules = await load_packaging_rules(db, org_id)
    merged, grand_total = finalize_order_draft(
        validated, ai, packaging_rules=rules, prepayment_enforced=prepay_enf,
    )
    mix_err = validate_mixed_payment_total(ai, grand_total)
    if mix_err:
        raise HTTPException(status_code=400, detail=mix_err)
    om = merged.get("order_meta")
    if isinstance(om, dict):
        merged["order_meta"] = _merge_preserved_order_meta_keys(om, preserved_rec)
        merge_confidence_into_order_meta(merged["order_meta"], validated)
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


@router.patch("/orders/{order_id}/payment-split")
async def patch_order_payment_split(
    request: Request,
    order_id: int,
    body: OrderPaymentSplitPatchBody,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Ручная правка способа оплаты в order_meta после смены суммы (состав, доставка и т.п.).
    """
    order = await _order_in_org(db, order_id, admin_org_from_session(request))
    st = (order.status or "").lower()
    if st not in (OrderStatus.DRAFT.value, OrderStatus.CONFIRMED.value):
        raise HTTPException(
            status_code=400,
            detail="Правка оплаты доступна только для черновика или подтверждённого заказа (до отправки в iiko).",
        )
    if body.expected_version is not None and int(order.row_version) != int(body.expected_version):
        raise HTTPException(
            status_code=409,
            detail="Заказ изменился в другом окне. Обновите список и повторите.",
        )
    ij = order.items_json if isinstance(order.items_json, dict) else {}
    meta = dict(order_meta_from_items_json(ij))
    grand_total = float(order.total_price or 0)

    pm = body.payment_mode
    pay_m = body.payment_method
    if pay_m not in ("cash", "card", "remote"):
        pay_m = "cash"

    if pm == "mixed":
        ps = PaymentSplit(
            cash=float(body.split_cash),
            card=float(body.split_card),
            remote=float(body.split_remote),
        )
        ai_chk = AIBrainResponse(
            intent="order",
            reply_text="",
            items=[],
            order_type=str(meta.get("order_type") or "pickup"),
            payment_mode="mixed",
            payment_method=pay_m,
            payment_split=ps,
        )
        mix_err = validate_mixed_payment_total(ai_chk, grand_total)
        if mix_err:
            raise HTTPException(status_code=400, detail=mix_err)
        pay_details: dict[str, object] = {
            "type": "mixed",
            "split": {
                "cash": float(ps.cash),
                "card": float(ps.card),
                "remote": float(ps.remote),
            },
        }
    else:
        pay_details = {"type": "single", "method": pay_m}

    meta["payment_mode"] = pm
    meta["payment_method"] = pay_m
    meta["payment_details"] = pay_details

    new_ij = dict(ij)
    new_ij["order_meta"] = meta
    order.items_json = new_ij
    order.row_version = int(order.row_version) + 1
    await db.commit()
    await db.refresh(order)
    split_warn = _check_mixed_payment_split(new_ij, grand_total)
    return {
        "ok": True,
        "id": order.id,
        "row_version": int(order.row_version),
        "items_json": new_ij,
        "payment_split_warning": split_warn,
    }


@router.post("/orders/manual")
async def create_manual_order(
    request: Request,
    body: AdminManualOrderBody,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Создать черновик заказа из админки (тест / ручной ввод).
    Без позиций — одна «безопасная» строка из меню (не плов 1 кг без упаковки).
    """
    org_id = admin_org_from_session(request)
    raw_phone = (body.phone or "").strip()
    phone = _normalize_phone_e164(raw_phone)
    if not phone:
        raise HTTPException(status_code=400, detail="Некорректный номер телефона")

    existing = await get_open_draft_order(db, phone, org_id)
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"У этого номера уже есть открытый черновик заказа №{existing.id}. "
                "Завершите или отмените его."
            ),
        )

    user = await get_or_create_user(db, phone, org_id)
    menu_items = await load_available_menu(db, organization_id=org_id)
    rules = await load_packaging_rules(db, org_id)

    items_in: list[OrderItem] = []
    if body.food_lines:
        for line in body.food_lines:
            pkg_raw = (line.packaging_plov_1kg or "").strip()
            if pkg_raw not in ("", "tabak", "foil_kazan"):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Недопустимое packaging_plov_1kg для «{line.name}» "
                        "(ожидается tabak, foil_kazan или пусто)."
                    ),
                )
            items_in.append(
                OrderItem(
                    name=line.name.strip(),
                    quantity=line.quantity,
                    iiko_item_id=(line.iiko_item_id or "").strip(),
                    packaging_plov_1kg=pkg_raw,  # type: ignore[arg-type]
                    modifiers_ids=list(line.modifiers_ids or []),
                    modifiers=list(line.modifiers or []),
                    exclude_ingredients=list(line.exclude_ingredients or []),
                ),
            )
    else:
        seed = _pick_seed_menu_item(menu_items)
        if seed is None:
            raise HTTPException(
                status_code=400,
                detail="В меню нет доступных позиций для тестового заказа.",
            )
        items_in.append(
            OrderItem(
                name=(seed.name or "").strip(),
                quantity=1,
                iiko_item_id=(seed.iiko_id or "").strip(),
                packaging_plov_1kg="",
                exclude_ingredients=[],
            ),
        )

    validated = await validate_order(items_in, db=db, organization_id=org_id)
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

    pm_mode = body.payment_mode
    pay_m = body.payment_method
    if pay_m not in ("cash", "card", "remote"):
        pay_m = "cash"
    ps = PaymentSplit(
        cash=float(body.split_cash),
        card=float(body.split_card),
        remote=float(body.split_remote),
    )
    ai = AIBrainResponse(
        intent="order",
        reply_text="",
        items=[],
        order_type=body.order_type,
        payment_mode=pm_mode,
        payment_method=pay_m,
        payment_split=ps,
        delivery_address=(body.delivery_address or "").strip(),
        pickup_time_note=(body.pickup_time_note or "").strip(),
    )
    org_ent = await db.get(Organization, org_id)
    prepay_enf = bool(org_ent.prepayment_enforced) if org_ent else True
    merged, grand_total = finalize_order_draft(
        validated, ai, packaging_rules=rules, prepayment_enforced=prepay_enf,
    )
    mix_err = validate_mixed_payment_total(ai, grand_total)
    if mix_err:
        raise HTTPException(status_code=400, detail=mix_err)

    om = merged.get("order_meta") if isinstance(merged.get("order_meta"), dict) else {}
    prepayment_status = (
        "pending" if om.get("requires_order_prepayment") else "not_required"
    )

    order = Order(
        organization_id=org_id,
        user_id=user.id,
        status=OrderStatus.DRAFT,
        items_json=merged,
        total_price=grand_total,
        prepayment_status=prepayment_status,
        row_version=1,
    )
    db.add(order)
    await db.commit()
    await db.refresh(order)
    split_warn = _check_mixed_payment_split(merged, float(grand_total))
    return {
        "ok": True,
        "id": order.id,
        "phone": phone,
        "total_price": float(order.total_price),
        "row_version": int(order.row_version),
        "items_json": merged,
        "payment_split_warning": split_warn,
    }


# ─── Failed tasks ─────────────────────────────────────────


@router.get("/failed-tasks")
async def list_failed_tasks(
    request: Request,
    resolved: str | None = Query(None, description="true | false | all"),
    phone: str | None = Query(None, max_length=24),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Очередь ошибок обработки сообщений (retry исчерпан)."""
    org_id = admin_org_from_session(request)
    task_scope = _failed_tasks_tenant_clause(org_id)
    q = select(FailedTask).where(task_scope)
    cnt_q = select(func.count(FailedTask.id)).where(task_scope)
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
    request: Request,
    task_id: int,
    body: FailedTaskPatchBody,
    db: AsyncSession = Depends(get_db),
) -> dict:
    org_id = admin_org_from_session(request)
    res = await db.execute(
        select(FailedTask).where(
            FailedTask.id == task_id,
            _failed_tasks_tenant_clause(org_id),
        ),
    )
    t = res.scalar_one_or_none()
    if t is None:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    t.resolved = body.resolved
    await db.commit()
    await db.refresh(t)
    return {"ok": True, "task": _failed_task_public(t)}


@router.post("/failed-tasks/{task_id}/retry")
async def retry_failed_task(
    request: Request,
    task_id: int,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Поставить failed task на повторную обработку через тот же pipeline, что и входящий WhatsApp."""
    org_id = admin_org_from_session(request)
    res = await db.execute(
        select(FailedTask).where(
            FailedTask.id == task_id,
            _failed_tasks_tenant_clause(org_id),
        ),
    )
    t = res.scalar_one_or_none()
    if t is None:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    if bool(t.resolved):
        raise HTTPException(status_code=409, detail="Задача уже закрыта")
    phone = (t.phone or "").strip()
    message_text = (t.message_text or "").strip()
    if not phone or not message_text:
        raise HTTPException(status_code=400, detail="Недостаточно данных для повтора")

    from app.services.task_queue import dispatch_arq_or_background

    await dispatch_arq_or_background(
        "whatsapp_process_text",
        background_tasks,
        phone=phone,
        message_text=message_text,
        whatsapp_message_id="",
        webhook_value=None,
        organization_id=org_id,
    )
    t.resolved = True
    await db.commit()
    await db.refresh(t)
    return {
        "ok": True,
        "queued": "arq",
        "task": _failed_task_public(t),
    }


# ─── Bulk delete / cancel ─────────────────────────────────


@router.post("/orders/bulk-delete")
async def bulk_delete_orders(
    request: Request,
    body: DeleteOrdersBulkBody,
    _perm: None = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Удалить заказы по списку id. Клиенты (users) не удаляются."""
    if not body.confirm:
        raise HTTPException(
            status_code=400,
            detail='Передайте {"confirm": true, "order_ids": [1, 2, ...]}',
        )
    org_id = admin_org_from_session(request)
    ids = sorted({int(x) for x in body.order_ids if int(x) > 0})
    if not ids:
        raise HTTPException(status_code=400, detail="Список order_ids пуст")

    # outerjoin: заказ должен удаляться даже при битом user_id (INNER JOIN давал бы «не найден»).
    res = await db.execute(
        select(Order, User.phone)
        .outerjoin(User, Order.user_id == User.id)
        .where(
            Order.id.in_(ids),
            or_(
                Order.organization_id == org_id,
                and_(Order.organization_id.is_(None), User.organization_id == org_id),
            ),
        ),
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
        await _clear_redis_pending_if_matches(phone, order.id, organization_id=org_id)

    r_del = await db.execute(sql_delete(Order).where(Order.id.in_(ids)))
    await db.commit()
    deleted = _sql_delete_rowcount(r_del)
    for oid in ids:
        await publish_event("order_deleted", {"order_id": oid, "organization_id": org_id})
    logger.warning("Админ: удалено заказов (bulk): %s", ids)
    return {"ok": True, "deleted": deleted, "order_ids": ids}


@router.post("/orders/{order_id}/delete")
async def delete_single_order(
    request: Request,
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
    org_id = admin_org_from_session(request)
    res = await db.execute(
        select(Order, User.phone)
        .outerjoin(User, Order.user_id == User.id)
        .where(
            Order.id == order_id,
            or_(
                Order.organization_id == org_id,
                and_(Order.organization_id.is_(None), User.organization_id == org_id),
            ),
        ),
    )
    row = res.one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Заказ не найден")
    order, phone = row
    await _clear_redis_pending_if_matches(phone, order.id, organization_id=org_id)
    await db.execute(sql_delete(Order).where(Order.id == order_id))
    await db.commit()
    await publish_event("order_deleted", {"order_id": order_id, "organization_id": org_id})
    logger.warning("Админ: удалён заказ #%s", order_id)
    return {"ok": True, "id": order_id}


@router.post("/orders/bulk-cancel")
async def bulk_cancel_orders(
    request: Request,
    body: DeleteOrdersBulkBody,
    _perm: None = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Перевести заказы в статус cancelled (строки в БД сохраняются)."""
    if not body.confirm:
        raise HTTPException(
            status_code=400,
            detail='Передайте {"confirm": true, "order_ids": [1, 2, ...]}',
        )
    org_id = admin_org_from_session(request)
    ids = sorted({int(x) for x in body.order_ids if int(x) > 0})
    if not ids:
        raise HTTPException(status_code=400, detail="Список order_ids пуст")

    res = await db.execute(
        select(Order, User.phone)
        .join(User, Order.user_id == User.id)
        .where(Order.id.in_(ids), _orders_tenant_clause(org_id)),
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
        await _clear_redis_pending_if_matches(phone, order.id, organization_id=org_id)
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
                "organization_id": org_id,
            },
        )
    logger.warning("Админ: массовая отмена заказов: ids=%s, отменено=%d, уже были отменены=%d", ids, cancelled, skipped)
    return {"ok": True, "cancelled": cancelled, "skipped_already_cancelled": skipped, "order_ids": ids}
