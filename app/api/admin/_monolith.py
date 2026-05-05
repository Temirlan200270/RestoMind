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
from typing import Annotated, Any, Literal
from datetime import date, datetime, time as dt_time, timedelta, timezone

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect
from sqlalchemy import delete as sql_delete
from sqlalchemy import and_, exists, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.passwords import hash_password, verify_password
from app.db.models import (
    Booking,
    ChatLog,
    EscalationEvent,
    FailedTask,
    IntegrationEvent,
    KnowledgeItem,
    MenuItem,
    Order,
    OrderStatus,
    Organization,
    PackagingRule,
    PaymentEvent,
    RegistrationRequest,
    StaffRole,
    StaffUser,
    UpsellRule,
    User,
)
from app.db.session import async_session_factory, get_db, redis_client
from app.integrations.whatsapp import send_message
from app.services.admin_tokens import AdminWsClaims, create_admin_ws_token, parse_admin_ws_token
from app.services.ai_brain import call_openai
from app.services.customer_context import build_customer_context
from app.services.time_context import check_operational_status, format_org_current_time_block, parse_schedule_json
from app.services.demo_data import clear_demo_data, demo_data_exists, seed_demo_data
from app.services.integration_health import (
    build_status_payload,
    list_integration_events,
    record_menu_sync,
    record_stoplist_sync,
)
from app.services.chat_delivery import finalize_outbound_delivery
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
    update_user_session_fields_in_db,
)
from app.services.events import publish_event, subscribe_events
from app.services.booking_halls import BOOKING_HALL_KEYS, BOOKING_HALL_VIP, vip_slot_occupied
from app.services.intent_router import (
    confirm_order,
    get_open_draft_order,
    get_or_create_user,
    route_intent,
)
from app.services.iiko_onboarding import setup_organization_iiko, verify_iiko_api_login
from app.services.menu_embeddings import reindex_organization_menu_embeddings
from app.services.menu_sync import sync_menu_from_iiko, sync_stop_lists
from app.services.org_iiko import resolve_org_iiko_credentials
from app.services.tenant_scope import (
    available_organizations_for_admin_session,
    branding_placeholder_e21,
    organization_id_allowed_for_admin_session,
    resolve_tenant_summary_for_session,
)
from app.services.knowledge_context import load_knowledge_context_block
from app.schemas.ai_schemas import AIBrainResponse, BookingDetails, OrderItem, PaymentSplit
from app.services.order_logic import (
    build_menu_context_for_ai,
    classify_packaging_kind,
    finalize_order_draft,
    format_draft_order_context_for_prompt,
    invalidate_menu_context_cache,
    load_available_menu,
    load_packaging_rules,
    validate_mixed_payment_total,
    validate_order,
)
from app.services.payment_autoprint_iiko import run_auto_send_to_iiko_after_payment
from app.services.payment_notify import run_payment_received_customer_notify
from app.services.tenant_scope import (
    failed_tasks_tenant_clause as _failed_tasks_tenant_clause,
    orders_tenant_clause as _orders_tenant_clause,
    phones_subquery_for_org as _phones_subquery_for_org,
)
from app.services.sales_strategy import build_sales_strategy, format_strategy_for_prompt
from app.services.intelligence_analytics import (
    delivery_geo_rows,
    menu_engineering_rows,
    order_meta_from_items_json,
    upsell_stats_from_items_json,
)
from app.services.owner_roi import aggregate_org_window, build_achievements_week, build_today_narrative_ru
from app.services.readiness import build_admin_readiness_payload
from app.api.webhooks import _normalize_phone_e164

from .deps import (
    _bookings_tenant_clause,
    _credentials_ok,
    _escalation_tenant_clause,
    _integration_events_tenant_clause,
    _iiko_login_org_for_tenant,
    _knowledge_tenant_clause,
    _menu_item_in_org,
    _menu_tenant_clause,
    _order_in_org,
    _packaging_tenant_clause,
    _pick_seed_menu_item,
    _session_is_superadmin,
    _session_staff_user,
    admin_org_from_session,
    require_admin_session,
    require_admin_session_active,
    require_staff_admin,
    require_superadmin,
)

logger = logging.getLogger(__name__)


# ─── Публичные эндпоинты входа (без сессии) ──────────────

auth_router = APIRouter(prefix="/admin/auth", tags=["Admin Auth"])


class LoginBody(BaseModel):
    """Данные формы входа: email staff или legacy username + пароль."""

    username: str = ""
    email: str = ""
    password: str = ""


class SignupBody(BaseModel):
    """Self-serve регистрация ресторана и первого администратора."""

    restaurant_name: str = Field(..., min_length=2, max_length=255)
    network_name: str = Field(default="", max_length=255, description="Опционально: название сети/холдинга")
    email: str = Field(..., min_length=3, max_length=255)
    password: str = Field(..., min_length=8, max_length=128)


class RequestAccessBody(BaseModel):
    restaurant_name: str = Field(..., min_length=2, max_length=255)
    contact_name: str = Field(default="", max_length=255)
    phone: str = Field(default="", max_length=32)
    email: str = Field(default="", max_length=255)
    has_iiko: bool = False
    note: str = Field(default="", max_length=4000)


@auth_router.post("/login")
async def admin_login(request: Request, body: LoginBody, db: AsyncSession = Depends(get_db)) -> dict:
    """Staff по email или legacy ADMIN_USERNAME / ADMIN_PASSWORD."""
    email_try = (body.email or body.username).strip().lower()
    password = body.password
    request.session.clear()

    if email_try and "@" in email_try:
        staff = await db.scalar(
            select(StaffUser).where(
                StaffUser.email == email_try,
                StaffUser.is_active.is_(True),
            ),
        )
        if staff and verify_password(password, staff.password_hash):
            if not bool(staff.is_superadmin):
                org_login = await db.get(Organization, int(staff.organization_id))
                if org_login is not None and not bool(org_login.is_active):
                    raise HTTPException(
                        status_code=403,
                        detail="Подписка приостановлена. Свяжитесь с администратором.",
                    )
            request.session["admin_ok"] = True
            request.session["admin_user"] = staff.email
            request.session["staff_id"] = int(staff.id)
            request.session["organization_id"] = int(staff.organization_id)
            request.session.pop("is_demo", None)
            ws_token = create_admin_ws_token(
                organization_id=int(staff.organization_id),
                email=staff.email,
                staff_id=int(staff.id),
            )
            return {
                "ok": True,
                "username": staff.email,
                "organization_id": int(staff.organization_id),
                "staff_role": (staff.role or StaffRole.ADMIN.value).strip().lower(),
                "is_superadmin": bool(staff.is_superadmin),
                "ws_token": ws_token,
            }

    if _credentials_ok(body.username.strip(), password):
        # Legacy-вход (ADMIN_USERNAME/PASSWORD): привязываем сессию к реальной организации,
        # иначе при миграциях/демо-данных id может быть не 1 и админка будет "пустой".
        oid_db = await db.scalar(select(Organization.id).order_by(Organization.id.asc()).limit(1))
        oid = int(oid_db) if oid_db is not None else int(settings.default_organization_id)
        org_login = await db.get(Organization, oid)
        if org_login is not None and not bool(org_login.is_active):
            raise HTTPException(status_code=403, detail="Подписка приостановлена. Свяжитесь с администратором.")
        request.session["admin_ok"] = True
        request.session["admin_user"] = body.username.strip()
        request.session["organization_id"] = oid
        request.session["staff_id"] = None
        request.session.pop("is_demo", None)
        ws_token = create_admin_ws_token(
            organization_id=oid,
            email=body.username.strip(),
            staff_id=None,
        )
        return {
            "ok": True,
            "username": body.username.strip(),
            "organization_id": oid,
            "staff_role": StaffRole.ADMIN.value,
            "is_superadmin": False,
            "ws_token": ws_token,
        }

    raise HTTPException(status_code=401, detail="Неверный логин или пароль")


@auth_router.post("/signup")
async def admin_signup_disabled(request: Request, body: SignupBody) -> dict:
    """Self-serve регистрация отключена: только заявка на модерацию."""
    _ = request
    _ = body
    raise HTTPException(status_code=410, detail="Регистрация теперь по заявке на подключение")


