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
from app.db.models import ChatLog, EscalationEvent, Order, OrderStatus, User
from app.db.session import async_session_factory, redis_client
from app.integrations.iiko_client import IikoClient
from app.integrations.telegram import send_tg_fallback_alert
from app.integrations.whatsapp import download_media_bytes, send_message, send_template, send_voice_message
from app.services.ai_brain import call_gemini, call_gemini_with_audio, gemini_transcribe_voice
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
from app.services.knowledge_context import load_knowledge_context_block
from app.services.order_logic import (
    build_menu_context,
    build_summary_text_from_stored_items,
    detect_payment_method_from_text,
    format_order_confirmation_summary,
    load_available_menu,
)
from app.services.tts_edge import synthesize_speech_mp3

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/whatsapp", tags=["WhatsApp Webhook"])

WHATSAPP_DEDUPE_TTL_SECONDS = 15 * 60


async def _dedupe_whatsapp_message(message_id: str) -> bool:
    """
    Защита от дублей вебхука Meta.

    Meta может доставлять одно и то же сообщение несколько раз, поэтому без идемпотентности
    в админке появляются дубли (и расходуется Gemini).

    Returns:
        True если сообщение уже было обработано (дубликат), иначе False.
    """
    mid = (message_id or "").strip()
    if not mid:
        return False
    key = f"wa:dedupe:{mid}"
    try:
        # redis.asyncio.Redis поддерживает nx/ex
        created = await redis_client.set(key, "1", ex=WHATSAPP_DEDUPE_TTL_SECONDS, nx=True)  # type: ignore[arg-type]
        return not bool(created)
    except TypeError:
        # InMemoryRedis не поддерживает nx — делаем простой get/set
        existing = await redis_client.get(key)
        if existing:
            return True
        try:
            await redis_client.setex(key, WHATSAPP_DEDUPE_TTL_SECONDS, "1")
        except Exception:
            await redis_client.set(key, "1", ex=WHATSAPP_DEDUPE_TTL_SECONDS)
        return False



async def _save_chat_log(
    db: AsyncSession,
    phone: str,
    user_text: str,
    reply_text: str,
    assistant_meta: dict | None = None,
) -> None:
    """Сохраняет пару сообщений (user + assistant) в ChatLog."""
    user = await get_or_create_user(db, phone)
    db.add(ChatLog(user_id=user.id, role="user", content=user_text))
    db.add(
        ChatLog(
            user_id=user.id,
            role="assistant",
            content=reply_text,
            meta_json=assistant_meta,
        ),
    )


