"""
Роутер для входящих вебхуков WhatsApp.
Принимает сообщения, мгновенно возвращает 200 OK,
а обработку передаёт в BackgroundTasks.
"""

import asyncio
import base64
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import re
from fastapi import APIRouter, BackgroundTasks, Query, Request, Response, WebSocket
from starlette.websockets import WebSocketDisconnect

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.pipeline_log import log_pipeline_stage
from app.core.rate_limiter import check_rate_limit
from app.db.models import ChatLog, EscalationEvent, FailedTask, Order, OrderStatus, User
from app.db.session import async_session_factory, redis_client
from app.integrations.iiko_client import IikoClient
from app.integrations.telegram import send_tg_fallback_alert
from app.integrations.twilio_client import verify_twilio_signature
from app.integrations.twilio_media import mulaw_8k_to_wav
from app.integrations.whatsapp import download_media_bytes, send_template, send_voice_message
from app.services.ai_brain import call_openai, call_openai_with_audio, openai_transcribe_voice
from app.services.chat_delivery import apply_whatsapp_status_webhook
from app.services.customer_context import build_customer_context
from app.services.customer_reply import (
    reset_twilio_call_context,
    send_customer_text,
    twilio_call_context,
)
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
    get_open_draft_order,
    get_or_create_user,
    route_intent,
)
from app.services.knowledge_context import load_knowledge_context_block
from app.services.order_logic import (
    build_menu_context,
    build_summary_text_from_stored_items,
    detect_payment_method_from_text,
    format_draft_order_context_for_prompt,
    format_whatsapp_order_card,
    load_available_menu,
    merge_total_into_items_json,
)
from app.services.sales_strategy import build_sales_strategy, format_strategy_for_prompt
from app.services.whatsapp_idempotency import try_claim_whatsapp_inbound_in_db
from app.services.tts_edge import synthesize_speech_mp3

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/whatsapp", tags=["WhatsApp Webhook"])

WHATSAPP_DEDUPE_TTL_SECONDS = 15 * 60

# Twilio CallSid → From (E.164), если Redis выключен — только один инстанс
_twilio_caller_memory: dict[str, str] = {}


def _twilio_stream_wss_url() -> str:
    """WSS URL для <Stream> (нужен PUBLIC_BASE_URL с https)."""
    base = (settings.public_base_url or "").strip().rstrip("/")
    if not base:
        return ""
    if base.startswith("https://"):
        rest = base[len("https://") :]
        return f"wss://{rest}/api/whatsapp/voice/stream"
    if base.startswith("http://"):
        rest = base[len("http://") :]
        return f"ws://{rest}/api/whatsapp/voice/stream"
    return f"wss://{base}/api/whatsapp/voice/stream"


async def _store_twilio_caller(call_sid: str, phone: str) -> None:
    if not call_sid or not phone:
        return
    key = f"twilio:caller:{call_sid}"
    if settings.redis_enabled:
        try:
            await redis_client.setex(key, 600, phone)
            return
        except Exception as exc:
            logger.warning("Redis twilio caller cache: %s", exc)
    _twilio_caller_memory[call_sid] = phone


async def _get_twilio_caller(call_sid: str) -> str:
    if not call_sid:
        return ""
    key = f"twilio:caller:{call_sid}"
    if settings.redis_enabled:
        try:
            raw = await redis_client.get(key)
            if raw:
                return str(raw).strip()
        except Exception:
            pass
    return (_twilio_caller_memory.get(call_sid) or "").strip()


