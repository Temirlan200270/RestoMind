"""
Маршрутизатор намерений (intent).
Получает ответ от AI Brain и выполняет соответствующую бизнес-логику:
  order → валидация по меню + создание DRAFT + запрос подтверждения
  book → сохранение бронирования в БД
  escalate → уведомление администратора + перевод в HUMAN_MODE
  faq → просто отправка ответа
"""

import logging
from dataclasses import dataclass
from datetime import date, time
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Booking, MenuItem, Order, OrderStatus, User
from app.services.booking_halls import (
    BOOKING_HALL_VIP,
    HALL_LABEL_RU,
    normalize_hall_key,
    vip_slot_occupied,
)
from app.schemas.ai_schemas import AIBrainResponse
from app.services.dialog_mgr import UserState
from app.services.events import publish_event
from app.services.order_logic import validate_order

logger = logging.getLogger(__name__)


@dataclass
class RouteResult:
    """Результат маршрутизации intent — текст ответа + опциональные флаги."""

    reply_text: str
    pending_order_id: int | None = None
    pending_booking_id: int | None = None
    new_state: UserState | None = None


async def get_or_create_user(db: AsyncSession, phone: str) -> User:
    """Находит пользователя по телефону или создаёт нового."""
    result = await db.execute(select(User).where(User.phone == phone))
    user = result.scalar_one_or_none()

    if user is None:
        user = User(phone=phone)
        db.add(user)
        await db.flush()
        logger.info("Создан новый пользователь: phone=%s, id=%d", phone, user.id)

    return user


async def _handle_order(
    db: AsyncSession,
    phone: str,
    ai_response: AIBrainResponse,
    menu_items: list[MenuItem] | None = None,
) -> RouteResult:
    """
    Обработка intent='order':
    Валидация → создание DRAFT → запрос подтверждения у клиента.
    Заказ НЕ отправляется в iiko сразу — ждёт подтверждения.
    """
    if not ai_response.items:
        return RouteResult(reply_text=ai_response.reply_text)

    validated = await validate_order(ai_response.items, menu_items=menu_items, db=db)

    if not validated.valid_items:
        unknown_list = ", ".join(validated.unknown_items) if validated.unknown_items else "—"
        return RouteResult(
            reply_text=(
                f"{ai_response.reply_text}\n\n"
                f"⚠️ К сожалению, не нашёл в меню: {unknown_list}.\n"
                "Попробуйте назвать блюда по-другому или спросите, что есть в меню."
            )
        )

    user = await get_or_create_user(db, phone)

    order = Order(
        user_id=user.id,
        status=OrderStatus.DRAFT,
        items_json={"items": validated.valid_items},
        total_price=validated.total_price,
    )
    db.add(order)
    await db.flush()

    reply = ai_response.reply_text + "\n\n📋 Ваш заказ:\n" + validated.summary_text

    if validated.unknown_items:
        reply += "\n\nНе нашёл в меню некоторые позиции. Уточните, пожалуйста."

    reply += "\n\n✅ Подтверждаете заказ? (Да / Нет)"

    logger.info(
        "Заказ #%d (DRAFT): %d позиций, %.2f ₸ — ожидает подтверждения",
        order.id, len(validated.valid_items), validated.total_price,
    )

    await publish_event("order_updated", {
        "order_id": order.id, "status": OrderStatus.DRAFT,
        "phone": phone, "total_price": validated.total_price,
        "items": validated.valid_items,
    })

    return RouteResult(
        reply_text=reply,
        pending_order_id=order.id,
        new_state=UserState.CONFIRMING_ORDER,
    )


