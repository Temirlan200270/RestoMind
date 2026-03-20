"""
Админ-панель API.
REST-эндпоинты для просмотра заказов, диалогов, аналитики и синхронизации меню.
"""

import asyncio
import logging
import secrets
import uuid
from datetime import date, datetime, timedelta, timezone
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from sqlalchemy import delete as sql_delete
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from pydantic import BaseModel, Field

from app.core.config import settings
from app.db.models import Booking, ChatLog, MenuItem, Order, OrderStatus, User
from app.db.session import async_session_factory, get_db, redis_client
from app.integrations.whatsapp import send_message
from app.services.admin_tokens import create_admin_ws_token, parse_admin_ws_token
from app.services.ai_brain import call_gemini
from app.services.demo_data import clear_demo_data, demo_data_exists, seed_demo_data
from app.services.dialog_mgr import (
    UserState,
    append_to_history,
    get_chat_history,
    get_user_state,
    set_pending_booking,
    set_pending_order,
    set_user_state,
)
from app.services.events import publish_event, subscribe_events
from app.services.intent_router import get_or_create_user, route_intent
from app.services.menu_sync import sync_menu_from_iiko, sync_stop_lists
from app.services.order_logic import build_menu_context, load_available_menu

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
    try:
        async for event_json in subscribe_events():
            await ws.send_text(event_json)
    except WebSocketDisconnect:
        logger.info("Admin WebSocket отключён")
    except Exception as exc:
        logger.error("WebSocket error: %s", exc)


# ─── Заказы ─────────────────────────────────────────────

@router.get("/orders")
async def list_orders(
    status: str | None = Query(None, description="Фильтр по статусу (draft, confirmed, ...)"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Список заказов с пагинацией и фильтрацией по статусу."""
    query = select(Order).order_by(Order.created_at.desc())
    if status:
        query = query.where(Order.status == status)
    query = query.limit(limit).offset(offset)

    result = await db.execute(query)
    orders = result.scalars().all()

    return {
        "count": len(orders),
        "orders": [
            {
                "id": o.id,
                "user_id": o.user_id,
                "status": o.status,
                "items": o.items_json,
                "total_price": float(o.total_price),
                "created_at": o.created_at.isoformat() if o.created_at else None,
            }
            for o in orders
        ],
    }


# ─── Бронирования ───────────────────────────────────────

@router.get("/bookings")
async def list_bookings(
    status: str | None = Query(None, description="Фильтр по статусу (pending, confirmed, cancelled)"),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Список бронирований."""
    query = select(Booking).order_by(Booking.created_at.desc())
    if status:
        query = query.where(Booking.status == status)
    query = query.limit(limit)

    result = await db.execute(query)
    bookings = result.scalars().all()

    return {
        "count": len(bookings),
        "bookings": [
            {
                "id": b.id,
                "user_id": b.user_id,
                "date": b.booking_date.isoformat(),
                "time": b.booking_time.isoformat(),
                "guests": b.guests,
                "comment": b.comment,
                "status": b.status,
                "created_at": b.created_at.isoformat() if b.created_at else None,
            }
            for b in bookings
        ],
    }


# ─── Диалоги ────────────────────────────────────────────

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
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Список позиций меню."""
    query = select(MenuItem).order_by(MenuItem.category, MenuItem.name)
    if category:
        query = query.where(MenuItem.category == category)
    if available_only:
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


@router.post("/menu/sync")
async def sync_menu(
    api_login: str = Query(..., description="API-логин iiko"),
    organization_id: str = Query(..., description="ID организации в iiko"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Принудительная синхронизация меню из iiko.
    Скачивает номенклатуру и обновляет таблицу menu_items.
    """
    try:
        stats = await sync_menu_from_iiko(db, api_login, organization_id)
        return {"status": "ok", **stats}
    except Exception as exc:
        logger.error("Ошибка синхронизации меню: %s", exc, exc_info=True)
        raise HTTPException(status_code=502, detail=f"Ошибка при обращении к iiko: {exc}")


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


@router.delete("/demo")
async def demo_delete(db: AsyncSession = Depends(get_db)) -> dict:
    """Удалить всех демо-пользователей и связанные заказы/брони/логи."""
    if not await demo_data_exists(db):
        raise HTTPException(status_code=404, detail="Демо-данных нет")
    cleared = await clear_demo_data(db)
    return {"ok": True, **cleared}


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


# ─── Статистика дашборда ────────────────────────────────

@router.get("/stats")
async def dashboard_stats(
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Статистика для дашборда: выручка за сегодня, общие счётчики."""
    today_start = datetime.now(tz=timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0,
    )

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
        .where(not_cancelled, Order.created_at >= today_start)
    )
    today_row = today_q.one()
    today_orders = today_row[0]
    today_revenue = float(today_row[1])

    yesterday_start = today_start - timedelta(days=1)
    yesterday_q = await db.execute(
        select(func.count(Order.id), func.coalesce(func.sum(Order.total_price), 0))
        .where(
            not_cancelled,
            Order.created_at >= yesterday_start,
            Order.created_at < today_start,
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
    }


# ─── Аналитика ──────────────────────────────────────────


@router.get("/analytics")
async def analytics(
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
    now = datetime.now(tz=timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

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

    not_cancelled = Order.status != OrderStatus.CANCELLED

    # Агрегаты текущего периода (SQL)
    cur_q = await db.execute(
        select(
            func.count(Order.id),
            func.coalesce(func.sum(Order.total_price), 0),
        )
        .where(not_cancelled, Order.created_at >= start, Order.created_at <= end)
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
        .where(not_cancelled, Order.created_at >= prev_start, Order.created_at < prev_end)
    )
    prev_row = prev_q.one()
    prev_count = prev_row[0]
    prev_revenue = float(prev_row[1])

    avg_check = current_revenue / current_count if current_count else 0
    prev_avg = prev_revenue / prev_count if prev_count else 0

    # Загружаем только заказы текущего периода (для daily + top_items)
    cur_orders_result = await db.execute(
        select(Order)
        .where(not_cancelled, Order.created_at >= start, Order.created_at <= end)
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
    from app.api.webhooks import handle_booking_confirmation, handle_confirmation

    phone = "test-admin"
    message_text = body.text

    state = await get_user_state(redis_client, phone)

    if state == UserState.HUMAN_MODE:
        return {"reply": "[HUMAN_MODE — AI отключён]", "state": state.value, "intent": None}

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
        ai_response = await call_gemini(history, message_text, menu_context)
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
