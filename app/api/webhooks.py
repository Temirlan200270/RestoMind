"""
Роутер для входящих вебхуков WhatsApp.
Принимает сообщения, мгновенно возвращает 200 OK,
а обработку передаёт в BackgroundTasks.
"""

import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Query, Request, Response

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.rate_limiter import check_rate_limit
from app.db.models import ChatLog, Order, OrderStatus
from app.db.session import async_session_factory, redis_client
from app.integrations.iiko_client import IikoClient
from app.integrations.whatsapp import send_message, send_template
from app.services.ai_brain import call_gemini
from app.services.dialog_mgr import (
    CANCEL_WORDS,
    CONFIRM_WORDS,
    UserState,
    append_to_history,
    clear_pending_booking,
    clear_pending_order,
    get_chat_history,
    get_pending_booking,
    get_pending_order,
    get_user_state,
    set_pending_booking,
    set_pending_order,
    set_user_state,
)
from app.services.events import publish_event
from app.services.intent_router import (
    cancel_booking,
    cancel_order,
    confirm_booking,
    confirm_order,
    get_or_create_user,
    route_intent,
)
from app.services.order_logic import build_menu_context, load_available_menu

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/whatsapp", tags=["WhatsApp Webhook"])


async def _save_chat_log(
    db: AsyncSession, phone: str, user_text: str, reply_text: str,
) -> None:
    """Сохраняет пару сообщений (user + assistant) в ChatLog."""
    user = await get_or_create_user(db, phone)
    db.add(ChatLog(user_id=user.id, role="user", content=user_text))
    db.add(ChatLog(user_id=user.id, role="assistant", content=reply_text))


async def _send_order_to_iiko(
    order_id: int,
    phone: str,
    items_json: dict[str, Any] | None,
) -> bool:
    """Попытка отправить подтверждённый заказ в iiko. Возвращает True при успехе."""
    if not settings.iiko_api_login or not settings.iiko_organization_id:
        logger.info("iiko не настроен — заказ сохранён только в БД")
        return False

    try:
        items_data = items_json.get("items", []) if items_json else []
        iiko_items = []
        for item in items_data:
            iiko_id = item.get("iiko_id")
            if not iiko_id:
                continue
            iiko_items.append({
                "productId": iiko_id,
                "type": "Product",
                "amount": item.get("quantity", 1),
            })

        if not iiko_items:
            logger.warning("Нет позиций с iiko_id — заказ не отправлен в iiko")
            return False

        async with IikoClient(api_login=settings.iiko_api_login) as client:
            await client.create_delivery_order(
                organization_id=settings.iiko_organization_id,
                order_data={
                    "customer": {"phone": phone},
                    "items": iiko_items,
                    "comment": f"Заказ #{order_id} через RestoMind",
                },
            )
        return True
    except Exception as exc:
        logger.error("Ошибка отправки заказа #%d в iiko: %s", order_id, exc, exc_info=True)
        return False