async def _dedupe_whatsapp_message(message_id: str) -> bool:
    """
    Защита от дублей вебхука Meta.

    Meta может доставлять одно и то же сообщение несколько раз, поэтому без идемпотентности
    в админке появляются дубли (и расходуется лимит OpenAI).

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


def _normalize_phone_e164(phone: str) -> str:
    """
    iiko deliveries/create строго валидирует customer.phone.
    В БД/вебхуке у нас телефон обычно хранится как '7705...' (без '+').
    Приводим к E.164: '+7705...'.
    """
    raw = (phone or "").strip()
    if raw.startswith("+"):
        return raw
    digits = re.sub(r"\D+", "", raw)
    if not digits:
        return ""
    # Казахстан/Россия: +7XXXXXXXXXX (11 цифр)
    if len(digits) == 11 and digits.startswith("7"):
        return f"+{digits}"
    # Если дали 10 цифр без кода страны — попробуем трактовать как KZ/RU
    if len(digits) == 10:
        return f"+7{digits}"
    return f"+{digits}"



async def _save_chat_log(
    db: AsyncSession,
    phone: str,
    user_text: str,
    reply_text: str,
    assistant_meta: dict | None = None,
    *,
    outbound_whatsapp: bool = True,
) -> int | None:
    """
    Сохраняет пару сообщений (user + assistant) в ChatLog.
    Для исходящего ответа в WhatsApp — assistant со статусом sending; возвращает id строки assistant.
    """
    user = await get_or_create_user(db, phone)
    db.add(ChatLog(user_id=user.id, role="user", content=user_text))
    now = datetime.now(timezone.utc)
    assistant_kwargs: dict[str, Any] = {
        "user_id": user.id,
        "role": "assistant",
        "content": reply_text,
        "meta_json": assistant_meta,
    }
    if outbound_whatsapp:
        assistant_kwargs["delivery_status"] = "sending"
        assistant_kwargs["status_updated_at"] = now
    assistant_log = ChatLog(**assistant_kwargs)
    db.add(assistant_log)
    await db.flush()
    return int(assistant_log.id) if outbound_whatsapp else None


async def _process_whatsapp_status_batch(statuses: list[Any]) -> None:
    """Фон: обновление delivery по массиву statuses из вебхука Meta."""
    for raw in statuses:
        if not isinstance(raw, dict):
            continue
        mid = (raw.get("id") or "").strip()
        st = (raw.get("status") or "").strip().lower()
        if not mid or not st:
            continue
        errs = raw.get("errors")
        try:
            async with async_session_factory() as db:
                await apply_whatsapp_status_webhook(db, mid, st, errs)
                await db.commit()
        except Exception as exc:
            logger.warning("WhatsApp status update failed for %s: %s", mid[:20], exc)


async def _send_order_to_iiko(
    order_id: int,
    phone: str,
    items_json: dict[str, Any] | None,
) -> tuple[bool, str | None, dict[str, Any] | None]:
    """
    Попытка отправить подтверждённый заказ в iiko.

    Returns:
        (True, None, response_json) — успех; response_json — тело ответа iiko (correlationId, orderInfo…).
        (False, None, None) — iiko не настроен в .env (ошибку в БД не пишем).
        (False, msg, None) — настроен, но отправка не удалась (msg для админки).
    """
    if not settings.iiko_api_login or not settings.iiko_organization_id:
        logger.info("iiko не настроен — заказ сохранён только в БД")
        log_pipeline_stage("iiko_skip", phone=phone, extra={"order_id": order_id, "reason": "not_configured"})
        return False, None, None

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
            return False, msg, None

        meta = items_json.get("order_meta") if items_json else {}
        pd = meta.get("payment_details")
        pay_note = meta.get("payment_method", "?")
        if isinstance(pd, dict) and pd.get("type") == "mixed":
            sp = pd.get("split") or {}
            pay_note = f"mixed remote={sp.get('remote', 0)} card={sp.get('card', 0)} cash={sp.get('cash', 0)}"
        comment_bits = [
            f"Заказ #{order_id} · RestoMind · WhatsApp",
            f"тип: {meta.get('order_type', '?')}",
            f"оплата: {pay_note}",
        ]
        if meta.get("delivery_address"):
            comment_bits.append(f"адрес: {meta['delivery_address'][:200]}")
        comment = " · ".join(comment_bits)

        terminal_group = (settings.iiko_terminal_group_id or "").strip()
        ot_raw = (meta.get("order_type") or "").strip().lower()
        ot = "hall" if settings.iiko_force_hall_for_ai_orders else ot_raw
        if ot == "delivery":
            order_type_id = (settings.iiko_order_type_id_delivery or "").strip()
        elif ot == "pickup":
            order_type_id = (settings.iiko_order_type_id_pickup or "").strip()
        elif ot == "hall":
            order_type_id = (settings.iiko_order_type_id_hall or "").strip()
        else:
            order_type_id = ""
        if not order_type_id:
            order_type_id = (settings.iiko_order_type_id or "").strip()
        if not order_type_id:
            msg = (
                "Не задан orderTypeId для iiko. Укажите IIKO_ORDER_TYPE_ID "
                "или раздельные IIKO_ORDER_TYPE_ID_DELIVERY/PICKUP/HALL"
            )
            logger.warning("Заказ #%d: %s", order_id, msg)
            return False, msg, None
        phone_e164 = _normalize_phone_e164(phone)
        if not phone_e164:
            msg = "Телефон клиента пустой — iiko deliveries/create требует customer.phone"
            logger.warning("Заказ #%d: %s", order_id, msg)
            return False, msg, None
        async with IikoClient(api_login=settings.iiko_api_login) as client:
            iiko_response = await client.create_delivery_order(
                organization_id=settings.iiko_organization_id,
                order_data={
                    "customer": {"phone": phone_e164},
                    # Некоторые конфигурации iiko валидируют телефон на верхнем уровне заказа.
                    # Дублируем, чтобы избежать 400 "Parameter 'phone'" при корректном customer.phone.
                    "phone": phone_e164,
                    # Стабильный номер для поиска в iiko (например, "1456").
                    "externalNumber": str(order_id),
                    "items": iiko_items,
                    "comment": comment[:1000],
                    "orderTypeId": order_type_id,
                },
                terminal_group_id=terminal_group or None,
            )
        log_pipeline_stage("iiko_ok", phone=phone, extra={"order_id": order_id})
        return True, None, iiko_response if isinstance(iiko_response, dict) else None
    except Exception as exc:
        logger.error("Ошибка отправки заказа #%d в iiko: %s", order_id, exc, exc_info=True)
        msg = str(exc).strip() or type(exc).__name__
        log_pipeline_stage(
            "iiko_err",
            phone=phone,
            extra={"order_id": order_id, "error": msg[:500]},
        )
        return False, msg[:500], None


async def handle_confirmation(phone: str, message_text: str) -> str | None:
    """
    Обработка ответа на подтверждение заказа.
    При «Да» → подтверждаем. При «Нет» → отменяем.
    Иначе → None (клиент хочет изменить заказ — process_message перенаправит в LLM).
    """
    word = message_text.lower().strip().rstrip("!.,")
    order_id = await get_pending_order(redis_client, phone)

    if not order_id:
        await clear_pending_order(redis_client, phone)
        return "Заказ не найден — возможно, истекло время ожидания. Назовите блюда заново."

    if word in CONFIRM_WORDS:
        async with async_session_factory() as db:
            order_row = await db.get(Order, order_id)
            if order_row and order_row.items_json:
                om = (order_row.items_json.get("order_meta") or {})
                if om.get("requires_order_prepayment"):
                    pst = (order_row.prepayment_status or "").strip().lower()
                    if pst not in ("paid", "waived"):
                        return (
                            "По сумме заказа нужна **предоплата**. Пока платёж не отмечен оператором, "
                            "подтвердить заказ нельзя — как только оплатите, оператор отметит в системе, "
                            "и вы сможете ответить «Да» ещё раз."
                        )

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
        ij = order.items_json if isinstance(order.items_json, dict) else {}
        ij = merge_total_into_items_json(ij, float(order.total_price or 0))
        summary_core = build_summary_text_from_stored_items(ij)
        card = format_whatsapp_order_card(ij, summary_core)
        return (
            f"✨ *Заказ #{order.id} подтверждён!*\n\n"
            f"{card}\n\n"
            "_Оператор проверит детали и отправит заказ в iiko — при необходимости с вами свяжутся._"
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

    return None  # не «Да» и не «Нет» — вернём None, process_message пропустит через LLM


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


async def handle_order_payment_choice(phone: str, message_text: str) -> str | None:
    """
    После черновика заказа: фиксируем способ оплаты в items_json, затем переход к Да/Нет.
    None → текст не распознан как оплата/отмена — process_message перенаправит в LLM.
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
        return None  # не способ оплаты и не отмена — вернём None, process_message пропустит через LLM

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
        body = format_whatsapp_order_card(items_json, summary_core)
        pay_human = _PAYMENT_LABEL_RU.get(pm, pm)
        reply = (
            f"Принял способ оплаты: {pay_human}.\n\n"
            f"{body}\n\n"
            "✅ Подтверждаете заказ? (Да / Нет)"
        )
        await db.commit()

        total = float(order.total_price)
        meta_after = (order.items_json or {}).get("order_meta") or {}
        needs_prepay = bool(meta_after.get("requires_order_prepayment"))
        prep_st = (order.prepayment_status or "").strip().lower()

        await publish_event("order_updated", {
            "order_id": order.id,
            "status": OrderStatus.DRAFT,
            "phone": phone,
            "total_price": total,
            "payment_method": pm,
        })

    if needs_prepay and prep_st not in ("paid", "waived"):
        await set_user_state(redis_client, phone, UserState.CHATTING)
        return (
            f"Принял способ оплаты: {pay_human}.\n\n"
            f"{body}\n\n"
            f"💳 Сумма от **{int(settings.order_prepayment_threshold_kzt):,}** ₸ — нужна предоплата. "
            "Оператор пришлёт реквизиты или ссылку. После оплаты вы сможете подтвердить заказ ответом «Да»."
        )

    await set_user_state(redis_client, phone, UserState.CONFIRMING_ORDER)
    return reply