@auth_router.post("/demo-login")
async def admin_demo_login(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    """Гостевой вход в демо-организацию (read-only)."""
    request.session.clear()
    demo_org = await db.scalar(
        select(Organization).where(
            Organization.is_demo.is_(True),
        ),
    )
    if demo_org is None:
        demo_org = await db.scalar(select(Organization).where(Organization.slug == "demo"))
    if demo_org is None:
        raise HTTPException(status_code=503, detail="Демо временно недоступно")
    if not bool(demo_org.is_active):
        raise HTTPException(status_code=503, detail="Демо временно отключено")

    request.session["admin_ok"] = True
    request.session["admin_user"] = "demo-guest"
    request.session["organization_id"] = int(demo_org.id)
    request.session["staff_id"] = None
    request.session["is_demo"] = True

    ws_token = create_admin_ws_token(
        organization_id=int(demo_org.id),
        email="demo-guest",
        staff_id=None,
    )
    return {
        "ok": True,
        "username": "demo-guest",
        "organization_id": int(demo_org.id),
        "staff_role": StaffRole.OPERATOR.value,
        "is_superadmin": False,
        "ws_token": ws_token,
    }


@auth_router.post("/request-access")
async def admin_request_access(body: RequestAccessBody, db: AsyncSession = Depends(get_db)) -> dict:
    """Создать заявку на модерацию подключения ресторана."""
    req = RegistrationRequest(
        restaurant_name=(body.restaurant_name or "").strip(),
        contact_name=(body.contact_name or "").strip(),
        phone=(body.phone or "").strip(),
        email=(body.email or "").strip().lower(),
        has_iiko=bool(body.has_iiko),
        note=(body.note or "").strip(),
        status="pending",
    )
    db.add(req)
    await db.commit()
    await db.refresh(req)

    token = (settings.telegram_bot_token or "").strip()
    chat_id = (settings.superadmin_telegram_chat_id or settings.telegram_admin_chat_id or "").strip()
    if token and chat_id:
        payload = {
            "chat_id": chat_id,
            "text": (
                "🆕 <b>Новая заявка на подключение</b>\n"
                f"Ресторан: <code>{req.restaurant_name}</code>\n"
                f"Контакт: <code>{req.contact_name or '—'}</code>\n"
                f"Телефон: <code>{req.phone or '—'}</code>\n"
                f"Email: <code>{req.email or '—'}</code>\n"
                f"iiko: <code>{'да' if req.has_iiko else 'нет'}</code>"
            ),
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(f"https://api.telegram.org/bot{token}/sendMessage", json=payload)
                resp.raise_for_status()
        except Exception:
            logger.warning("request-access: telegram notify failed", exc_info=True)

    return {"ok": True, "request_id": int(req.id)}


@auth_router.post("/logout")
async def admin_logout(request: Request) -> dict:
    """Завершить сессию."""
    request.session.clear()
    return {"ok": True}


class SelectOrgBody(BaseModel):
    """Смена активного филиала в сессии (владелец сети / суперадмин)."""

    organization_id: int = Field(..., ge=1)


# ── E2.1 ── Мультифилиальность: расширение GET /auth/me и POST /auth/select-org


async def _admin_auth_me_payload(request: Request, db: AsyncSession) -> dict[str, Any]:
    """Тело ответа GET /auth/me и успешного POST /auth/select-org (контракт PARALLEL_AI_PLAN §4)."""
    user = request.session.get("admin_user") or settings.admin_username
    oid = admin_org_from_session(request)
    # Если в сессии лежит несуществующий organization_id (после миграций/ресетов БД),
    # переведём админку на первую доступную организацию.
    exists_oid = await db.scalar(select(Organization.id).where(Organization.id == int(oid)))
    if exists_oid is None:
        oid_db = await db.scalar(select(Organization.id).order_by(Organization.id.asc()).limit(1))
        if oid_db is not None:
            oid = int(oid_db)
            request.session["organization_id"] = oid
    sid = request.session.get("staff_id")
    staff_role = StaffRole.ADMIN.value
    is_demo = bool(request.session.get("is_demo"))
    is_superadmin = False
    staff_me: StaffUser | None = None
    if sid is not None:
        staff_me = await db.get(StaffUser, int(sid))
        if staff_me is not None:
            staff_role = (staff_me.role or StaffRole.ADMIN.value).strip().lower()
            is_superadmin = bool(staff_me.is_superadmin)
    if not is_superadmin:
        org_me = await db.get(Organization, int(oid))
        if org_me is not None and not bool(org_me.is_active):
            raise HTTPException(status_code=403, detail="Подписка приостановлена. Свяжитесь с администратором.")

    available = await available_organizations_for_admin_session(
        db,
        staff=staff_me,
        is_superadmin=is_superadmin,
        is_demo=is_demo,
        session_organization_id=int(oid),
    )
    tenant_payload = await resolve_tenant_summary_for_session(
        db,
        staff=staff_me,
        active_organization_id=int(oid),
    )
    branding = branding_placeholder_e21()

    staff_id_val: int | None = int(sid) if sid is not None else None
    email_out = str(user)
    if staff_me is not None:
        email_out = str(staff_me.email)

    return {
        "authenticated": True,
        "username": user,
        "organization_id": int(oid),
        "staff_role": staff_role,
        "is_demo": is_demo,
        "is_superadmin": is_superadmin,
        "ws_token": create_admin_ws_token(
            organization_id=int(oid),
            email=email_out,
            staff_id=staff_id_val,
        ),
        "id": staff_id_val,
        "email": email_out,
        "role": staff_role,
        "tenant_owner_id": int(staff_me.tenant_owner_id)
        if staff_me is not None and staff_me.tenant_owner_id is not None
        else None,
        "active_organization_id": int(oid),
        "available_organizations": available,
        "tenant": tenant_payload,
        "branding": branding,
    }


@auth_router.get("/me")
async def admin_me(request: Request, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Проверка сессии и перевыпуск ws_token для переподключения."""
    if not request.session.get("admin_ok"):
        raise HTTPException(status_code=401, detail="Не авторизован")
    return await _admin_auth_me_payload(request, db)


@auth_router.post("/select-org")
async def admin_select_org(
    request: Request,
    body: SelectOrgBody,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Переключить активный филиал в cookie-сессии (проверка доступа по tenant / роли)."""
    if not request.session.get("admin_ok"):
        raise HTTPException(status_code=401, detail="Не авторизован")

    sid = request.session.get("staff_id")
    staff_me: StaffUser | None = None
    is_demo = bool(request.session.get("is_demo"))
    is_superadmin = False
    if sid is not None:
        staff_me = await db.get(StaffUser, int(sid))
        if staff_me is not None:
            is_superadmin = bool(staff_me.is_superadmin)

    cur_oid = admin_org_from_session(request)
    ok_switch = await organization_id_allowed_for_admin_session(
        db,
        staff=staff_me,
        is_superadmin=is_superadmin,
        is_demo=is_demo,
        target_organization_id=int(body.organization_id),
        session_organization_id=int(cur_oid),
    )
    if not ok_switch:
        raise HTTPException(status_code=403, detail="Филиал недоступен для этой учётной записи")

    target = await db.get(Organization, int(body.organization_id))
    if target is None:
        raise HTTPException(status_code=404, detail="Организация не найдена")
    if not is_superadmin and not bool(target.is_active):
        raise HTTPException(status_code=403, detail="Подписка приостановлена. Свяжитесь с администратором.")

    request.session["organization_id"] = int(body.organization_id)
    return await _admin_auth_me_payload(request, db)


# ─── Защищённый REST API ─────────────────────────────────

router = APIRouter(
    prefix="/admin",
    tags=["Admin Panel"],
    dependencies=[Depends(require_admin_session_active)],
)


# WebSocket без cookie-сессии (браузер ограничен) — только подписанный токен
ws_router = APIRouter(prefix="/admin", tags=["Admin Panel"])


def _make_naive(dt: datetime | None) -> datetime | None:
    """Убираем tzinfo для корректного сравнения с naive-датами."""
    if dt is None:
        return None
    return dt.replace(tzinfo=None) if dt.tzinfo else dt


# ─── WebSocket (Live-события для админки) ────────────────

def _ws_event_allowed_for_org(event_json: str, claims: AdminWsClaims) -> bool:
    """События с чужим или неизвестным organization_id не отправляем подписчику."""
    try:
        payload = json.loads(event_json)
    except json.JSONDecodeError:
        return False
    data = payload.get("data")
    if not isinstance(data, dict):
        return False
    if "organization_id" not in data:
        return False
    oid = data.get("organization_id")
    if oid is None:
        return False
    try:
        return int(oid) == int(claims.organization_id)
    except (TypeError, ValueError):
        return False


@ws_router.websocket("/ws")
async def admin_websocket(ws: WebSocket, token: str = "") -> None:
    """
    WebSocket для real-time уведомлений в админке.
    Авторизация: query ?token= — подписанный токен из POST /auth/login или GET /auth/me.
    """
    claims = parse_admin_ws_token(token)
    if claims is None:
        await ws.close(code=4003, reason="Unauthorized")
        return
    await ws.accept()
    logger.info("Admin WebSocket подключён org=%s", claims.organization_id)
    try:
        await ws.send_text(json.dumps({"type": "ws_ready", "v": 1}, ensure_ascii=False))
    except Exception:
        return
    try:
        async for event_json in subscribe_events():
            if _ws_event_allowed_for_org(event_json, claims):
                await ws.send_text(event_json)
    except (WebSocketDisconnect, RuntimeError) as exc:
        logger.info("Admin WebSocket отключён: %s", exc)
    except Exception as exc:
        logger.error("WebSocket error: %s", exc)


# ─── Заказы ─────────────────────────────────────────────

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

    out: list[dict] = []
    for o, phone, user_name in rows:
        items_json = o.items_json
        meta = order_meta_from_items_json(items_json if isinstance(items_json, dict) else None)
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


ORDER_TIMELINE_CHAT_LIMIT = 15


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
        event = {"from": cur, "to": want, "at": datetime.now(timezone.utc).isoformat(), "actor": _admin_actor_key(request)}
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

    queued_in_arq = await dispatch_arq_or_background(
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
        "queued": "arq" if queued_in_arq else "background",
        "task": _failed_task_public(t),
    }


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


@router.get("/search")
async def global_search(
    request: Request,
    q: str = Query(..., min_length=1, max_length=80),
    limit: int = Query(12, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Поиск по телефону/имени: заказы, чаты, бронирования (для Ctrl+K)."""
    org_id = admin_org_from_session(request)
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
        .where(_orders_tenant_clause(org_id), User.organization_id == org_id, or_(*o_clauses))
        .order_by(Order.created_at.desc())
        .limit(limit)
    )

    r1 = await db.execute(oq)
    for o, p, nm in r1.all():
        meta = order_meta_from_items_json(o.items_json if isinstance(o.items_json, dict) else None)
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
        .where(
            User.organization_id == org_id,
            or_(User.phone.ilike(term), func.coalesce(User.name, "").ilike(term)),
        )
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
        .where(User.organization_id == org_id, User.phone.ilike(term))
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


@router.get("/integrations/status")
async def integrations_status(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Состояние интеграций для админки: индикаторы iiko / WhatsApp."""
    org_id = admin_org_from_session(request)
    iiko_ok = await _iiko_effective_configured(db, org_id)
    base = await build_status_payload(
        db,
        organization_id=int(org_id),
        iiko_configured=iiko_ok,
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
    base["openai_configured"] = bool(str(settings.openai_api_key or "").strip())
    base["whatsapp_voice_replies_enabled"] = bool(settings.whatsapp_voice_replies)
    base["iiko_secrets_encrypt_ready"] = bool((settings.app_secrets_fernet_key or "").strip())
    org_row = await db.get(Organization, org_id)
    base["prepayment_enforced"] = bool(getattr(org_row, "prepayment_enforced", True)) if org_row else True
    base["auto_send_to_iiko_after_payment"] = bool(
        getattr(org_row, "auto_send_to_iiko_after_payment", False),
    ) if org_row is not None else False
    tg_token_ok = bool(str(settings.telegram_bot_token or "").strip())
    tg_global_chat_ok = bool(str(settings.telegram_admin_chat_id or "").strip())
    tg_org_chat_ok = bool(
        str(getattr(org_row, "telegram_ops_chat_id", "") or "").strip(),
    ) if org_row is not None else False
    base["telegram_configured"] = tg_token_ok and (tg_global_chat_ok or tg_org_chat_ok)
    return base


@router.get("/readiness")
async def admin_readiness(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    """Сводка состояния окружения для вкладки «Состояние» (без секретов)."""
    org_id = admin_org_from_session(request)
    out = await build_admin_readiness_payload(db, org_id)
    out["organization_id"] = org_id
    return out


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


class OrganizationPrefsPatchBody(BaseModel):
    """Настройки филиала (без секретов)."""

    prepayment_enforced: bool | None = Field(
        default=None,
        description="False — не требовать предоплату по порогу; оператор подтверждает оплату вручную",
    )
    auto_send_to_iiko_after_payment: bool | None = Field(
        default=None,
        description="После оплаты (вебхук) автоматически подтвердить заказ и отправить в iiko",
    )


class OrganizationProfilePatchBody(BaseModel):
    """Профиль ресторана/филиала (для UI, без секретов)."""

    name: str | None = Field(default=None, min_length=2, max_length=255)
    timezone: str | None = Field(default=None, max_length=64)
    currency: str | None = Field(default=None, max_length=8)
    whatsapp_phone_number_id: str | None = Field(default=None, max_length=100)
    telegram_ops_chat_id: str | None = Field(
        default=None,
        max_length=32,
        description="Telegram chat_id группы персонала для SOS-алертов (если пусто — TELEGRAM_ADMIN_CHAT_ID из Render)",
    )
    schedule_json: dict[str, object] | None = Field(
        default=None,
        description="Структурированный график работы по дням недели",
    )
    prepayment_legal_text: str | None = Field(
        default=None,
        max_length=8000,
        description="Доп. дисклеймер при предоплате по порогу суммы (показывается гостю в WhatsApp)",
    )


@router.get("/organization/prefs")
async def get_organization_prefs(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    org_id = admin_org_from_session(request)
    org = await db.get(Organization, org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    return {
        "id": org.id,
        "name": org.name,
        "organization_id": org.id,
        "prepayment_enforced": bool(getattr(org, "prepayment_enforced", True)),
        "auto_send_to_iiko_after_payment": bool(getattr(org, "auto_send_to_iiko_after_payment", False)),
    }


@router.patch("/organization/prefs")
async def patch_organization_prefs(
    request: Request,
    body: OrganizationPrefsPatchBody,
    db: AsyncSession = Depends(get_db),
) -> dict:
    org_id = admin_org_from_session(request)
    org = await db.get(Organization, org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    if body.prepayment_enforced is not None:
        org.prepayment_enforced = bool(body.prepayment_enforced)
    if body.auto_send_to_iiko_after_payment is not None:
        org.auto_send_to_iiko_after_payment = bool(body.auto_send_to_iiko_after_payment)
    await db.commit()
    await db.refresh(org)
    return {
        "ok": True,
        "prepayment_enforced": bool(getattr(org, "prepayment_enforced", True)),
        "auto_send_to_iiko_after_payment": bool(getattr(org, "auto_send_to_iiko_after_payment", False)),
    }


@router.get("/organization/profile")
async def get_organization_profile(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    """Данные «Мой ресторан» для текущей админ-сессии."""
    org_id = admin_org_from_session(request)
    org = await db.get(Organization, org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    op = check_operational_status(org.timezone, getattr(org, "schedule_json", None))
    return {
        "id": int(org.id),
        "organization_id": int(org.id),
        "name": org.name,
        "timezone": org.timezone,
        "currency": org.currency,
        "whatsapp_phone_number_id": (org.whatsapp_phone_number_id or "").strip(),
        "telegram_ops_chat_id": (getattr(org, "telegram_ops_chat_id", None) or "").strip(),
        "prepayment_legal_text": (getattr(org, "prepayment_legal_text", None) or "").strip(),
        "schedule_json": parse_schedule_json(getattr(org, "schedule_json", None)).model_dump(mode="json"),
        "operational_label": op.human_label,
        "is_business_open": op.is_business_open,
        "is_kitchen_open": op.is_kitchen_open,
    }


@router.patch("/organization/profile")
async def patch_organization_profile(
    request: Request,
    body: OrganizationProfilePatchBody,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Обновить профиль ресторана/филиала."""
    org_id = admin_org_from_session(request)
    org = await db.get(Organization, org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    if body.name is not None:
        nm = (body.name or "").strip()
        if not nm:
            raise HTTPException(status_code=400, detail="Название не должно быть пустым")
        org.name = nm
    if body.timezone is not None:
        org.timezone = (body.timezone or "").strip() or org.timezone
    if body.currency is not None:
        org.currency = (body.currency or "").strip().upper() or org.currency
    if body.whatsapp_phone_number_id is not None:
        org.whatsapp_phone_number_id = (body.whatsapp_phone_number_id or "").strip()
    if body.telegram_ops_chat_id is not None:
        org.telegram_ops_chat_id = (body.telegram_ops_chat_id or "").strip()
    if body.schedule_json is not None:
        org.schedule_json = parse_schedule_json(body.schedule_json).model_dump(mode="json")
    if body.prepayment_legal_text is not None:
        raw = (body.prepayment_legal_text or "").strip()
        org.prepayment_legal_text = raw if raw else None
    await db.commit()
    await db.refresh(org)
    op = check_operational_status(org.timezone, getattr(org, "schedule_json", None))
    return {
        "ok": True,
        "organization_id": int(org.id),
        "name": org.name,
        "timezone": org.timezone,
        "currency": org.currency,
        "whatsapp_phone_number_id": (org.whatsapp_phone_number_id or "").strip(),
        "telegram_ops_chat_id": (getattr(org, "telegram_ops_chat_id", None) or "").strip(),
        "prepayment_legal_text": (getattr(org, "prepayment_legal_text", None) or "").strip(),
        "schedule_json": parse_schedule_json(getattr(org, "schedule_json", None)).model_dump(mode="json"),
        "operational_label": op.human_label,
        "is_business_open": op.is_business_open,
        "is_kitchen_open": op.is_kitchen_open,
    }


class StaffCreateBody(BaseModel):
    email: str = Field(..., min_length=3, max_length=255)
    role: str = Field(default=StaffRole.OPERATOR.value, description="admin | operator")
    password: str = Field(default="", max_length=128, description="Опционально: если пусто — сгенерируем временный")


@router.get("/staff")
async def list_staff(
    request: Request,
    _perm: None = Depends(require_staff_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    org_id = admin_org_from_session(request)
    rows = (
        await db.execute(
            select(StaffUser)
            .where(StaffUser.organization_id == org_id)
            .order_by(StaffUser.created_at.desc(), StaffUser.id.desc())
        )
    ).scalars().all()
    out: list[dict] = []
    for u in rows:
        out.append(
            {
                "id": int(u.id),
                "email": u.email,
                "role": u.role,
                "is_active": bool(u.is_active),
                "created_at": u.created_at.isoformat() if u.created_at else None,
            }
        )
    return {"ok": True, "users": out}


@router.post("/staff")
async def create_staff(
    request: Request,
    body: StaffCreateBody,
    _perm: None = Depends(require_staff_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    org_id = admin_org_from_session(request)
    email = (body.email or "").strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Укажите корректный email")
    role = (body.role or "").strip().lower() or StaffRole.OPERATOR.value
    if role not in (StaffRole.ADMIN.value, StaffRole.OPERATOR.value):
        raise HTTPException(status_code=400, detail="Некорректная роль")
    pwd = (body.password or "").strip()
    if not pwd:
        # временный пароль: покажем в UI один раз
        pwd = secrets.token_urlsafe(9)
    if len(pwd) < 8:
        raise HTTPException(status_code=400, detail="Пароль должен быть минимум 8 символов")

    exists = await db.scalar(select(StaffUser.id).where(StaffUser.email == email))
    if exists is not None:
        raise HTTPException(status_code=409, detail="Пользователь с таким email уже существует")

    u = StaffUser(
        organization_id=org_id,
        email=email,
        password_hash=hash_password(pwd),
        role=role,
        is_active=True,
    )
    db.add(u)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Не удалось создать пользователя") from None
    return {"ok": True, "user": {"id": int(u.id), "email": u.email, "role": u.role}, "temp_password": pwd}


@router.get("/integrations/events")
async def integrations_events(
    request: Request,
    limit: int = Query(40, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Журнал последних событий синхронизации (меню, стоп-листы)."""
    events = await list_integration_events(
        db, limit=limit, organization_id=admin_org_from_session(request),
    )
    return {"events": events}


class IikoVerifyBody(BaseModel):
    api_login: str = Field(..., min_length=2, description="Ключ apiLogin из iiko Cloud")


@router.post("/integrations/iiko/verify")
async def integrations_iiko_verify(body: IikoVerifyBody) -> dict:
    """Проверить ключ и получить список организаций iiko (ключ не сохраняется)."""
    try:
        orgs = await verify_iiko_api_login(body.api_login)
    except Exception as exc:
        logger.warning("iiko verify failed: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc)[:500]) from exc
    return {"ok": True, "organizations": orgs}


class IikoSetupBody(BaseModel):
    api_login: str = Field(..., min_length=2)
    iiko_organization_id: str = Field(..., min_length=8)
    terminal_group_id: str = ""


@router.post("/integrations/iiko/setup")
async def integrations_iiko_setup(
    request: Request,
    body: IikoSetupBody,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Сохранить ключ (зашифрованно при APP_SECRETS_FERNET_KEY) и импортировать меню филиала."""
    org_id = admin_org_from_session(request)
    enc = bool((settings.app_secrets_fernet_key or "").strip())
    try:
        stats = await setup_organization_iiko(
            db,
            organization_id=org_id,
            api_login_plain=body.api_login.strip(),
            iiko_organization_uuid=body.iiko_organization_id.strip(),
            encrypt_login=enc,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("iiko setup: %s", exc, exc_info=True)
        raise HTTPException(status_code=502, detail=str(exc)[:500]) from exc
    org = await db.get(Organization, org_id)
    if org is not None and (body.terminal_group_id or "").strip():
        org.iiko_terminal_group_id = body.terminal_group_id.strip()
    sk = stats.get("skipped")
    detail_m = (
        f"Синхронизация меню: успешно "
        f"(всего {stats.get('total', 0)}, новых {stats.get('created', 0)}, обновлено {stats.get('updated', 0)}"
        + (f", пропущено {sk}" if sk else "")
        + ")"
    )
    await record_menu_sync(db, True, None, detail=detail_m, organization_id=org_id)
    await db.commit()
    return {"ok": True, "stats": stats, "encrypted": enc}


@router.get("/setup-status")
async def setup_status(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    """Прогресс онбординга (Stripe-style) для текущего филиала."""
    org_id = admin_org_from_session(request)
    org = await db.get(Organization, org_id)
    creds = await resolve_org_iiko_credentials(db, org_id)
    iiko_ok = creds is not None
    menu_n = int(
        await db.scalar(
            select(func.count()).select_from(MenuItem).where(MenuItem.organization_id == org_id),
        )
        or 0,
    )
    packaging_n = int(
        await db.scalar(
            select(func.count()).select_from(PackagingRule).where(
                PackagingRule.organization_id == org_id,
                PackagingRule.is_active.is_(True),
            ),
        )
        or 0,
    )
    org_wa = ((org.whatsapp_phone_number_id if org else None) or "").strip()
    wa_ok = bool(org_wa) or (
        bool((settings.whatsapp_phone_number_id or "").strip())
        and bool((settings.whatsapp_api_token or "").strip())
    )
    rules_n = int(
        await db.scalar(
            select(func.count()).select_from(UpsellRule).where(
                UpsellRule.organization_id == org_id,
                UpsellRule.is_active.is_(True),
            ),
        )
        or 0,
    )
    kb_n = int(
        await db.scalar(
            select(func.count()).select_from(KnowledgeItem).where(
                KnowledgeItem.organization_id == org_id,
                KnowledgeItem.is_active.is_(True),
            ),
        )
        or 0,
    )
    # Шесть равных «весов»: 100% только когда закрыты все пункты (см. подсказки в админке).
    step_rows: list[tuple[str, str, bool, str, str]] = [
        (
            "iiko",
            "iiko подключён",
            iiko_ok,
            "connections",
            "«Подключения»: API-ключ iiko Cloud и сохранение с выбором организации.",
        ),
        (
            "menu",
            "Меню импортировано",
            menu_n > 0,
            "connections",
            "«Подключения»: импорт меню (после iiko — «Сохранить и импортировать» или синхронизация). Нужен хотя бы один пункт.",
        ),
        (
            "whatsapp",
            "WhatsApp (номер или .env)",
            wa_ok,
            "connections",
            "«Подключения»: Phone Number ID в форме или WHATSAPP_PHONE_NUMBER_ID и WHATSAPP_API_TOKEN в .env.",
        ),
        (
            "packaging",
            "Правила упаковки",
            packaging_n > 0,
            "restaurant",
            "«Мой ресторан»: добавьте активное правило упаковки (надбавка за контейнер и т.п.).",
        ),
        (
            "upsell",
            "Правила допродаж",
            rules_n > 0,
            "smart_sales",
            "«Умные продажи»: хотя бы одно активное правило допродаж.",
        ),
        (
            "knowledge",
            "База знаний",
            kb_n > 0,
            "restaurant",
            "«Мой ресторан», блок «База знаний»: хотя бы одна активная запись для гостей и бота.",
        ),
    ]
    steps = [
        {
            "id": sid,
            "label": label,
            "done": done,
            "open_tab": tab,
            "hint": hint,
        }
        for sid, label, done, tab, hint in step_rows
    ]
    score = int(round(100 * sum(1 for s in steps if s["done"]) / max(len(steps), 1)))
    return {
        "score": score,
        "steps": steps,
        "menu_items": menu_n,
        "packaging_rules": packaging_n,
        "upsell_rules": rules_n,
        "knowledge_items": kb_n,
    }


@router.post("/integrations/sync")
async def integrations_sync_now(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Принудительно: номенклатура + стоп-листы из iiko (настройки филиала или .env).
    """
    org_id = admin_org_from_session(request)
    creds = await resolve_org_iiko_credentials(db, org_id)
    if creds is None:
        raise HTTPException(
            status_code=400,
            detail="Настройте iiko для филиала (ключ и организация) или задайте IIKO_* в .env",
        )

    menu_block: dict = {"ok": False, "stats": None, "error": None}
    stop_block: dict = {"ok": False, "stats": None, "error": None}

    try:
        stats_m = await sync_menu_from_iiko(
            db,
            creds.api_login,
            creds.iiko_organization_id,
            restomind_organization_id=org_id,
        )
        invalidate_menu_context_cache(org_id)
        menu_block = {"ok": True, "stats": stats_m, "error": None}
        sk = stats_m.get("skipped")
        detail_m = (
            f"Синхронизация меню: успешно "
            f"(всего {stats_m.get('total', 0)}, новых {stats_m.get('created', 0)}, обновлено {stats_m.get('updated', 0)}"
            + (f", пропущено {sk}" if sk else "")
            + ")"
        )
        await record_menu_sync(db, True, None, detail=detail_m, organization_id=org_id)
    except Exception as exc:
        err = str(exc)
        logger.error("Ручная синхронизация меню iiko: %s", exc, exc_info=True)
        menu_block = {"ok": False, "stats": None, "error": err}
        await record_menu_sync(
            db, False, err, detail=f"Синхронизация меню: ошибка — {err[:400]}", organization_id=org_id,
        )

    try:
        stats_s = await sync_stop_lists(
            db,
            creds.api_login,
            creds.iiko_organization_id,
            terminal_group_id=creds.terminal_group_id or None,
            menu_organization_id=org_id,
        )
        stop_block = {"ok": True, "stats": stats_s, "error": None}
        detail_s = (
            f"Стоп-листы: успешно "
            f"(в стопе: {stats_s.get('stopped', 0)}, восстановлено: {stats_s.get('restored', 0)})"
        )
        await record_stoplist_sync(db, True, None, detail=detail_s, organization_id=org_id)
    except Exception as exc:
        err = str(exc)
        logger.error("Ручная синхронизация стоп-листов iiko: %s", exc, exc_info=True)
        stop_block = {"ok": False, "stats": None, "error": err}
        await record_stoplist_sync(
            db, False, err, detail=f"Стоп-листы: ошибка — {err[:400]}", organization_id=org_id,
        )

    if not menu_block["ok"] and not stop_block["ok"]:
        raise HTTPException(
            status_code=502,
            detail=f"Меню: {menu_block['error']}; стоп-листы: {stop_block['error']}",
        )

    snap = await build_status_payload(
        db,
        organization_id=int(org_id),
        iiko_configured=await _iiko_effective_configured(db, org_id),
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
    request: Request,
    status: str | None = Query(None, description="Фильтр по статусу (pending, confirmed, cancelled)"),
    q: str | None = Query(None, description="Поиск по телефону клиента"),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Список бронирований."""
    org_id = admin_org_from_session(request)
    query = (
        select(Booking, User.phone, User.name, Order.id)
        .join(User, Booking.user_id == User.id)
        .outerjoin(Order, Order.booking_id == Booking.id)
        .where(User.organization_id == org_id)
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
    request: Request,
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

    org_id = admin_org_from_session(request)
    result = await db.execute(
        select(Booking)
        .join(User, User.id == Booking.user_id)
        .where(Booking.id == booking_id, User.organization_id == org_id),
    )
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


def _parse_dt(value: object) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _admin_actor_key(request: Request) -> str:
    sid = request.session.get("staff_id")
    if sid is not None:
        return f"staff:{sid}"
    email = request.session.get("email") or request.session.get("staff_email")
    if email:
        return f"staff:{email}"
    return "staff:session"


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


@router.get("/chats")
async def list_chats_sidebar(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    cursor_at: str | None = Query(None, description="Cursor: lastAt ISO (for infinite scroll)"),
    cursor_id: int | None = Query(None, ge=1, description="Cursor: last message id (tie-breaker)"),
    mode: Literal["active", "mine", "closed", "snoozed", "all"] = Query("active"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Список диалогов для боковой панели админки: телефон, превью последнего сообщения, время.
    Infinite scroll: курсор по (lastAt, last_id). Без full-scan chat_logs.
    """
    org_id = admin_org_from_session(request)

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
        .where(User.organization_id == org_id)
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
    staff_key = _admin_actor_key(request)
    now = datetime.now(timezone.utc)
    for r in rows:
        phone = r.phone
        if not phone:
            continue
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
            }
        )
    if chats:
        last = rows[-1]
        if last.last_at is not None:
            next_cursor = {"cursor_at": last.last_at.isoformat(), "cursor_id": int(last.log_id)}
        else:
            next_cursor = {"cursor_at": None, "cursor_id": int(last.log_id)}

    return {"chats": chats, "has_more": has_more, "next_cursor": next_cursor}


@router.get("/chats/{phone}")
async def get_chat_log(
    request: Request,
    phone: str,
    limit: int = Query(50, ge=1, le=200),
    before_id: int | None = Query(None, ge=1, description="Cursor: load older messages with id < before_id"),
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

    stmt = select(ChatLog).where(ChatLog.user_id == user.id)
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
        "messages": [
            {
                "id": log.id,
                "role": log.role,
                "content": log.content,
                "created_at": log.created_at.isoformat() if log.created_at else None,
                "meta": log.meta_json if isinstance(log.meta_json, dict) else None,
                "provider_message_id": log.provider_message_id,
                "delivery_status": log.delivery_status,
                "error_details": log.error_details if isinstance(log.error_details, dict) else None,
                "status_updated_at": log.status_updated_at.isoformat() if log.status_updated_at else None,
            }
            for log in reversed(list(logs))
        ],
    }


class CustomerNoteBody(BaseModel):
    """Тело запроса: заметка оператора о клиенте."""

    note: str = ""


@router.get("/customers/{phone}/summary")
async def customer_summary(
    request: Request,
    phone: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Сводка по клиенту для панели оператора: заказы, выручка, заметка, «чёрный список» (is_active).
    Если пользователя с таким телефоном ещё нет в БД — возвращаются нули (диалог только откроют вручную).
    """
    org_id = admin_org_from_session(request)
    user = await db.scalar(
        select(User).where(User.phone == phone, User.organization_id == org_id),
    )
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
        OrderStatus.IN_TRANSIT.value,
        OrderStatus.WAITING_PICKUP.value,
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
    request: Request,
    phone: str,
    body: CustomerNoteBody,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Сохранить внутреннюю заметку оператора о клиенте."""
    org_id = admin_org_from_session(request)
    user = await db.scalar(
        select(User).where(User.phone == phone, User.organization_id == org_id),
    )
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
    request: Request,
    phone: str,
    body: AiPauseBody,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Заблокировать ИИ для номера: бот не отвечает, пока не снять блокировку.
    Дублирует смысл «перехвата», но сохраняется в БД (переживает рестарт Redis).
    """
    org_id = admin_org_from_session(request)
    user = await db.scalar(
        select(User).where(User.phone == phone, User.organization_id == org_id),
    )
    if user is None:
        raise HTTPException(
            status_code=404,
            detail="Пользователь не найден — сначала должен быть диалог или заказ",
        )
    user.ai_paused = body.paused
    await db.flush()
    await db.commit()

    if body.paused:
        await set_user_state(redis_client, phone, UserState.HUMAN_MODE, organization_id=org_id)
        await publish_event(
            "state_changed",
            {"phone": phone, "state": UserState.HUMAN_MODE.value, "organization_id": org_id},
        )
    else:
        await set_user_state(redis_client, phone, UserState.CHATTING, organization_id=org_id)
        await publish_event(
            "state_changed",
            {"phone": phone, "state": UserState.CHATTING.value, "organization_id": org_id},
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
        "tags": item.tags or "",
        "portion_kind": getattr(item, "portion_kind", None) or "single",
        "serves_min": int(getattr(item, "serves_min", None) or 1),
        "serves_max": int(getattr(item, "serves_max", None) or 1),
        "allergens": getattr(item, "allergens", None) or "",
        "ingredients_summary": getattr(item, "ingredients_summary", None) or "",
        "dietary_tags": getattr(item, "dietary_tags", None) or "",
        "upsell_pairs": getattr(item, "upsell_pairs", None) or "",
        "price": float(item.price),
        "is_available": item.is_available,
        "image_url": item.image_url,
    }


class MenuItemPatchBody(BaseModel):
    """Частичное обновление позиции (только переданные поля)."""

    name: str | None = Field(None, min_length=1, max_length=255)
    category: str | None = Field(None, max_length=100)
    description: str | None = None
    tags: str | None = None
    portion_kind: str | None = Field(None, description="single | shareable")
    serves_min: int | None = Field(None, ge=1, le=99)
    serves_max: int | None = Field(None, ge=1, le=99)
    allergens: str | None = None
    ingredients_summary: str | None = None
    dietary_tags: str | None = None
    upsell_pairs: str | None = None
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
    tags: str = ""
    portion_kind: str = Field(default="single", description="single | shareable")
    serves_min: int = Field(default=1, ge=1, le=99)
    serves_max: int = Field(default=1, ge=1, le=99)
    allergens: str = ""
    ingredients_summary: str = ""
    dietary_tags: str = ""
    upsell_pairs: str = ""
    price: float = Field(0, ge=0)
    is_available: bool = True
    image_url: str | None = Field(None, max_length=500)


@router.get("/menu")
async def list_menu(
    request: Request,
    category: str | None = Query(None, description="Фильтр по категории"),
    available_only: bool = Query(True),
    stopped_only: bool = Query(
        False,
        description="Только позиции в стопе (is_available=false); при True игнорируется available_only",
    ),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Список позиций меню."""
    org_id = admin_org_from_session(request)
    query = (
        select(MenuItem)
        .where(_menu_tenant_clause(org_id))
        .order_by(MenuItem.category, MenuItem.name)
    )
    if category:
        query = query.where(MenuItem.category == category)
    if stopped_only:
        query = query.where(MenuItem.is_available.is_(False))
    elif available_only:
        query = query.where(MenuItem.is_available.is_(True))

    result = await db.execute(query)
    items = result.scalars().all()

    if stopped_only:
        logger.info(
            "Admin stoplist fetch org=%s: returned=%d (stopped_only=true)",
            org_id,
            len(items),
        )

    return {
        "count": len(items),
        # Backward-compat: старый админский UI мог ожидать ключ `menu_items`.
        "items": [_menu_item_dict(item) for item in items],
        "menu_items": [_menu_item_dict(item) for item in items],
    }


@router.post("/menu")
async def create_menu_item(
    request: Request,
    body: MenuItemCreateBody,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Добавить позицию меню вручную (iiko_id генерируется локально)."""
    org_id = admin_org_from_session(request)
    pk = (body.portion_kind or "single").strip().lower()
    if pk not in ("single", "shareable"):
        pk = "single"
    smin = max(1, min(99, int(body.serves_min)))
    smax = max(1, min(99, int(body.serves_max)))
    if smax < smin:
        smax = smin
    item = MenuItem(
        organization_id=org_id,
        name=body.name.strip(),
        category=(body.category or "").strip(),
        description=(body.description or "").strip(),
        tags=(body.tags or "").strip(),
        portion_kind=pk,
        serves_min=smin,
        serves_max=smax,
        allergens=(body.allergens or "").strip(),
        ingredients_summary=(body.ingredients_summary or "").strip(),
        dietary_tags=(body.dietary_tags or "").strip(),
        upsell_pairs=(body.upsell_pairs or "").strip(),
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
    request: Request,
    item_id: int,
    body: MenuItemPatchBody,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Изменить поля позиции меню (цена, стоп-лист, название и т.д.)."""
    org_id = admin_org_from_session(request)
    item = await _menu_item_in_org(db, item_id, org_id)

    data = body.model_dump(exclude_unset=True)
    if "name" in data and data["name"] is not None:
        data["name"] = data["name"].strip()
    if "category" in data and data["category"] is not None:
        data["category"] = data["category"].strip()
    if "description" in data and data["description"] is not None:
        data["description"] = data["description"].strip()
    if "tags" in data and data["tags"] is not None:
        data["tags"] = data["tags"].strip()
    if "portion_kind" in data and data["portion_kind"] is not None:
        pk = str(data["portion_kind"]).strip().lower()
        data["portion_kind"] = pk if pk in ("single", "shareable") else "single"
    if "serves_min" in data and data["serves_min"] is not None:
        data["serves_min"] = max(1, min(99, int(data["serves_min"])))
    if "serves_max" in data and data["serves_max"] is not None:
        data["serves_max"] = max(1, min(99, int(data["serves_max"])))
    if "allergens" in data and data["allergens"] is not None:
        data["allergens"] = str(data["allergens"]).strip()
    if "ingredients_summary" in data and data["ingredients_summary"] is not None:
        data["ingredients_summary"] = str(data["ingredients_summary"]).strip()
    if "dietary_tags" in data and data["dietary_tags"] is not None:
        data["dietary_tags"] = str(data["dietary_tags"]).strip()
    if "upsell_pairs" in data and data["upsell_pairs"] is not None:
        data["upsell_pairs"] = str(data["upsell_pairs"]).strip()
    if "image_url" in data:
        url = (data["image_url"] or "").strip()
        data["image_url"] = url if url else None

    for key, value in data.items():
        setattr(item, key, value)
    if hasattr(item, "serves_min") and hasattr(item, "serves_max"):
        if int(item.serves_max or 1) < int(item.serves_min or 1):
            item.serves_max = int(item.serves_min or 1)

    await db.flush()
    invalidate_menu_context_cache(org_id)
    return {"ok": True, "item": _menu_item_dict(item)}


@router.post("/menu/clear")
async def clear_all_menu_items(
    request: Request,
    body: ClearMenuBody,
    _perm: None = Depends(require_superadmin),
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
    org_id = admin_org_from_session(request)
    cnt = (
        await db.scalar(
            select(func.count()).select_from(MenuItem).where(MenuItem.organization_id == org_id),
        )
        or 0
    )
    await db.execute(sql_delete(MenuItem).where(MenuItem.organization_id == org_id))
    await db.flush()
    logger.warning("Админ: полная очистка menu_items, удалено позиций: %d", cnt)
    return {"ok": True, "deleted": int(cnt)}


@router.delete("/menu/{item_id}")
async def delete_menu_item(
    request: Request,
    item_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Удалить позицию из меню (осторожно: старые заказы ссылаются на названия в JSON)."""
    org_id = admin_org_from_session(request)
    item = await _menu_item_in_org(db, item_id, org_id)
    await db.execute(sql_delete(MenuItem).where(MenuItem.id == item_id))
    await db.flush()
    return {"ok": True, "id": item_id}


# ─── База знаний (FAQ заведения для промпта LLM / OpenAI) ──────


def _knowledge_item_dict(row: KnowledgeItem) -> dict:
    return {
        "id": row.id,
        "organization_id": row.organization_id,
        "knowledge_kind": getattr(row, "knowledge_kind", None) or "facility",
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
    organization_id: int | None = Field(
        None,
        description="Игнорируется: запись создаётся в филиале текущей сессии (multi-tenant).",
    )
    knowledge_kind: str = Field(
        "facility",
        description="facility — справочник заведения; persona — тон и характер бота",
    )


class KnowledgeItemPatchBody(BaseModel):
    category: str | None = Field(None, max_length=120)
    question: str | None = Field(None, min_length=1, max_length=500)
    answer: str | None = Field(None, min_length=1, max_length=50_000)
    is_active: bool | None = None
    sort_order: int | None = Field(None, ge=-10_000, le=10_000)
    organization_id: int | None = None
    knowledge_kind: str | None = Field(None, description="facility | persona")


@router.get("/knowledge")
async def list_knowledge_items(
    request: Request,
    db: AsyncSession = Depends(get_db),
    active_only: bool = Query(False, description="Только is_active=true"),
) -> dict:
    """Список записей базы знаний для админки."""
    org_id = admin_org_from_session(request)
    q = (
        select(KnowledgeItem)
        .where(_knowledge_tenant_clause(org_id))
        .order_by(KnowledgeItem.sort_order, KnowledgeItem.id)
    )
    if active_only:
        q = q.where(KnowledgeItem.is_active.is_(True))
    result = await db.execute(q)
    rows = list(result.scalars().all())
    return {"items": [_knowledge_item_dict(r) for r in rows]}


@router.post("/knowledge")
async def create_knowledge_item(
    request: Request,
    body: KnowledgeItemCreateBody,
    db: AsyncSession = Depends(get_db),
) -> dict:
    org_id = admin_org_from_session(request)
    kk_raw = (body.knowledge_kind or "facility").strip().lower()
    kk = "persona" if kk_raw == "persona" else "facility"
    row = KnowledgeItem(
        organization_id=org_id,
        knowledge_kind=kk[:32],
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
    request: Request,
    item_id: int,
    body: KnowledgeItemPatchBody,
    db: AsyncSession = Depends(get_db),
) -> dict:
    org_id = admin_org_from_session(request)
    row = await db.get(KnowledgeItem, item_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    if row.organization_id is None and org_id != int(settings.default_organization_id):
        raise HTTPException(status_code=404, detail="Запись не найдена")
    if row.organization_id is not None and int(row.organization_id) != org_id:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    data = body.model_dump(exclude_unset=True)
    # Запрет переноса записи между филиалами через PATCH.
    data.pop("organization_id", None)
    if "category" in data and data["category"] is not None:
        data["category"] = data["category"].strip()
    if "question" in data and data["question"] is not None:
        data["question"] = data["question"].strip()
    if "answer" in data and data["answer"] is not None:
        data["answer"] = data["answer"].strip()
    if "knowledge_kind" in data and data["knowledge_kind"] is not None:
        kk = str(data["knowledge_kind"]).strip().lower()
        data["knowledge_kind"] = kk if kk == "persona" else "facility"
    for key, value in data.items():
        setattr(row, key, value)
    await db.flush()
    return {"ok": True, "item": _knowledge_item_dict(row)}


@router.delete("/knowledge/{item_id}")
async def delete_knowledge_item(
    request: Request,
    item_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await _delete_knowledge_item_impl(request, item_id, db)


@router.post("/knowledge/{item_id}/delete")
async def delete_knowledge_item_post(
    request: Request,
    item_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    То же, что DELETE /knowledge/{id}: часть хостингов/прокси режет метод DELETE.
    """
    return await _delete_knowledge_item_impl(request, item_id, db)


async def _delete_knowledge_item_impl(request: Request, item_id: int, db: AsyncSession) -> dict:
    org_id = admin_org_from_session(request)
    row = await db.get(KnowledgeItem, item_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    if row.organization_id is None and org_id != int(settings.default_organization_id):
        raise HTTPException(status_code=404, detail="Запись не найдена")
    if row.organization_id is not None and int(row.organization_id) != org_id:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    await db.execute(sql_delete(KnowledgeItem).where(KnowledgeItem.id == item_id))
    await db.flush()
    return {"ok": True, "id": item_id}


@router.post("/menu/sync")
async def sync_menu(
    request: Request,
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
    Учётные данные из query, из настроек филиала или из .env. Совпадение по ``iiko_id``.
    """
    org_id = admin_org_from_session(request)
    login, org, _tg = await _iiko_login_org_for_tenant(db, org_id, api_login, organization_id)
    try:
        stats = await sync_menu_from_iiko(
            db, login, org, restomind_organization_id=org_id,
        )
        invalidate_menu_context_cache(org_id)
        sk = stats.get("skipped")
        detail_m = (
            f"Синхронизация меню: успешно "
            f"(всего {stats.get('total', 0)}, новых {stats.get('created', 0)}, обновлено {stats.get('updated', 0)}"
            + (f", пропущено {sk}" if sk else "")
            + ")"
        )
        await record_menu_sync(
            db, True, None, detail=detail_m, organization_id=admin_org_from_session(request),
        )
        return {"ok": True, "status": "ok", **stats}
    except Exception as exc:
        err = str(exc)
        logger.error("Ошибка синхронизации меню: %s", exc, exc_info=True)
        await record_menu_sync(
            db, False, err, detail=f"Синхронизация меню: ошибка — {err[:400]}",
            organization_id=admin_org_from_session(request),
        )
        raise HTTPException(status_code=502, detail=f"Ошибка при обращении к iiko: {err}")


@router.post("/menu/reindex-embeddings")
async def post_menu_reindex_embeddings(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    E12: пересчитать эмбеддинги позиций меню (нужен OPENAI_API_KEY и MENU_RAG_ENABLED на сервере для выборки в промпте).
    """
    org_id = admin_org_from_session(request)
    out = await reindex_organization_menu_embeddings(db, org_id)
    err = out.get("error")
    if err:
        raise HTTPException(status_code=502, detail=str(err))
    await db.commit()
    stats = {k: v for k, v in out.items() if k != "error"}
    return {"ok": True, "embedding_stats": stats}


@router.post("/menu/stop-lists")
async def sync_stop_lists_endpoint(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Синхронизация стоп-листов для **текущего филиала** (учётка из настроек org).
    Раньше принимались произвольные api_login/organization_id (риск cross-tenant).
    """
    org_id = admin_org_from_session(request)
    login, org, tg = await _iiko_login_org_for_tenant(db, org_id, None, None)
    try:
        stats = await sync_stop_lists(
            db, login, org, terminal_group_id=tg, menu_organization_id=org_id,
        )
        detail_s = (
            f"Стоп-листы: успешно "
            f"(в стопе: {stats.get('stopped', 0)}, восстановлено: {stats.get('restored', 0)})"
        )
        await record_stoplist_sync(
            db, True, None, detail=detail_s, organization_id=org_id,
        )
        snap = await build_status_payload(
            db,
            organization_id=int(org_id),
            iiko_configured=await _iiko_effective_configured(db, org_id),
            whatsapp_configured=_whatsapp_env_configured(),
        )
        return {"ok": True, "status": "ok", **stats, "integration_status": snap}
    except Exception as exc:
        err = str(exc)
        logger.error("Ошибка синхронизации стоп-листов: %s", exc, exc_info=True)
        await record_stoplist_sync(
            db, False, err, detail=f"Стоп-листы: ошибка — {err[:400]}",
            organization_id=org_id,
        )
        raise HTTPException(status_code=502, detail=f"Ошибка при обращении к iiko: {err}")


@router.post("/stop-lists/sync")
async def sync_stop_lists_from_env(
    request: Request,
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
    Синхронизация стоп-листов iiko → флаги is_available в menu_items (филиал, .env или query).
    """
    org_id = admin_org_from_session(request)
    login, org, tg = await _iiko_login_org_for_tenant(db, org_id, api_login, organization_id)
    try:
        stats = await sync_stop_lists(
            db, login, org, terminal_group_id=tg, menu_organization_id=org_id,
        )
        detail_s = (
            f"Стоп-листы: успешно "
            f"(в стопе: {stats.get('stopped', 0)}, восстановлено: {stats.get('restored', 0)})"
        )
        await record_stoplist_sync(
            db, True, None, detail=detail_s, organization_id=org_id,
        )
        snap = await build_status_payload(
            db,
            organization_id=int(org_id),
            iiko_configured=await _iiko_effective_configured(db, org_id),
            whatsapp_configured=_whatsapp_env_configured(),
        )
        logger.info("Синхронизация стоп-листов из админки: %s", stats)
        return {"ok": True, "status": "ok", **stats, "integration_status": snap}
    except Exception as exc:
        err = str(exc)
        logger.error("Ошибка синхронизации стоп-листов (.env): %s", exc, exc_info=True)
        await record_stoplist_sync(
            db, False, err, detail=f"Стоп-листы: ошибка — {err[:400]}",
            organization_id=admin_org_from_session(request),
        )
        raise HTTPException(status_code=502, detail=f"Ошибка при обращении к iiko: {err}")


# ─── Демо-данные (админка) ──────────────────────────────


@router.get("/demo/status")
async def demo_status(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    """Есть ли в БД пакет демо-пользователей (префикс телефона)."""
    oid = admin_org_from_session(request)
    return {"has_demo": await demo_data_exists(db, organization_id=oid)}


@router.post("/demo/seed")
async def demo_seed(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    """Заполнить БД фальшивыми заказами, бронями и чатами (идемпотентно)."""
    oid = admin_org_from_session(request)
    stats = await seed_demo_data(db, organization_id=oid)
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


async def _demo_delete_core(db: AsyncSession, organization_id: int) -> dict:
    """Общая логика удаления демо (БД + Redis-ключи сессий)."""
    if not await demo_data_exists(db, organization_id=organization_id):
        raise HTTPException(status_code=404, detail="Демо-данных нет")
    cleared = await clear_demo_data(db, organization_id=organization_id)
    return {"ok": True, **cleared}


@router.delete("/demo")
async def demo_delete(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    """Удалить всех демо-пользователей и связанные заказы/брони/логи."""
    return await _demo_delete_core(db, admin_org_from_session(request))


@router.post("/demo/delete")
async def demo_delete_post(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    """
    То же, что DELETE /admin/demo.
    Нужен для сред, где HTTP DELETE режется прокси/CDN (удаление «не работает», а POST проходит).
    """
    return await _demo_delete_core(db, admin_org_from_session(request))


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


async def _clear_redis_pending_if_matches(
    phone: str | None,
    order_id: int,
    organization_id: int | None = None,
) -> None:
    """Если в Redis висит черновик этого заказа — снять, чтобы клиент не застрял на мёртвом id."""
    if not phone:
        return
    try:
        pid = await get_pending_order(redis_client, phone, organization_id=organization_id)
        if pid == order_id:
            await clear_pending_order(redis_client, phone, organization_id=organization_id)
    except Exception:
        logger.exception("Redis: не удалось сбросить pending_order для %s", phone)


@router.post("/settings/purge-operational-data")
async def purge_operational_data(
    request: Request,
    body: PurgeOperationalBody,
    _perm: None = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Удалить операционные записи **текущего филиала** (сессия): ``chat_logs``, ``orders``,
    ``bookings``, ``escalation_events``, ``integration_events``, ``failed_tasks``.

    Таблицы ``users``, ``menu_items``, ``organizations`` **не** трогаются.
    ``integration_health`` (id=1) — глобальный singleton для всей платформы, при очистке
    одного филиала **не** сбрасывается.

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

    org_id = admin_org_from_session(request)

    r_chat = await db.execute(sql_delete(ChatLog).where(ChatLog.organization_id == org_id))
    r_ord = await db.execute(sql_delete(Order).where(_orders_tenant_clause(org_id)))
    r_book = await db.execute(sql_delete(Booking).where(_bookings_tenant_clause(org_id)))
    r_esc = await db.execute(sql_delete(EscalationEvent).where(_escalation_tenant_clause(org_id)))
    r_int = await db.execute(sql_delete(IntegrationEvent).where(_integration_events_tenant_clause(org_id)))
    r_ft = await db.execute(sql_delete(FailedTask).where(_failed_tasks_tenant_clause(org_id)))

    await db.commit()
    logger.warning(
        "Админ: сброс операционных данных филиала org_id=%s (чаты/заказы/брони/эскалации/интеграции/failed_tasks)",
        org_id,
    )
    return {
        "ok": True,
        "organization_id": org_id,
        "chat_logs_deleted": _sql_delete_rowcount(r_chat),
        "orders_deleted": _sql_delete_rowcount(r_ord),
        "bookings_deleted": _sql_delete_rowcount(r_book),
        "escalation_events_deleted": _sql_delete_rowcount(r_esc),
        "integration_events_deleted": _sql_delete_rowcount(r_int),
        "failed_tasks_deleted": _sql_delete_rowcount(r_ft),
    }


@router.post("/settings/clear-menu-and-stop-snapshot")
async def clear_menu_and_stop_snapshot(
    request: Request,
    body: ClearMenuBody,
    _perm: None = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Удалить строки ``menu_items`` с ``organization_id`` текущего филиала (как ``POST /menu/clear``).

    Строки с ``organization_id IS NULL`` (legacy) **не** трогаем — иначе франшиза сотрёт общую
    номенклатуру платформы. Отдельной таблицы стоп-листа в БД нет. ``integration_health``
    глобальный — не сбрасываем, чтобы не ломать индикаторы других филиалов.
    """
    if not body.confirm:
        raise HTTPException(
            status_code=400,
            detail='Для очистки передайте в теле JSON: {"confirm": true}',
        )
    org_id = admin_org_from_session(request)
    cnt = (
        await db.scalar(
            select(func.count()).select_from(MenuItem).where(MenuItem.organization_id == org_id),
        )
        or 0
    )
    await db.execute(sql_delete(MenuItem).where(MenuItem.organization_id == org_id))
    await db.commit()
    logger.warning(
        "Админ: очистка menu_items филиала org_id=%s, позиций: %d",
        org_id,
        int(cnt),
    )
    return {"ok": True, "organization_id": org_id, "menu_items_deleted": int(cnt)}


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
async def settings_environment(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    """
    Безопасный снимок окружения для админки (без секретов и полных токенов).
    """
    org_id = admin_org_from_session(request)
    integ = await build_status_payload(
        db,
        organization_id=int(org_id),
        iiko_configured=await _iiko_effective_configured(db, org_id),
        whatsapp_configured=_whatsapp_env_configured(),
    )
    org_row = await db.get(Organization, org_id)
    tg_token_ok = bool(str(settings.telegram_bot_token or "").strip())
    tg_global_chat_ok = bool(str(settings.telegram_admin_chat_id or "").strip())
    tg_org_chat_ok = bool(
        str(getattr(org_row, "telegram_ops_chat_id", "") or "").strip(),
    ) if org_row is not None else False
    telegram_staff_reachable = tg_token_ok and (tg_global_chat_ok or tg_org_chat_ok)
    return {
        "app_name": settings.app_name,
        "app_version": settings.app_version,
        "app_debug": settings.app_debug,
        "app_environment": settings.app_environment,
        "is_prod_like": settings.is_prod_like,
        "db_mode": settings.db_mode,
        "redis_enabled": settings.redis_enabled,
        "redis_memory_only": settings.redis_memory_only,
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
                "configured": telegram_staff_reachable,
                "bot_token_set": tg_token_ok,
                "default_chat_set": tg_global_chat_ok,
                "org_chat_set": tg_org_chat_ok,
            },
            "openai": {"configured": bool(str(settings.openai_api_key or "").strip())},
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
async def redis_purge_phone(
    request: Request,
    body: RedisPurgePhoneBody,
    _perm: None = Depends(require_superadmin),
) -> dict:
    """Удалить из Redis/in-memory ключи chat:history, user:state, pending_order/booking для номера."""
    if not body.confirm:
        raise HTTPException(
            status_code=400,
            detail='Передайте {"confirm": true, "phone": "+7700..."}',
        )
    phone = (body.phone or "").strip()
    if not phone:
        raise HTTPException(status_code=400, detail="Укажите телефон")
    await purge_all_session_keys_for_phone(
        redis_client, phone, organization_id=admin_org_from_session(request),
    )
    logger.warning("Админ: сброшена Redis-сессия для %s", phone[:6] + "…")
    return {"ok": True, "phone": phone}


@router.post("/settings/chat-logs/run-retention")
async def run_chat_log_retention_manual(
    body: RetentionRunBody,
    _perm: None = Depends(require_superadmin),
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


@router.get("/export/orders")
async def export_orders_csv(
    request: Request,
    date_from: date | None = Query(None, description="Начало периода (UTC, дата)"),
    date_to: date | None = Query(None, description="Конец периода включительно (UTC, дата)"),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """CSV заказов за период (UTF-8 с BOM для Excel)."""
    org_id = admin_org_from_session(request)
    lo, hi_excl = _export_range_utc(date_from, date_to)
    lo_sql = _sql_dt_for_filter(lo)
    hi_sql = _sql_dt_for_filter(hi_excl)

    res = await db.execute(
        select(Order, User.phone, User.name)
        .join(User, Order.user_id == User.id)
        .where(
            _orders_tenant_clause(org_id),
            User.organization_id == org_id,
            Order.created_at >= lo_sql,
            Order.created_at < hi_sql,
        )
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
        meta = order_meta_from_items_json(o.items_json if isinstance(o.items_json, dict) else None)
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
    request: Request,
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """CSV сообщений chat_logs за период (роль, телефон клиента)."""
    org_id = admin_org_from_session(request)
    lo, hi_excl = _export_range_utc(date_from, date_to)
    lo_sql = _sql_dt_for_filter(lo)
    hi_sql = _sql_dt_for_filter(hi_excl)

    res = await db.execute(
        select(ChatLog, User.phone)
        .join(User, ChatLog.user_id == User.id)
        .where(
            User.organization_id == org_id,
            ChatLog.created_at >= lo_sql,
            ChatLog.created_at < hi_sql,
        )
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

    yesterday_start = today_start - timedelta(days=1)
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
    metrics = await aggregate_org_window(db, org_id, today_start, now_utc)
    cur = (getattr(org, "currency", None) or "KZT") if org is not None else "KZT"
    narrative = build_today_narrative_ru(metrics, str(cur))
    tz = (getattr(org, "timezone", None) or "UTC") if org is not None else "UTC"
    achievements = await build_achievements_week(db, org_id, str(tz))
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
    Activity feed для CEO/Staff: последние события, читаемые “за 1 секунду”.
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


# ─── Human Override (Перехват диалога) ───────────────────


@router.post("/chats/{phone}/takeover")
async def takeover_chat(request: Request, phone: str, db: AsyncSession = Depends(get_db)) -> dict:
    """
    Перехватить диалог — AI замолкает, оператор ведёт общение вручную.
    Устанавливает флаг HUMAN_MODE в Redis.
    """
    org_id = admin_org_from_session(request)
    await set_user_state(redis_client, phone, UserState.HUMAN_MODE, organization_id=org_id)
    await publish_event("state_changed", {
        "phone": phone,
        "state": UserState.HUMAN_MODE,
        "organization_id": org_id,
    })
    try:
        await _save_chat_triage(
            request,
            db,
            phone,
            {"state": "active", "assignee": _admin_actor_key(request), "snoozed_until": None, "snooze_reason": ""},
        )
    except HTTPException:
        pass
    logger.info("Оператор перехватил диалог: %s", phone)
    return {"status": "ok", "phone": phone, "mode": "human"}


@router.post("/chats/{phone}/release")
async def release_chat(request: Request, phone: str) -> dict:
    """
    Вернуть управление боту — AI снова отвечает на сообщения.
    Возвращает состояние в CHATTING.
    """
    org_id = admin_org_from_session(request)
    await set_user_state(redis_client, phone, UserState.CHATTING, organization_id=org_id)
    await publish_event("state_changed", {
        "phone": phone,
        "state": UserState.CHATTING,
        "organization_id": org_id,
    })
    logger.info("Оператор вернул бота: %s", phone)
    return {"status": "ok", "phone": phone, "mode": "bot"}


class ChatSnoozeBody(BaseModel):
    minutes: int = Field(30, ge=1, le=60 * 24 * 14)
    reason: str = Field("", max_length=240)


@router.post("/chats/{phone}/assign-me")
async def assign_chat_to_me(request: Request, phone: str, db: AsyncSession = Depends(get_db)) -> dict:
    return await _save_chat_triage(
        request,
        db,
        phone,
        {
            "state": "active",
            "assignee": _admin_actor_key(request),
            "snoozed_until": None,
            "snooze_reason": "",
        },
    )


@router.post("/chats/{phone}/snooze")
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
            "assignee": _admin_actor_key(request),
            "snoozed_until": until.isoformat(),
            "snooze_reason": (body.reason or "").strip(),
        },
    )


@router.post("/chats/{phone}/close")
async def close_chat(request: Request, phone: str, db: AsyncSession = Depends(get_db)) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return await _save_chat_triage(
        request,
        db,
        phone,
        {
            "state": "closed",
            "assignee": _admin_actor_key(request),
            "snoozed_until": None,
            "snooze_reason": "",
            "closed_at": now,
            "closed_by": _admin_actor_key(request),
        },
    )


@router.post("/chats/{phone}/reopen")
async def reopen_chat(request: Request, phone: str, db: AsyncSession = Depends(get_db)) -> dict:
    return await _save_chat_triage(
        request,
        db,
        phone,
        {
            "state": "active",
            "assignee": _admin_actor_key(request),
            "snoozed_until": None,
            "snooze_reason": "",
            "closed_at": None,
            "closed_by": "",
        },
    )


class TextRequest(BaseModel):
    """Тело запроса с текстовым сообщением."""
    text: str


@router.post("/chats/{phone}/send_message")
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
    op_log = ChatLog(
        user_id=user.id,
        organization_id=org_id,
        role="operator",
        content=body.text,
        delivery_status="sending",
        status_updated_at=now,
    )
    db.add(op_log)
    await db.commit()
    await db.refresh(op_log)
    log_id = int(op_log.id)

    # UI-event только после commit: иначе фантомная запись, если транзакция упадёт.
    await publish_event("new_message", {
        "phone": phone,
        "role": "operator",
        "content": body.text,
        "id": log_id,
        "delivery_status": "sending",
        "organization_id": org_id,
    })

    wa = await send_message(phone, body.text)

    # finalize после внешнего I/O, но в той же сессии (важно для тестов/фикстур)
    evt = await finalize_outbound_delivery(
        db, log_id, wa.ok, provider_message_id=wa.message_id, error_details=wa.error,
    )
    await db.commit()
    if evt is not None:
        await publish_event("message_status_updated", evt)
    logger.info("Оператор отправил сообщение в %s: %s", phone, body.text[:50])
    return {"status": "sent" if wa.ok else "failed", "phone": phone, "chat_log_id": log_id}


@router.post("/chats/{phone}/messages/{chat_log_id}/resend")
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
    await publish_event("new_message", {
        "phone": phone,
        "role": log.role,
        "content": text,
        "id": chat_log_id,
        "delivery_status": "sending",
        "organization_id": org_id,
    })
    wa = await send_message(phone, text)
    evt = await finalize_outbound_delivery(
        db, chat_log_id, wa.ok, provider_message_id=wa.message_id, error_details=wa.error,
    )
    await db.commit()
    if evt is not None:
        await publish_event("message_status_updated", evt)
    return {"status": "sent" if wa.ok else "failed", "phone": phone, "chat_log_id": chat_log_id}


@router.get("/chats/{phone}/state")
async def get_chat_state(request: Request, phone: str) -> dict:
    """Получить текущее состояние диалога (CHATTING, CONFIRMING_ORDER, HUMAN_MODE)."""
    org_id = admin_org_from_session(request)
    state = await get_user_state(redis_client, phone, organization_id=org_id)
    return {"phone": phone, "state": state.value}


# ─── Тест бота (без WhatsApp) ────────────────────────────

@router.post("/test-bot")
async def test_bot(request: Request, body: TextRequest) -> dict:
    """
    Тестовый endpoint: эмулирует диалог с ботом без WhatsApp.
    Использует фиктивный номер 'test-admin', проходит полный цикл AI.
    """
    from app.api.webhooks import (
        handle_booking_confirmation,
        handle_confirmation,
        handle_order_payment_choice,
    )

    org_id = admin_org_from_session(request)
    phone = "test-admin"
    message_text = body.text

    state = await get_user_state(redis_client, phone, organization_id=org_id)

    if state == UserState.HUMAN_MODE:
        return {"reply": "[HUMAN_MODE — AI отключён]", "state": state.value, "intent": None}

    if state == UserState.AWAITING_ORDER_PAYMENT:
        reply = await handle_order_payment_choice(phone, message_text, org_id)
        await append_to_history(redis_client, phone, "user", message_text, organization_id=org_id)
        await append_to_history(redis_client, phone, "assistant", reply or "", organization_id=org_id)
        new_state = await get_user_state(redis_client, phone, organization_id=org_id)
        return {"reply": reply, "state": new_state.value, "intent": None}

    if state == UserState.CONFIRMING_ORDER:
        reply = await handle_confirmation(phone, message_text, org_id)
        await append_to_history(redis_client, phone, "user", message_text, organization_id=org_id)
        await append_to_history(redis_client, phone, "assistant", reply or "", organization_id=org_id)
        new_state = await get_user_state(redis_client, phone, organization_id=org_id)
        return {"reply": reply, "state": new_state.value, "intent": None}

    if state == UserState.CONFIRMING_BOOKING:
        reply = await handle_booking_confirmation(phone, message_text, org_id)
        await append_to_history(redis_client, phone, "user", message_text, organization_id=org_id)
        await append_to_history(redis_client, phone, "assistant", reply, organization_id=org_id)
        new_state = await get_user_state(redis_client, phone, organization_id=org_id)
        return {"reply": reply, "state": new_state.value, "intent": None}

    history = await get_chat_history(redis_client, phone, organization_id=org_id)
    await append_to_history(redis_client, phone, "user", message_text, organization_id=org_id)

    async with async_session_factory() as db:
        org_ent = await db.get(Organization, org_id)
        current_time_ctx = format_org_current_time_block(
            getattr(org_ent, "timezone", None) if org_ent is not None else "Asia/Almaty",
            getattr(org_ent, "schedule_json", None) if org_ent is not None else None,
        )
        menu_items = await load_available_menu(db, organization_id=org_id)
        menu_context = await build_menu_context_for_ai(menu_items, message_text)
        u_row = await db.scalar(
            select(User).where(User.phone == phone, User.organization_id == org_id),
        )
        customer_ctx = await build_customer_context(db, u_row)
        kb_context = await load_knowledge_context_block(db, org_id)
        draft_row = await get_open_draft_order(db, phone, org_id)
        draft_ctx = format_draft_order_context_for_prompt(
            draft_row.items_json if draft_row else None,
        )
        strategy_ctx = ""
        sales_gastro_hint = ""
        sales_target_iiko_ids: list[str] = []
        if draft_row and isinstance(draft_row.items_json, dict):
            cart = [
                x for x in (draft_row.items_json.get("items") or [])
                if isinstance(x, dict)
            ]
            om = draft_row.items_json.get("order_meta")
            meta_d = om if isinstance(om, dict) else {}
            total = float(draft_row.total_price or 0)
            decision = build_sales_strategy(
                cart, total, meta_d, menu_items,
                u_row.meta_json if u_row is not None else None,
            )
            strategy_ctx = format_strategy_for_prompt(decision)
            sales_gastro_hint = (decision.gastro_hint or "").strip()
            sales_target_iiko_ids = list(decision.target_iiko_ids or [])
        ai_response = await call_openai(
            history,
            message_text,
            menu_context,
            kb_context,
            draft_order_context=draft_ctx,
            sales_strategy_context=strategy_ctx,
            customer_context=customer_ctx,
            current_time_context=current_time_ctx,
            raise_on_transient=False,
        )
        inbound_mid = f"admin-test-bot:{secrets.token_hex(8)}"
        result = await route_intent(
            db,
            phone,
            ai_response,
            menu_items=menu_items,
            organization_id=org_id,
            inbound_message_id=inbound_mid,
            sales_gastro_hint=sales_gastro_hint,
            sales_target_iiko_ids=sales_target_iiko_ids,
        )
        await update_user_session_fields_in_db(
            db,
            phone=phone,
            organization_id=org_id,
            current_state=(result.new_state.value if result.new_state else None),
            **(
                {"current_pending_order_id": result.pending_order_id}
                if result.pending_order_id is not None
                else {}
            ),
            **(
                {"current_pending_booking_id": result.pending_booking_id}
                if result.pending_booking_id is not None
                else {}
            ),
        )

        await db.commit()

        if result.new_state:
            await set_user_state(redis_client, phone, result.new_state, organization_id=org_id)
        if result.pending_order_id:
            await set_pending_order(redis_client, phone, result.pending_order_id, organization_id=org_id)
        if result.pending_booking_id:
            await set_pending_booking(redis_client, phone, result.pending_booking_id, organization_id=org_id)
        for evt_type, evt_data in (result.events or []):
            await publish_event(evt_type, evt_data)

    await append_to_history(redis_client, phone, "assistant", result.reply_text, organization_id=org_id)

    new_state = await get_user_state(redis_client, phone, organization_id=org_id)
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
        "scope": getattr(r, "scope", None) or "item",
        "category_match": getattr(r, "category_match", None) or "",
        "is_active": r.is_active,
        "sort_order": r.sort_order,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    }


def _upsell_rule_dict(r: UpsellRule) -> dict:
    return {
        "id": r.id,
        "organization_id": r.organization_id,
        "trigger_mode": r.trigger_mode,
        "trigger_category": r.trigger_category,
        "suggest_category": r.suggest_category,
        "min_order_sum": float(r.min_order_sum or 0),
        "max_order_sum": float(r.max_order_sum) if r.max_order_sum is not None else None,
        "phrase_template": r.phrase_template or "",
        "sort_order": r.sort_order,
        "is_active": bool(r.is_active),
    }


class UpsellRuleCreateBody(BaseModel):
    trigger_mode: str = "missing_category"
    trigger_category: str = Field(..., min_length=1, max_length=120)
    suggest_category: str = Field(..., min_length=1, max_length=120)
    min_order_sum: float = Field(0, ge=0)
    max_order_sum: float | None = Field(None, ge=0)
    phrase_template: str = ""
    sort_order: int = 0
    is_active: bool = True


class UpsellRulePatchBody(BaseModel):
    trigger_mode: str | None = None
    trigger_category: str | None = Field(None, max_length=120)
    suggest_category: str | None = Field(None, max_length=120)
    min_order_sum: float | None = Field(None, ge=0)
    max_order_sum: float | None = None
    phrase_template: str | None = None
    sort_order: int | None = None
    is_active: bool | None = None


class UpsellFeedbackBody(BaseModel):
    mode: Literal["suggest", "forbid"] = "forbid"
    trigger_category: str = Field("", max_length=120)
    suggest_category: str = Field("", max_length=120)
    item_iiko_id: str = Field("", max_length=100)
    item_name: str = Field("", max_length=255)
    phrase_template: str = Field("", max_length=1000)
    is_active: bool = True


@router.get("/upsell-rules")
async def list_upsell_rules(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    org_id = admin_org_from_session(request)
    result = await db.execute(
        select(UpsellRule)
        .where(UpsellRule.organization_id == org_id)
        .order_by(UpsellRule.sort_order.desc(), UpsellRule.id),
    )
    rows = list(result.scalars().all())
    return {"items": [_upsell_rule_dict(r) for r in rows]}


@router.post("/upsell-rules")
async def create_upsell_rule(
    request: Request,
    body: UpsellRuleCreateBody,
    db: AsyncSession = Depends(get_db),
) -> dict:
    org_id = admin_org_from_session(request)
    tmpl = (body.phrase_template or "").strip() or (
        "К заказу отлично подойдёт {item_name} ({price} ₸). Добавить?"
    )
    row = UpsellRule(
        organization_id=org_id,
        trigger_mode=(body.trigger_mode or "missing_category").strip(),
        trigger_category=body.trigger_category.strip(),
        suggest_category=body.suggest_category.strip(),
        min_order_sum=body.min_order_sum,
        max_order_sum=body.max_order_sum,
        phrase_template=tmpl,
        sort_order=body.sort_order,
        is_active=body.is_active,
    )
    db.add(row)
    await db.flush()
    return {"ok": True, "item": _upsell_rule_dict(row)}


@router.patch("/upsell-rules/{rule_id}")
async def patch_upsell_rule(
    request: Request,
    rule_id: int,
    body: UpsellRulePatchBody,
    db: AsyncSession = Depends(get_db),
) -> dict:
    org_id = admin_org_from_session(request)
    row = await db.get(UpsellRule, rule_id)
    if row is None or int(row.organization_id) != org_id:
        raise HTTPException(status_code=404, detail="Правило не найдено")
    data = body.model_dump(exclude_unset=True)
    for key in ("trigger_mode", "trigger_category", "suggest_category", "phrase_template"):
        if key in data and data[key] is not None:
            data[key] = str(data[key]).strip()
    for key, value in data.items():
        setattr(row, key, value)
    await db.flush()
    return {"ok": True, "item": _upsell_rule_dict(row)}


@router.delete("/upsell-rules/{rule_id}")
async def delete_upsell_rule(
    request: Request,
    rule_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict:
    org_id = admin_org_from_session(request)
    row = await db.get(UpsellRule, rule_id)
    if row is None or int(row.organization_id) != org_id:
        raise HTTPException(status_code=404, detail="Правило не найдено")
    await db.delete(row)
    await db.flush()
    return {"ok": True}


@router.post("/orders/{order_id}/feedback/upsell-rule")
async def create_upsell_rule_from_order_feedback(
    request: Request,
    order_id: int,
    body: UpsellFeedbackBody,
    db: AsyncSession = Depends(get_db),
) -> dict:
    org_id = admin_org_from_session(request)
    order = await _order_in_org(db, order_id, org_id)
    items_json = order.items_json if isinstance(order.items_json, dict) else {}
    foods = items_json.get("items") if isinstance(items_json.get("items"), list) else []
    first_food = next((x for x in foods if isinstance(x, dict)), {})
    meta = order_meta_from_items_json(items_json)
    trace = meta.get("recommendation_trace")
    trace_item = next((x for x in trace if isinstance(x, dict)), {}) if isinstance(trace, list) else {}

    trigger_category = (body.trigger_category or str(first_food.get("category") or "")).strip()
    suggest_category = (body.suggest_category or str(trace_item.get("category") or trace_item.get("suggest_category") or "")).strip()
    item_iiko_id = (body.item_iiko_id or str(trace_item.get("item_iiko_id") or trace_item.get("iiko_id") or "")).strip()
    item_name = (body.item_name or str(trace_item.get("item_name") or trace_item.get("name") or "")).strip()

    if body.mode == "suggest":
        if not trigger_category or not suggest_category:
            raise HTTPException(status_code=400, detail="trigger_category and suggest_category are required")
        row = UpsellRule(
            organization_id=org_id,
            trigger_mode="missing_category",
            trigger_category=trigger_category,
            suggest_category=suggest_category,
            min_order_sum=0,
            max_order_sum=None,
            phrase_template=(body.phrase_template or "").strip() or "К заказу хорошо подойдёт {item_name} ({price} ₸). Добавить?",
            sort_order=100,
            is_active=bool(body.is_active),
        )
        db.add(row)
        await db.flush()
        created: dict[str, Any] = {"kind": "upsell_rule", "rule": _upsell_rule_dict(row)}
    else:
        if not item_iiko_id and not item_name:
            raise HTTPException(status_code=400, detail="item_iiko_id or item_name is required")
        q = select(MenuItem).where(MenuItem.organization_id == org_id)
        if item_iiko_id:
            q = q.where(MenuItem.iiko_id == item_iiko_id)
        else:
            q = q.where(func.lower(MenuItem.name) == item_name.lower())
        menu_item = (await db.execute(q.limit(1))).scalar_one_or_none()
        if menu_item is None:
            raise HTTPException(status_code=404, detail="Menu item not found for anti-rule")
        tags = [t.strip() for t in (menu_item.tags or "").split(",") if t.strip()]
        if "not_upsell" not in {t.lower() for t in tags}:
            tags.append("not_upsell")
        menu_item.tags = ", ".join(tags)
        created = {"kind": "anti_rule", "menu_item_id": int(menu_item.id), "tags": menu_item.tags}

    ij = dict(items_json)
    om = dict(ij.get("order_meta") or {}) if isinstance(ij.get("order_meta"), dict) else {}
    audit = list(om.get("upsell_feedback_audit") or []) if isinstance(om.get("upsell_feedback_audit"), list) else []
    audit.append({
        "mode": body.mode,
        "at": datetime.now(timezone.utc).isoformat(),
        "actor": _admin_actor_key(request),
        "result": created,
    })
    om["upsell_feedback_audit"] = audit[-25:]
    ij["order_meta"] = om
    order.items_json = ij
    await db.commit()
    return {"ok": True, "feedback": created}


class PackagingRuleCreateBody(BaseModel):
    kind: str = Field(..., min_length=1, max_length=60)
    name: str = Field(..., min_length=1, max_length=200)
    price: float = Field(0, ge=0)
    iiko_product_id: str | None = None
    keywords: str = ""
    option_key: str = ""
    scope: Literal["item", "category", "order"] = "item"
    category_match: str = Field("", max_length=120)
    is_active: bool = True
    sort_order: int = 0


class PackagingRulePatchBody(BaseModel):
    kind: str | None = Field(None, min_length=1, max_length=60)
    name: str | None = Field(None, min_length=1, max_length=200)
    price: float | None = Field(None, ge=0)
    iiko_product_id: str | None = None
    keywords: str | None = None
    option_key: str | None = None
    scope: Literal["item", "category", "order"] | None = None
    category_match: str | None = Field(None, max_length=120)
    is_active: bool | None = None
    sort_order: int | None = None


class PackagingPreviewBody(BaseModel):
    menu_item_id: int = Field(..., ge=1)
    quantity: int = Field(1, ge=1, le=999)
    # Whitelist через Literal: единственный валидный набор типов заказа в домене.
    # Pydantic вернёт 422 на любой другой ввод — не маскируем мусор в логику расчёта.
    order_type: Literal["delivery", "pickup", "hall"] = Field(...)
    # Синхронизация с доменной схемой PackagingPlov1Kg (app/schemas/ai_schemas.py):
    # пустая строка = не выбран, tabak / foil_kazan = контейнеры для плова 1кг.
    packaging_plov_1kg: Literal["", "tabak", "foil_kazan"] = ""


@router.get("/packaging-rules")
async def list_packaging_rules(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    """Правила упаковки арендатора (включая неактивные). При отсутствии — сиды для организации."""
    from app.services.order_logic import PACKAGING_SEED

    org_id = admin_org_from_session(request)
    result = await db.execute(
        select(PackagingRule)
        .where(_packaging_tenant_clause(org_id))
        .order_by(PackagingRule.sort_order.desc(), PackagingRule.id)
    )
    rows = list(result.scalars().all())
    if not rows:
        for seed in PACKAGING_SEED:
            db.add(PackagingRule(**{**seed, "organization_id": org_id}))
        await db.flush()
        result2 = await db.execute(
            select(PackagingRule)
            .where(_packaging_tenant_clause(org_id))
            .order_by(PackagingRule.sort_order.desc(), PackagingRule.id)
        )
        rows = list(result2.scalars().all())
    return {"items": [_packaging_rule_dict(r) for r in rows]}


@router.post("/packaging-rules/preview")
async def preview_packaging_rules(
    request: Request,
    body: PackagingPreviewBody,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    «Живой калькулятор» для вкладки упаковки: считает fee_lines тем же кодом, что и прод,
    чтобы админ видел понятную математику без дублирования логики в JS.
    """
    from app.services.order_logic import compute_fee_lines

    org_id = admin_org_from_session(request)

    menu_item = await _menu_item_in_org(db, int(body.menu_item_id), org_id)

    qty = int(body.quantity)
    price = float(menu_item.price or 0.0)
    foods_subtotal = round(price * qty, 2)
    foods = [{
        "name": menu_item.name,
        "category": menu_item.category or "",
        "quantity": qty,
        "price_per_unit": price,
        "item_total": foods_subtotal,
        "packaging_plov_1kg": body.packaging_plov_1kg,
    }]

    rules = await load_packaging_rules(db, org_id)
    fee_lines, extras = compute_fee_lines(foods, foods_subtotal, body.order_type, packaging_rules=rules)
    grand_total = round(foods_subtotal + float(extras), 2)

    return {
        "ok": True,
        "input": {
            "order_type": body.order_type,
            "quantity": qty,
            "menu_item": _menu_item_dict(menu_item),
        },
        "foods_subtotal": foods_subtotal,
        "fee_lines": fee_lines,
        "extras_total": round(float(extras), 2),
        "grand_total": grand_total,
    }


@router.post("/packaging-rules")
async def create_packaging_rule(
    request: Request,
    body: PackagingRuleCreateBody,
    db: AsyncSession = Depends(get_db),
) -> dict:
    org_id = admin_org_from_session(request)
    k = body.kind.strip()
    existing = await db.scalar(
        select(PackagingRule).where(
            PackagingRule.organization_id == org_id,
            PackagingRule.kind == k,
        ),
    )
    if existing:
        raise HTTPException(status_code=409, detail=f"Правило с kind='{k}' уже есть у этой организации")
    row = PackagingRule(
        organization_id=org_id,
        kind=k,
        name=body.name.strip(),
        price=body.price,
        iiko_product_id=(body.iiko_product_id or "").strip() or None,
        keywords=(body.keywords or "").strip(),
        option_key=(body.option_key or "").strip(),
        scope=body.scope,
        category_match=(body.category_match or "").strip(),
        is_active=body.is_active,
        sort_order=body.sort_order,
    )
    db.add(row)
    await db.flush()
    return {"ok": True, "item": _packaging_rule_dict(row)}


@router.patch("/packaging-rules/{rule_id}")
async def patch_packaging_rule(
    request: Request,
    rule_id: int,
    body: PackagingRulePatchBody,
    db: AsyncSession = Depends(get_db),
) -> dict:
    org_id = admin_org_from_session(request)
    row = await db.get(PackagingRule, rule_id)
    if row is None or int(row.organization_id) != org_id:
        raise HTTPException(status_code=404, detail="Правило не найдено")
    data = body.model_dump(exclude_unset=True)
    for key in ("kind", "name", "keywords", "option_key", "category_match"):
        if key in data and data[key] is not None:
            data[key] = data[key].strip()
    if "iiko_product_id" in data:
        data["iiko_product_id"] = (data["iiko_product_id"] or "").strip() or None
    if "kind" in data and data["kind"] is not None:
        dup = await db.scalar(
            select(PackagingRule).where(
                PackagingRule.organization_id == org_id,
                PackagingRule.kind == data["kind"],
                PackagingRule.id != row.id,
            ),
        )
        if dup:
            raise HTTPException(status_code=409, detail="Такой kind уже занят у организации")
    for key, value in data.items():
        setattr(row, key, value)
    await db.flush()
    return {"ok": True, "item": _packaging_rule_dict(row)}


@router.delete("/packaging-rules/{rule_id}")
async def delete_packaging_rule(
    request: Request,
    rule_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict:
    org_id = admin_org_from_session(request)
    row = await db.get(PackagingRule, rule_id)
    if row is None or int(row.organization_id) != org_id:
        raise HTTPException(status_code=404, detail="Правило не найдено")
    await db.delete(row)
    await db.flush()
    return {"ok": True}