async def _send_order_to_iiko(
    order_id: int,
    phone: str,
    items_json: dict[str, Any] | None,
) -> tuple[bool, str | None]:
    """
    Попытка отправить подтверждённый заказ в iiko.

    Returns:
        (True, None) — успех.
        (False, None) — iiko не настроен в .env (ошибку в БД не пишем).
        (False, msg) — настроен, но отправка не удалась (msg для админки).
    """
    if not settings.iiko_api_login or not settings.iiko_organization_id:
        logger.info("iiko не настроен — заказ сохранён только в БД")
        return False, None

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

        fee_lines = items_json.get("fee_lines", []) if items_json else []
        for fl in fee_lines:
            if not isinstance(fl, dict):
                continue
            iiko_id = fl.get("iiko_id")
            if not iiko_id:
                continue
            iiko_items.append({
                "productId": iiko_id,
                "type": "Product",
                "amount": int(fl.get("quantity", 1)),
            })

        if not iiko_items:
            msg = "Нет позиций с iiko_id — синхронизируйте меню из iiko"
            logger.warning("Заказ #%d: %s", order_id, msg)
            return False, msg

        meta = items_json.get("order_meta") if items_json else {}
        comment_bits = [
            f"Заказ #{order_id} · RestoMind · WhatsApp",
            f"тип: {meta.get('order_type', '?')}",
            f"оплата: {meta.get('payment_method', '?')}",
        ]
        if meta.get("delivery_address"):
            comment_bits.append(f"адрес: {meta['delivery_address'][:200]}")
        comment = " · ".join(comment_bits)

        terminal_group = (settings.iiko_terminal_group_id or "").strip()
        async with IikoClient(api_login=settings.iiko_api_login) as client:
            await client.create_delivery_order(
                organization_id=settings.iiko_organization_id,
                order_data={
                    "customer": {"phone": phone},
                    "items": iiko_items,
                    "comment": comment[:1000],
                },
                terminal_group_id=terminal_group or None,
            )
        return True, None
    except Exception as exc:
        logger.error("Ошибка отправки заказа #%d в iiko: %s", order_id, exc, exc_info=True)
        msg = str(exc).strip() or type(exc).__name__
        return False, msg[:500]


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
            "order_id": order.id,
            "status": OrderStatus.CONFIRMED,
            "phone": phone,
            "total_price": float(order.total_price),
            "iiko_last_error": None,
        })

        await clear_pending_order(redis_client, phone)

        return (
            f"Отлично! Заказ #{order.id} на сумму {float(order.total_price):.0f} ₸ принят. "
            "Оператор проверит детали и отправит заказ на кухню — при необходимости с вами свяжутся. 👨‍💼"
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
            booking, err = await confirm_booking(db, booking_id)
            await db.commit()

        if err == "vip_conflict":
            await clear_pending_booking(redis_client, phone)
            return (
                "К сожалению, VIP-зал на это время только что заняли.\n"
                "Напишите другую дату/время или выберите Зал 1 / Зал 2 — оформим бронь заново."
            )

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


_PAYMENT_LABEL_RU = {
    "cash": "Наличные при получении",
    "card": "Карта при получении",
    "remote": "Удалённая оплата (перевод / ссылка)",
}


async def handle_order_payment_choice(phone: str, message_text: str) -> str:
    """
    После черновика заказа: фиксируем способ оплаты в items_json, затем переход к Да/Нет.
    """
    order_id = await get_pending_order(redis_client, phone)
    if not order_id:
        await clear_pending_order(redis_client, phone)
        return "Заказ не найден — возможно, истекло время. Назовите блюда заново."

    word = message_text.lower().strip().rstrip("!.,")

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
            "  • Написать что изменить\n"
            "  • Или просто продолжить общение 😊"
        )

    pm = detect_payment_method_from_text(message_text)
    if not pm:
        return (
            "Не разобрал способ оплаты. Напишите, пожалуйста:\n"
            "  • **наличные**\n"
            "  • **карта** (при получении)\n"
            "  • **удалённо** (перевод или ссылка)\n"
            "\nИли **отмена**, если передумали."
        )

    async with async_session_factory() as db:
        order = await db.get(Order, order_id)
        if not order or order.status != OrderStatus.DRAFT:
            await clear_pending_order(redis_client, phone)
            return "Заказ не найден или уже обработан. Начните оформление заново."

        raw_json = order.items_json
        items_json: dict[str, object] = dict(raw_json) if isinstance(raw_json, dict) else {}
        order_meta: dict[str, object] = dict(items_json.get("order_meta") or {})
        order_meta["payment_method"] = pm
        items_json["order_meta"] = order_meta
        order.items_json = items_json

        summary_core = build_summary_text_from_stored_items(items_json)
        body = format_order_confirmation_summary(items_json, summary_core)
        pay_human = _PAYMENT_LABEL_RU.get(pm, pm)
        reply = (
            f"Принял способ оплаты: {pay_human}.\n\n"
            f"📋 Ваш заказ:\n{body}\n\n"
            "✅ Подтверждаете заказ? (Да / Нет)"
        )
        await db.commit()

        total = float(order.total_price)
        await publish_event("order_updated", {
            "order_id": order.id,
            "status": OrderStatus.DRAFT,
            "phone": phone,
            "total_price": total,
            "payment_method": pm,
        })

    await set_user_state(redis_client, phone, UserState.CONFIRMING_ORDER)
    return reply