MAX_RETRIES = 3


async def _save_failed_task(phone: str, text: str, error: str, attempts: int) -> None:
    """Best-effort: сохранить необработанное сообщение для диагностики."""
    try:
        async with async_session_factory() as db:
            db.add(FailedTask(phone=phone, message_text=text[:4000], error=error[:2000], attempts=attempts))
            await db.commit()
    except Exception as exc:
        logger.error("Не удалось сохранить FailedTask для %s: %s", phone, exc)


async def process_with_retry(
    phone: str,
    message_text: str = "",
    *,
    whatsapp_message_id: str = "",
    voice_audio: tuple[bytes, str] | None = None,
) -> None:
    """
    Обёртка с retry + exponential backoff.
    После MAX_RETRIES неудач → сохраняет в FailedTask и извиняется перед клиентом.
    """
    last_exc: BaseException | None = None
    for attempt in range(MAX_RETRIES):
        try:
            await process_message(
                phone, message_text,
                whatsapp_message_id=whatsapp_message_id,
                voice_audio=voice_audio,
            )
            return
        except Exception as exc:
            last_exc = exc
            logger.warning(
                "Retry %d/%d для %s: %s", attempt + 1, MAX_RETRIES, phone, exc,
            )
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(2 ** attempt)

    logger.error("Все %d попыток исчерпаны для %s: %s", MAX_RETRIES, phone, last_exc)
    log_pipeline_stage(
        "failed_queue",
        phone=phone,
        extra={"error": str(last_exc)[:500] if last_exc else "", "attempts": MAX_RETRIES},
    )
    await _save_failed_task(phone, message_text, str(last_exc), MAX_RETRIES)
    try:
        await send_customer_text(phone, "Извините, произошла техническая ошибка. Мы уже работаем над ней — попробуйте написать чуть позже.")
    except Exception:
        pass


