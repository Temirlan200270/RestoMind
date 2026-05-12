"""
Админ-панель API.
REST-эндпоинты для входа, WebSocket, демо-данных, настроек, экспорта и тест-бота.
"""

import csv
import io
import json
import logging
import secrets
from typing import Any
from datetime import date, datetime, time as dt_time, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect
from sqlalchemy import delete as sql_delete
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.passwords import verify_password
from app.db.models import (
    Booking,
    ChatLog,
    EscalationEvent,
    FailedTask,
    IntegrationEvent,
    MenuItem,
    Order,
    Organization,
    RegistrationRequest,
    StaffRole,
    StaffUser,
    User,
)
from app.db.session import async_session_factory, get_db, redis_client
from app.services.admin_tokens import AdminWsClaims, create_admin_ws_token, parse_admin_ws_token
from app.services.ai_brain import call_openai
from app.services.customer_context import build_customer_context
from app.services.time_context import format_org_current_time_block
from app.services.demo_data import clear_demo_data, demo_data_exists, seed_demo_data
from app.services.integration_health import build_status_payload
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
from app.services.billing_guard import (
    billing_suspended_http_exception,
    load_tenant_for_organization,
    tenant_is_billing_suspended,
)
from app.services.intent_router import (
    get_open_draft_order,
    route_intent,
)
from app.services.org_iiko import resolve_org_iiko_credentials
from app.services.tenant_scope import (
    available_organizations_for_admin_session,
    organization_id_allowed_for_admin_session,
    resolve_branding_for_session,
    resolve_tenant_summary_for_session,
)
from app.services.knowledge_context import load_knowledge_context_block
from app.services.order_logic import (
    build_menu_context_for_ai,
    format_draft_order_context_for_prompt,
    load_available_menu,
)
from app.services.tenant_scope import (
    failed_tasks_tenant_clause as _failed_tasks_tenant_clause,
    orders_tenant_clause as _orders_tenant_clause,
)
from app.services.sales_strategy import build_sales_strategy, format_strategy_for_prompt
from app.services.intelligence_analytics import order_meta_from_items_json

from .deps import (
    _bookings_tenant_clause,
    _credentials_ok,
    _escalation_tenant_clause,
    _integration_events_tenant_clause,
    admin_org_from_session,
    require_admin_session,  # noqa: F401 - re-exported from app.api.admin for compatibility
    require_admin_session_active,
    require_superadmin,
)
from .bookings import bookings_router
from .branding import branding_router
from .chats import admin_send_message, chats_router
from .customers import customers_router
from .knowledge import knowledge_router
from .menu_bulk import menu_bulk_router
from .schemas import TextRequest
from .system import system_router

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
                tenant_st = await load_tenant_for_organization(db, int(staff.organization_id))
                if tenant_is_billing_suspended(tenant_st):
                    raise billing_suspended_http_exception()
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
        tenant_st = await load_tenant_for_organization(db, oid)
        if tenant_is_billing_suspended(tenant_st):
            raise billing_suspended_http_exception()
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
    tenant_st = await load_tenant_for_organization(db, int(demo_org.id))
    if tenant_is_billing_suspended(tenant_st):
        raise HTTPException(status_code=503, detail="Демо временно недоступно")

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
    """
    Тело ответа GET /auth/me и успешного POST /auth/select-org (контракт PARALLEL_AI_PLAN §4).

    Контракт по биллингу (E2.3):

    * `billing_blocked` — стабильное поле, всегда `bool`. Сейчас всегда
      возвращается ``False``: при `tenant.plan_status == "suspended"` мы
      раньше возвращаем 403 (см. ``billing_suspended_http_exception``), сюда не
      доходим. Поле сохранено в payload как контрактная подушка для
      будущего «soft suspend» (тёплое окно после превышения лимита) —
      UI сможет читать `billing_blocked` без новой версии API.
    * Поля `branding`, `tenant`, `available_organizations`, `ws_token`
      обязательны и не должны быть `null` для успешной сессии (см.
      ``.cursor/rules/restomind-zones.mdc``).
    * Super-admin не блокируется ни статусом организации, ни биллингом.
    """
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
        tenant_me = await load_tenant_for_organization(db, int(oid))
        if tenant_is_billing_suspended(tenant_me):
            raise billing_suspended_http_exception()

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
    branding = await resolve_branding_for_session(
        db,
        staff=staff_me,
        active_organization_id=int(oid),
    )

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
        "billing_blocked": False,
    }


@auth_router.get("/me")
async def admin_me(request: Request, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Проверка сессии и перевыпуск ws_token для переподключения."""
    if not request.session.get("admin_ok"):
        return {"authenticated": False}
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
    if not is_superadmin:
        tenant_t = await load_tenant_for_organization(db, int(body.organization_id))
        if tenant_is_billing_suspended(tenant_t):
            raise billing_suspended_http_exception()

    request.session["organization_id"] = int(body.organization_id)
    return await _admin_auth_me_payload(request, db)

# ─── Защищённый REST API ─────────────────────────────────

router = APIRouter(
    prefix="/admin",
    tags=["Admin Panel"],
    dependencies=[Depends(require_admin_session_active)],
)

router.include_router(menu_bulk_router)
router.include_router(knowledge_router)
router.include_router(branding_router)
router.include_router(bookings_router)
router.include_router(customers_router)
router.include_router(chats_router)
router.include_router(system_router)
# NOTE: rules_router, analytics_router, menu_router, organization_router, orders_router
# have their own prefix="/admin" — they are mounted directly in app.main at /api level.


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
    elig = await count_chat_logs_eligible_for_purge(db)
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
            "gemini": {"configured": bool(str(settings.gemini_api_key or "").strip())},
            "ai_provider": (settings.ai_provider or "openai").strip().lower(),
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
            getattr(org_ent, "timezone", None) if org_ent is not None else "Etc/GMT-5",
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
        if u_row is not None:
            from app.services.ai_snooze import ai_snooze_is_active, clear_ai_snooze_if_expired

            await clear_ai_snooze_if_expired(db, u_row)
            await db.commit()
            if getattr(u_row, "ai_paused", False) or ai_snooze_is_active(u_row):
                return {"reply": "[OPERATOR_ONLY — AI не отвечает]", "state": state.value, "intent": None}
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