async def handle_confirmation(phone: str, message_text: str) -> str:
    """
    Обработка ответа на подтверждение заказа.
    При «Да» → подтверждаем + пытаемся отправить в iiko.
    При «Нет» → отменяем.
    """
    word = message_text.lower().strip().rstrip("!.,")
    order_id = await get_pending_order(redis_client, phone)

    if not order_id:
        await clear_pending_order(redis_client, phone)
        return "Заказ не найден — возможно, истекло время ожидания. Назовите блюда заново."

    if word in CONFIRM_WORDS:
        async with async_session_factory() as db:
            order = await confirm_order(db, order_id)
            await db.commit()

        if not order:
            await clear_pending_order(redis_client, phone)
            return "Заказ не найден. Попробуйте оформить заново."

        await publish_event("order_updated", {
            "order_id": order.id, "status": OrderStatus.CONFIRMED,
            "phone": phone, "total_price": float(order.total_price),
        })

        sent_to_iiko = await _send_order_to_iiko(
            order_id=order.id,
            phone=phone,
            items_json=order.items_json,
        )

        if sent_to_iiko:
            async with async_session_factory() as db:
                order_upd = await db.execute(
                    select(Order).where(Order.id == order.id)
                )
                o = order_upd.scalar_one_or_none()
                if o:
                    o.status = OrderStatus.SENT_TO_IIKO
                    await db.commit()
            await publish_event("order_updated", {
                "order_id": order.id, "status": OrderStatus.SENT_TO_IIKO,
                "phone": phone, "total_price": float(order.total_price),
            })

        await clear_pending_order(redis_client, phone)

        status_msg = "передан в систему ресторана" if sent_to_iiko else "подтверждён и передан на кухню"
        return (
            f"Отлично! Заказ #{order.id} на сумму {float(order.total_price):.0f} ₸ "
            f"{status_msg}! 👨‍🍳"
        )

    if word in CANCEL_WORDS:
        async with async_session_factory() as db:
            await cancel_order(db, order_id)
            await db.commit()

        await publish_event("order_updated", {
            "order_id": order_id, "status": OrderStatus.CANCELLED, "phone": phone,
        })

        await clear_pending_order(redis_client, phone)
        return (
            "Заказ отменён. Вы можете:\n"
            "  • Назвать новые блюда — я оформлю новый заказ\n"
            "  • Написать что изменить — например «уберите лагман, добавьте плов»\n"
            "  • Или просто продолжить общение 😊"
        )

    return "Пожалуйста, ответьте «Да» для подтверждения или «Нет» для отмены заказа."


async def handle_booking_confirmation(phone: str, message_text: str) -> str:
    """
    Обработка подтверждения бронирования.
    При «Да» → подтверждаем. При «Нет» → отменяем.
    """
    word = message_text.lower().strip().rstrip("!.,")
    booking_id = await get_pending_booking(redis_client, phone)

    if not booking_id:
        await clear_pending_booking(redis_client, phone)
        return "Бронирование не найдено. Назовите дату и время заново."

    if word in CONFIRM_WORDS:
        async with async_session_factory() as db:
            booking = await confirm_booking(db, booking_id)
            await db.commit()

        if not booking:
            await clear_pending_booking(redis_client, phone)
            return "Бронирование не найдено. Попробуйте заново."

        await clear_pending_booking(redis_client, phone)
        return (
            f"Отлично! Бронь #{booking.id} подтверждена! 🎉\n"
            f"Ждём вас {booking.booking_date.strftime('%d.%m.%Y')} "
            f"в {booking.booking_time.strftime('%H:%M')} "
            f"на {booking.guests} гостей."
        )

    if word in CANCEL_WORDS:
        async with async_session_factory() as db:
            await cancel_booking(db, booking_id)
            await db.commit()

        await clear_pending_booking(redis_client, phone)
        return (
            "Бронирование отменено. Вы можете:\n"
            "  • Назвать другую дату и время\n"
            "  • Или просто продолжить общение 😊"
        )

    return "Пожалуйста, ответьте «Да» для подтверждения или «Нет» для отмены бронирования."