async def process_message(
    phone: str,
    message_text: str = "",
    *,
    voice_audio: tuple[bytes, str] | None = None,
) -> None:
    """
    Полный цикл обработки входящего сообщения с учётом State Machine:
    1. Проверить состояние пользователя (HUMAN_MODE, AWAITING_ORDER_PAYMENT, CONFIRMING_ORDER, CHATTING)
    2. Маршрутизировать по состоянию
    3. Сохранить в Redis + ChatLog
    4. Отправить ответ
    """
    try:
        if not await check_rate_limit(phone):
            logger.warning("Rate limit: %s заблокирован", phone)
            await send_message(phone, "Слишком много сообщений. Подождите минуту и попробуйте снова.")
            return

        voice_bytes: bytes | None = None
        voice_mime = ""
        if voice_audio:
            voice_bytes, voice_mime = voice_audio

        state = await get_user_state(redis_client, phone)

        async with async_session_factory() as db_u:
            u_row = await db_u.scalar(select(User).where(User.phone == phone))
            ai_paused_db = bool(u_row and getattr(u_row, "ai_paused", False))

        # Голос без мультимодального чата: сначала текст (подтверждения, оператор)
        if voice_bytes is not None and (
            state == UserState.AWAITING_ORDER_PAYMENT
            or state == UserState.CONFIRMING_ORDER
            or state == UserState.CONFIRMING_BOOKING
            or state == UserState.HUMAN_MODE
            or ai_paused_db
        ):
            if not (settings.gemini_api_key or "").strip():
                await send_message(
                    phone,
                    "Голосовые недоступны: задайте GEMINI_API_KEY в настройках сервера.",
                )
                return
            message_text = await gemini_transcribe_voice(voice_bytes, voice_mime)
            message_text = (message_text or "").strip()
            if not message_text:
                await send_message(
                    phone,
                    "Не разобрал голосовое. Повторите чётче или напишите текстом, пожалуйста.",
                )
                return
            voice_bytes = None

        user_evt = message_text if message_text.strip() else (
            "🎤 голосовое сообщение" if voice_bytes is not None else ""
        )
        await publish_event("new_message", {
            "phone": phone, "role": "user", "content": user_evt,
        })

        # ─── HUMAN_MODE / ai_paused: AI молчит, только логируем ─────────
        # Состояние в Redis: ключ user:state:{phone} (см. dialog_mgr.get_user_state), не вызываем Gemini.
        if state == UserState.HUMAN_MODE or ai_paused_db:
            async with async_session_factory() as db:
                await _save_chat_log(db, phone, message_text, "[HUMAN_MODE — AI отключён]")
                await db.commit()
            await append_to_history(redis_client, phone, "user", message_text)
            logger.info("HUMAN_MODE: сообщение от %s сохранено, AI не вызван", phone)
            return

        # ─── AWAITING_ORDER_PAYMENT: способ оплаты → затем Да/Нет ───
        if state == UserState.AWAITING_ORDER_PAYMENT:
            final_reply = await handle_order_payment_choice(phone, message_text)

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
        had_voice = voice_bytes is not None
        history = await get_chat_history(redis_client, phone)

        if had_voice:
            if not (settings.gemini_api_key or "").strip():
                await send_message(
                    phone,
                    "Голосовые недоступны: задайте GEMINI_API_KEY в настройках сервера.",
                )
                return
        else:
            await append_to_history(redis_client, phone, "user", message_text)

        async with async_session_factory() as db:
            menu_items = await load_available_menu(db)
            menu_context = build_menu_context(menu_items)
            u_row = await db.scalar(select(User).where(User.phone == phone))
            org_id = u_row.organization_id if u_row else None
            kb_context = await load_knowledge_context_block(db, org_id)
            if had_voice:
                if voice_bytes is None:
                    return
                ai_response = await call_gemini_with_audio(
                    history, voice_bytes, voice_mime, menu_context, kb_context,
                )
                user_log_text = (ai_response.recognized_speech or "").strip() or "🎤 голосовое сообщение"
                await append_to_history(redis_client, phone, "user", user_log_text)
            else:
                user_log_text = message_text
                ai_response = await call_gemini(history, message_text, menu_context, kb_context)

            result = await route_intent(
                db, phone, ai_response, menu_items=menu_items,
            )

            if result.new_state:
                await set_user_state(redis_client, phone, result.new_state)
            if result.pending_order_id:
                await set_pending_order(redis_client, phone, result.pending_order_id)
            if result.pending_booking_id:
                await set_pending_booking(redis_client, phone, result.pending_booking_id)

            assistant_meta = {
                "intent": ai_response.intent,
                "monologue": (
                    f"Распознан интент: {ai_response.intent}. "
                    "Ответ сформирован моделью (Gemini)."
                ),
            }
            await _save_chat_log(
                db, phone, user_log_text, result.reply_text, assistant_meta,
            )
            if result.new_state == UserState.HUMAN_MODE:
                db.add(
                    EscalationEvent(
                        phone=phone,
                        user_message=(user_log_text or "")[:2000],
                        reason=(result.reply_text or "")[:2000],
                    ),
                )
            await db.commit()

        await append_to_history(redis_client, phone, "assistant", result.reply_text)
        await send_message(phone, result.reply_text)

        if had_voice and settings.whatsapp_voice_replies:
            try:
                mp3 = await synthesize_speech_mp3(
                    result.reply_text,
                    language_code=ai_response.detected_language,
                )
                if mp3:
                    await send_voice_message(phone, mp3)
            except Exception as tts_exc:
                logger.warning("edge-tts / отправка голоса: %s", tts_exc)

        await publish_event("new_message", {
            "phone": phone, "role": "assistant", "content": result.reply_text,
            "intent": ai_response.intent,
        })

        if result.new_state == UserState.HUMAN_MODE:
            await publish_event("human_needed", {
                "phone": phone,
                "reason": ai_response.reply_text,
                "user_message": (user_log_text or "")[:500],
                "intent": ai_response.intent,
            })
            try:
                await send_tg_fallback_alert(phone, user_log_text, result.reply_text)
            except Exception as tg_exc:
                logger.warning("Telegram fallback alert не отправлен: %s", tg_exc)

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