async def process_message(
    phone: str,
    message_text: str = "",
    *,
    whatsapp_message_id: str = "",
    voice_audio: tuple[bytes, str] | None = None,
) -> None:
    """
    Полный цикл обработки входящего сообщения с учётом State Machine.
    """
    try:
        if not await check_rate_limit(phone):
            logger.warning("Rate limit: %s заблокирован", phone)
            await send_customer_text(phone, "Слишком много сообщений. Подождите минуту и попробуйте снова.")
            return

        wmid = (whatsapp_message_id or "").strip()
        if wmid:
            async with async_session_factory() as dedupe_db:
                if not await try_claim_whatsapp_inbound_in_db(
                    dedupe_db, message_id=wmid, phone=phone,
                ):
                    logger.info("Повтор message_id=%s (БД) — обработка не запускается", wmid)
                    return
                await dedupe_db.commit()

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
            if not (settings.openai_api_key or "").strip():
                await send_customer_text(
                    phone,
                    "Голосовые недоступны: задайте OPENAI_API_KEY в настройках сервера.",
                )
                return
            message_text = await openai_transcribe_voice(voice_bytes, voice_mime)
            message_text = (message_text or "").strip()
            if not message_text:
                await send_customer_text(
                    phone,
                    "Не разобрал голосовое. Повторите чётче или напишите текстом, пожалуйста.",
                )
                return
            log_pipeline_stage("stt_ok", phone=phone, extra={"len": len(message_text)})
            voice_bytes = None

        user_evt = message_text if message_text.strip() else (
            "🎤 голосовое сообщение" if voice_bytes is not None else ""
        )
        await publish_event("new_message", {
            "phone": phone, "role": "user", "content": user_evt,
        })

        # ─── HUMAN_MODE / ai_paused: AI молчит, только логируем ─────────
        # Состояние в Redis: ключ user:state:{phone} (см. dialog_mgr.get_user_state), не вызываем LLM.
        if state == UserState.HUMAN_MODE or ai_paused_db:
            async with async_session_factory() as db:
                await _save_chat_log(
                    db, phone, message_text, "[HUMAN_MODE — AI отключён]",
                    outbound_whatsapp=False,
                )
                await db.commit()
            await append_to_history(redis_client, phone, "user", message_text)
            logger.info("HUMAN_MODE: сообщение от %s сохранено, AI не вызван", phone)
            return

        # ─── AWAITING_ORDER_PAYMENT: способ оплаты, правка заказа или Да/Нет ───
        if state == UserState.AWAITING_ORDER_PAYMENT:
            final_reply = await handle_order_payment_choice(phone, message_text)

            if final_reply is not None:
                outbound_id: int | None = None
                async with async_session_factory() as db:
                    outbound_id = await _save_chat_log(db, phone, message_text, final_reply)
                    await db.commit()

                await append_to_history(redis_client, phone, "user", message_text)
                await append_to_history(redis_client, phone, "assistant", final_reply)
                await publish_event("new_message", {
                    "phone": phone,
                    "role": "assistant",
                    "content": final_reply,
                    "id": outbound_id,
                    "delivery_status": "sending",
                })
                await send_customer_text(
                    phone, final_reply, outbound_chat_log_id=outbound_id,
                )
                return
            # None → клиент хочет изменить заказ; переключаем в CHATTING и идём в OpenAI
            await set_user_state(redis_client, phone, UserState.CHATTING)
            state = UserState.CHATTING

        # ─── CONFIRMING_ORDER: ждём Да/Нет или правку заказа ──────
        if state == UserState.CONFIRMING_ORDER:
            final_reply = await handle_confirmation(phone, message_text)

            if final_reply is not None:
                outbound_id_o: int | None = None
                async with async_session_factory() as db:
                    outbound_id_o = await _save_chat_log(db, phone, message_text, final_reply)
                    await db.commit()

                await append_to_history(redis_client, phone, "user", message_text)
                await append_to_history(redis_client, phone, "assistant", final_reply)
                await publish_event("new_message", {
                    "phone": phone,
                    "role": "assistant",
                    "content": final_reply,
                    "id": outbound_id_o,
                    "delivery_status": "sending",
                })
                await send_customer_text(
                    phone, final_reply, outbound_chat_log_id=outbound_id_o,
                )
                return
            # None → клиент хочет изменить заказ; переключаем в CHATTING и идём в OpenAI
            await set_user_state(redis_client, phone, UserState.CHATTING)
            state = UserState.CHATTING

        # ─── CONFIRMING_BOOKING: ждём Да/Нет ─────────────────
        if state == UserState.CONFIRMING_BOOKING:
            final_reply = await handle_booking_confirmation(phone, message_text)

            outbound_id_b: int | None = None
            async with async_session_factory() as db:
                outbound_id_b = await _save_chat_log(db, phone, message_text, final_reply)
                await db.commit()

            await append_to_history(redis_client, phone, "user", message_text)
            await append_to_history(redis_client, phone, "assistant", final_reply)
            await publish_event("new_message", {
                "phone": phone,
                "role": "assistant",
                "content": final_reply,
                "id": outbound_id_b,
                "delivery_status": "sending",
            })
            await send_customer_text(
                phone, final_reply, outbound_chat_log_id=outbound_id_b,
            )
            return

        # ─── CHATTING: обычный AI-флоу ──────────────────────
        had_voice = voice_bytes is not None
        history = await get_chat_history(redis_client, phone)

        if had_voice:
            if not (settings.openai_api_key or "").strip():
                await send_customer_text(
                    phone,
                    "Голосовые недоступны: задайте OPENAI_API_KEY в настройках сервера.",
                )
                return
        else:
            await append_to_history(redis_client, phone, "user", message_text)

        outbound_id_chat: int | None = None
        async with async_session_factory() as db:
            menu_items = await load_available_menu(db)
            menu_context = build_menu_context(menu_items)
            u_row = await db.scalar(select(User).where(User.phone == phone))
            org_id = u_row.organization_id if u_row else None
            customer_ctx = await build_customer_context(db, u_row)
            kb_context = await load_knowledge_context_block(db, org_id)
            draft_row = await get_open_draft_order(db, phone)
            draft_ctx = format_draft_order_context_for_prompt(
                draft_row.items_json if draft_row else None,
            )
            strategy_ctx = ""
            if draft_row and isinstance(draft_row.items_json, dict):
                cart = [
                    x for x in (draft_row.items_json.get("items") or [])
                    if isinstance(x, dict)
                ]
                om = draft_row.items_json.get("order_meta")
                meta_d = om if isinstance(om, dict) else {}
                total = float(draft_row.total_price or 0)
                strategy_ctx = format_strategy_for_prompt(
                    build_sales_strategy(cart, total, meta_d, menu_items),
                )
            if had_voice:
                if voice_bytes is None:
                    return
                ai_response = await call_openai_with_audio(
                    history,
                    voice_bytes,
                    voice_mime,
                    menu_context,
                    kb_context,
                    draft_order_context=draft_ctx,
                    sales_strategy_context=strategy_ctx,
                    customer_context=customer_ctx,
                )
                user_log_text = (ai_response.recognized_speech or "").strip() or "🎤 голосовое сообщение"
                await append_to_history(redis_client, phone, "user", user_log_text)
            else:
                user_log_text = message_text
                ai_response = await call_openai(
                    history,
                    message_text,
                    menu_context,
                    kb_context,
                    draft_order_context=draft_ctx,
                    sales_strategy_context=strategy_ctx,
                    customer_context=customer_ctx,
                )

            log_pipeline_stage(
                "llm_ok",
                phone=phone,
                extra={"intent": ai_response.intent, "voice": had_voice},
            )
            result = await route_intent(
                db, phone, ai_response, menu_items=menu_items,
            )
            log_pipeline_stage(
                "route_ok",
                phone=phone,
                extra={
                    "intent": ai_response.intent,
                    "pending_order_id": result.pending_order_id,
                },
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
                    "Ответ сформирован моделью (OpenAI)."
                ),
            }
            outbound_id_chat = await _save_chat_log(
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
        await publish_event("new_message", {
            "phone": phone,
            "role": "assistant",
            "content": result.reply_text,
            "intent": ai_response.intent,
            "id": outbound_id_chat,
            "delivery_status": "sending",
        })
        await send_customer_text(
            phone, result.reply_text, outbound_chat_log_id=outbound_id_chat,
        )

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
        # Не глотаем исключение: иначе process_with_retry не сможет записать FailedTask и сделать backoff.
        # Сообщение клиенту после исчерпания попыток отправляет process_with_retry.
        raise


async def process_voice_message(
    phone: str,
    media_id: str,
    *,
    whatsapp_message_id: str = "",
) -> None:
    """
    Голосовое WhatsApp: скачать → Whisper (STT) + чат OpenAI в CHATTING или только STT в подтверждениях.
    """
    try:
        if not (settings.openai_api_key or "").strip():
            logger.info("Голосовое от %s: OPENAI_API_KEY не задан", phone)
            await send_customer_text(
                phone,
                "Голосовые сообщения пока не настроены. Настройте OPENAI_API_KEY на сервере или напишите текстом.",
            )
            return

        downloaded = await download_media_bytes(media_id)
        if not downloaded:
            await send_customer_text(
                phone,
                "Не удалось получить аудио. Попробуйте ещё раз или напишите текстом.",
            )
            return

        audio_bytes, mime_type = downloaded
        logger.info("Голосовое от %s, mime=%s, %d байт", phone, mime_type, len(audio_bytes))
        await process_with_retry(
            phone,
            "",
            whatsapp_message_id=whatsapp_message_id,
            voice_audio=(audio_bytes, mime_type),
        )
    except Exception as exc:
        logger.exception("Ошибка обработки голосового от %s: %s", phone, exc)
        try:
            await send_customer_text(
                phone,
                "Произошла ошибка при обработке голосового. Напишите текстом — я на связи.",
            )
        except Exception:
            logger.error("Не удалось отправить сообщение об ошибке голоса → %s", phone)


async def _flush_twilio_voice_chunk(phone: str, call_sid: str, mulaw: bytes) -> None:
    """
    μ-law → WAV → Whisper STT → тот же пайплайн, что WhatsApp (process_message).
    Ответ уходит в TwiML Say, если задан контекст CallSid.
    """
    if not mulaw or not phone or not call_sid:
        return
    if not (settings.openai_api_key or "").strip():
        logger.warning("Twilio voice: нет OPENAI_API_KEY")
        return
    wav = mulaw_8k_to_wav(mulaw)
    text = await openai_transcribe_voice(wav, "audio/wav")
    text = (text or "").strip()
    if not text:
        return
    mid = f"twilio:{call_sid}:{uuid.uuid4().hex}"
    tok = twilio_call_context(call_sid)
    try:
        await process_message(phone, text, whatsapp_message_id=mid)
    finally:
        reset_twilio_call_context(tok)


@router.post("/voice/incoming")
async def twilio_voice_incoming(request: Request) -> Response:
    """
    Twilio Voice: «A CALL COMES IN» → этот URL.
    Возвращает TwiML: приветствие + <Stream> на WebSocket (нужен PUBLIC_BASE_URL).
    """
    form = await request.form()
    params: dict[str, str] = {str(k): str(v) for k, v in form.items()}
    token = (settings.twilio_auth_token or "").strip()
    if token:
        sig = request.headers.get("X-Twilio-Signature")
        if not verify_twilio_signature(str(request.url), params, sig, token):
            logger.warning("Twilio signature verification failed")
            return Response(content="Forbidden", status_code=403)

    call_sid = (params.get("CallSid") or "").strip()
    from_phone = (params.get("From") or "").strip()
    if call_sid and from_phone:
        await _store_twilio_caller(call_sid, from_phone)

    wss = _twilio_stream_wss_url()
    if not wss:
        twiml = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say language="ru-RU">Сервер не настроен. Укажите PUBLIC_BASE_URL с адресом сайта по протоколу HTTPS.</Say>
    <Hangup/>
</Response>"""
        return Response(content=twiml.strip(), media_type="application/xml")

    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say language="ru-RU" voice="Polly.Tatyana">Здравствуйте! Слушаю ваш запрос.</Say>
    <Connect>
        <Stream url="{wss}" />
    </Connect>
</Response>"""
    return Response(content=twiml.strip(), media_type="application/xml")


@router.websocket("/voice/stream")
async def twilio_voice_stream(websocket: WebSocket) -> None:
    """
    Twilio Media Streams (входящий μ-law 8 kHz).
    Накопление буфера → транскрипт Whisper → process_message → Twilio Say (см. customer_reply).
    """
    await websocket.accept()
    buf = bytearray()
    call_sid = ""
    phone = ""
    processing = False
    threshold = int(settings.twilio_voice_buffer_bytes)

    async def maybe_flush(force: bool = False) -> None:
        nonlocal buf, processing
        if processing or not phone or not call_sid:
            return
        if not force and len(buf) < threshold:
            return
        if not buf:
            return
        processing = True
        chunk = bytes(buf)
        buf.clear()
        try:
            await _flush_twilio_voice_chunk(phone, call_sid, chunk)
        finally:
            processing = False

    try:
        while True:
            try:
                raw = await websocket.receive_text()
            except WebSocketDisconnect:
                break
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            ev = data.get("event")
            if ev == "connected":
                continue
            if ev == "start":
                st = data.get("start") or {}
                call_sid = (st.get("callSid") or data.get("callSid") or "").strip()
                phone = await _get_twilio_caller(call_sid) if call_sid else ""
                logger.info(
                    "Twilio stream start callSid=%s phone=%s",
                    (call_sid[:16] + "…") if len(call_sid) > 16 else call_sid,
                    phone,
                )
                continue
            if ev == "media":
                media = data.get("media") or {}
                b64 = media.get("payload") or ""
                if b64:
                    try:
                        buf.extend(base64.b64decode(b64))
                    except Exception:
                        pass
                await maybe_flush(force=False)
                continue
            if ev == "stop":
                await maybe_flush(force=True)
                break
    finally:
        if phone and call_sid and buf and not processing:
            await _flush_twilio_voice_chunk(phone, call_sid, bytes(buf))


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
        statuses = value.get("statuses", []) or []
        if statuses:
            background_tasks.add_task(_process_whatsapp_status_batch, list(statuses))

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
                    background_tasks.add_task(
                        process_voice_message,
                        phone,
                        media_id,
                        whatsapp_message_id=message_id,
                    )
                    logger.info("Голосовое от %s поставлено в очередь", phone)
            else:
                message_text = (msg.get("text") or {}).get("body") or ""
                if message_text:
                    background_tasks.add_task(
                        process_with_retry,
                        phone,
                        message_text,
                        whatsapp_message_id=message_id,
                    )
                    logger.info("Сообщение от %s поставлено в очередь обработки", phone)

    except (IndexError, KeyError, TypeError) as exc:
        logger.error("Ошибка парсинга вебхука: %s", exc)

    return {"status": "ok"}