async def process_message(phone: str, message_text: str) -> None:
    """
    Полный цикл обработки входящего сообщения с учётом State Machine:
    1. Проверить состояние пользователя (HUMAN_MODE, CONFIRMING_ORDER, CHATTING)
    2. Маршрутизировать по состоянию
    3. Сохранить в Redis + ChatLog
    4. Отправить ответ
    """
    try:
        if not await check_rate_limit(phone):
            logger.warning("Rate limit: %s заблокирован", phone)
            await send_message(phone, "Слишком много сообщений. Подождите минуту и попробуйте снова.")
            return

        state = await get_user_state(redis_client, phone)

        await publish_event("new_message", {
            "phone": phone, "role": "user", "content": message_text,
        })

        # ─── HUMAN_MODE: AI молчит, только логируем ─────────
        if state == UserState.HUMAN_MODE:
            async with async_session_factory() as db:
                await _save_chat_log(db, phone, message_text, "[HUMAN_MODE — AI отключён]")
                await db.commit()
            await append_to_history(redis_client, phone, "user", message_text)
            logger.info("HUMAN_MODE: сообщение от %s сохранено, AI не вызван", phone)
            return

        # ─── CONFIRMING_ORDER: ждём Да/Нет ──────────────────
        if state == UserState.CONFIRMING_ORDER:
            final_reply = await handle_confirmation(phone, message_text)

            async with async_session_factory() as db:
                await _save_chat_log(db, phone, message_text, final_reply)
                await db.commit()

            await append_to_history(redis_client, phone, "user", message_text)
            await append_to_history(redis_client, phone, "assistant", final_reply)
            await send_message(phone, final_reply)

            await publish_event("new_message", {
                "phone": phone, "role": "assistant", "content": final_reply,
            })
            return

        # ─── CONFIRMING_BOOKING: ждём Да/Нет ─────────────────
        if state == UserState.CONFIRMING_BOOKING:
            final_reply = await handle_booking_confirmation(phone, message_text)

            async with async_session_factory() as db:
                await _save_chat_log(db, phone, message_text, final_reply)
                await db.commit()

            await append_to_history(redis_client, phone, "user", message_text)
            await append_to_history(redis_client, phone, "assistant", final_reply)
            await send_message(phone, final_reply)

            await publish_event("new_message", {
                "phone": phone, "role": "assistant", "content": final_reply,
            })
            return

        # ─── CHATTING: обычный AI-флоу ──────────────────────
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

            await _save_chat_log(db, phone, message_text, result.reply_text)
            await db.commit()

        await append_to_history(redis_client, phone, "assistant", result.reply_text)
        await send_message(phone, result.reply_text)

        await publish_event("new_message", {
            "phone": phone, "role": "assistant", "content": result.reply_text,
            "intent": ai_response.intent,
        })

        if result.new_state == UserState.HUMAN_MODE:
            await publish_event("human_needed", {
                "phone": phone,
                "reason": ai_response.reply_text,
            })

        logger.info(
            "Сообщение обработано: phone=%s, intent=%s, state=%s",
            phone, ai_response.intent, state.value,
        )
    except Exception as exc:
        logger.error("Ошибка обработки сообщения от %s: %s", phone, exc, exc_info=True)
        try:
            await send_message(phone, "Извините, произошла ошибка. Попробуйте ещё раз чуть позже.")
        except Exception:
            logger.error("Не удалось отправить сообщение об ошибке → %s", phone)


@router.get("/webhook")
async def verify_webhook(
    hub_mode: str | None = Query(None, alias="hub.mode"),
    hub_verify_token: str | None = Query(None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(None, alias="hub.challenge"),
) -> Response:
    """
    Верификация вебхука Meta (WhatsApp).
    Meta отправляет GET-запрос с challenge, который нужно вернуть как есть.
    """
    if hub_mode == "subscribe" and hub_verify_token == settings.whatsapp_verify_token:
        logger.info("Вебхук WhatsApp успешно верифицирован")
        return Response(content=hub_challenge, media_type="text/plain")

    logger.warning("Неудачная попытка верификации вебхука")
    return Response(content="Forbidden", status_code=403)


@router.post("/webhook")
async def receive_message(request: Request, background_tasks: BackgroundTasks) -> dict:
    """
    Приём входящих сообщений от WhatsApp.
    Моментально возвращает 200 OK (требование Meta API),
    а обработку сообщения передаёт в фоновую задачу.
    """
    body = await request.json()
    logger.debug("Входящий вебхук: %s", body)

    try:
        entry = body.get("entry", [{}])[0]
        changes = entry.get("changes", [{}])[0]
        value = changes.get("value", {})
        messages = value.get("messages", [])

        if messages:
            msg = messages[0]
            phone = msg.get("from", "")
            message_text = msg.get("text", {}).get("body", "")

            if phone and message_text:
                background_tasks.add_task(process_message, phone, message_text)
                logger.info("Сообщение от %s поставлено в очередь обработки", phone)

    except (IndexError, KeyError, TypeError) as exc:
        logger.error("Ошибка парсинга вебхука: %s", exc)

    return {"status": "ok"}