async def process_voice_message(phone: str, media_id: str) -> None:
    """
    Голосовое WhatsApp: скачать → Gemini (мультимодально в CHATTING или транскрипт в подтверждениях).
    """
    try:
        if not (settings.gemini_api_key or "").strip():
            logger.info("Голосовое от %s: GEMINI_API_KEY не задан", phone)
            await send_message(
                phone,
                "Голосовые сообщения пока не настроены. Настройте GEMINI_API_KEY на сервере или напишите текстом.",
            )
            return

        downloaded = await download_media_bytes(media_id)
        if not downloaded:
            await send_message(
                phone,
                "Не удалось получить аудио. Попробуйте ещё раз или напишите текстом.",
            )
            return

        audio_bytes, mime_type = downloaded
        logger.info("Голосовое от %s, mime=%s, %d байт", phone, mime_type, len(audio_bytes))
        await process_message(phone, "", voice_audio=(audio_bytes, mime_type))
    except Exception as exc:
        logger.exception("Ошибка обработки голосового от %s: %s", phone, exc)
        try:
            await send_message(
                phone,
                "Произошла ошибка при обработке голосового. Напишите текстом — я на связи.",
            )
        except Exception:
            logger.error("Не удалось отправить сообщение об ошибке голоса → %s", phone)


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
        messages = value.get("messages", []) or []

        for msg in messages:
            if not isinstance(msg, dict):
                continue
            phone = (msg.get("from") or "").strip()
            msg_type = (msg.get("type") or "").strip().lower()
            message_id = (msg.get("id") or "").strip()

            if not phone:
                continue

            if message_id and await _dedupe_whatsapp_message(message_id):
                logger.info("Дубликат WhatsApp message_id=%s от %s — пропущен", message_id, phone)
                continue

            if msg_type == "audio":
                media_id = (msg.get("audio") or {}).get("id") or ""
                if media_id:
                    background_tasks.add_task(process_voice_message, phone, media_id)
                    logger.info("Голосовое от %s поставлено в очередь", phone)
            else:
                message_text = (msg.get("text") or {}).get("body") or ""
                if message_text:
                    background_tasks.add_task(process_message, phone, message_text)
                    logger.info("Сообщение от %s поставлено в очередь обработки", phone)

    except (IndexError, KeyError, TypeError) as exc:
        logger.error("Ошибка парсинга вебхука: %s", exc)

    return {"status": "ok"}