async def _handle_booking(
    db: AsyncSession, phone: str, ai_response: AIBrainResponse
) -> RouteResult:
    """
    Обработка intent='book':
    Парсинг даты/времени → создание DRAFT-брони → запрос подтверждения.
    """
    details = ai_response.booking_details
    if details is None:
        return RouteResult(reply_text=ai_response.reply_text)

    user = await get_or_create_user(db, phone)

    try:
        booking_date = date.fromisoformat(details.date)
        booking_time = time.fromisoformat(details.time)
    except ValueError:
        logger.warning("Не удалось распарсить дату/время: %s %s", details.date, details.time)
        return RouteResult(
            reply_text=ai_response.reply_text + "\n\n⚠️ Не удалось определить дату или время. Уточните, пожалуйста."
        )

    hall = normalize_hall_key(details.hall)
    if hall == BOOKING_HALL_VIP and await vip_slot_occupied(db, booking_date, booking_time, None):
        return RouteResult(
            reply_text=(
                f"{ai_response.reply_text}\n\n"
                "⚠️ VIP-зал на это время уже занят (в ресторане один VIP-стол на слот). "
                "Предложите другое время или Зал 1 / Зал 2."
            ),
        )

    booking = Booking(
        user_id=user.id,
        booking_date=booking_date,
        booking_time=booking_time,
        guests=details.guests,
        hall=hall,
        comment=details.comment,
        status="draft",
    )
    db.add(booking)
    await db.flush()

    weekday_names = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
    weekday = weekday_names[booking_date.weekday()]
    formatted_date = f"{booking_date.day:02d}.{booking_date.month:02d}.{booking_date.year} ({weekday})"
    formatted_time = f"{booking_time.hour:02d}:{booking_time.minute:02d}"

    reply = (
        f"{ai_response.reply_text}\n\n"
        f"📋 Ваша бронь:\n"
        f"  📅 Дата: {formatted_date}\n"
        f"  🕐 Время: {formatted_time}\n"
        f"  🏛 Зал: {HALL_LABEL_RU.get(hall, hall)}\n"
        f"  👥 Гостей: {details.guests}\n"
    )
    if details.comment:
        reply += f"  💬 Пожелание: {details.comment}\n"
    reply += "\n✅ Подтверждаете бронирование? (Да / Нет)"

    logger.info(
        "Бронь #%d (DRAFT): %s в %s на %d гостей — ожидает подтверждения",
        booking.id, booking_date, booking_time, details.guests,
    )

    return RouteResult(
        reply_text=reply,
        pending_booking_id=booking.id,
        new_state=UserState.CONFIRMING_BOOKING,
    )


async def _handle_escalate(
    phone: str, ai_response: AIBrainResponse,
) -> RouteResult:
    """
    Обработка intent='escalate':
    Переводим пользователя в HUMAN_MODE, AI замолкает.
    """
    logger.warning(
        "🚨 ESCALATION: клиент %s просит оператора. AI: '%s'",
        phone, ai_response.reply_text,
    )
    return RouteResult(
        reply_text=ai_response.reply_text,
        new_state=UserState.HUMAN_MODE,
    )


async def confirm_order(db: AsyncSession, order_id: int) -> Order | None:
    """Подтвердить заказ — перевести из DRAFT в confirmed."""
    result = await db.execute(select(Order).where(Order.id == order_id))
    order = result.scalar_one_or_none()

    if order and order.status == OrderStatus.DRAFT:
        order.status = OrderStatus.CONFIRMED
        await db.flush()
        logger.info("Заказ #%d подтверждён клиентом", order_id)
    return order


async def confirm_booking(
    db: AsyncSession, booking_id: int,
) -> tuple[Booking | None, str | None]:
    """
    Подтвердить бронирование — перевести из draft в confirmed.
    Возвращает (booking, None) при успехе; (None, код ошибки) при сбое.
    """
    result = await db.execute(select(Booking).where(Booking.id == booking_id))
    booking = result.scalar_one_or_none()
    if not booking:
        return None, "not_found"
    if booking.status != "draft":
        return None, "not_draft"
    if booking.hall == BOOKING_HALL_VIP and await vip_slot_occupied(
        db, booking.booking_date, booking.booking_time, booking.id,
    ):
        return None, "vip_conflict"
    booking.status = "confirmed"
    await db.flush()
    logger.info("Бронь #%d подтверждена клиентом", booking_id)
    return booking, None


async def cancel_booking(db: AsyncSession, booking_id: int) -> Booking | None:
    """Отменить бронирование."""
    result = await db.execute(select(Booking).where(Booking.id == booking_id))
    booking = result.scalar_one_or_none()
    if booking and booking.status == "draft":
        booking.status = "cancelled"
        await db.flush()
        logger.info("Бронь #%d отменена клиентом", booking_id)
    return booking


async def cancel_order(db: AsyncSession, order_id: int) -> Order | None:
    """Отменить заказ — перевести из DRAFT в cancelled."""
    result = await db.execute(select(Order).where(Order.id == order_id))
    order = result.scalar_one_or_none()

    if order and order.status == OrderStatus.DRAFT:
        order.status = OrderStatus.CANCELLED
        await db.flush()
        logger.info("Заказ #%d отменён клиентом", order_id)
    return order


async def route_intent(
    db: AsyncSession,
    phone: str,
    ai_response: AIBrainResponse,
    menu_items: list[MenuItem] | None = None,
) -> RouteResult:
    """
    Главный маршрутизатор — вызывает обработчик в зависимости от intent.
    menu_items — если уже загружены, передаём чтобы не дублировать запрос.

    Returns:
        RouteResult с текстом ответа и опциональными сменами состояния.
    """
    intent = ai_response.intent

    if intent == "order":
        return await _handle_order(db, phone, ai_response, menu_items=menu_items)
    elif intent == "book":
        return await _handle_booking(db, phone, ai_response)
    elif intent == "escalate":
        return await _handle_escalate(phone, ai_response)
    else:
        return RouteResult(reply_text=ai_response.reply_text)
