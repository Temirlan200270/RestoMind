"""
Роутер для входящих вебхуков WhatsApp.
Принимает сообщения, мгновенно возвращает 200 OK,
а обработку передаёт в очередь ARQ (worker).
"""

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import re
import secrets
from fastapi import APIRouter, BackgroundTasks, Query, Request, Response, WebSocket
from starlette.websockets import WebSocketDisconnect

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.pipeline_log import log_pipeline_stage
from app.core.pipeline_timing import PipelineStopwatch, log_pipeline_rm_stage_ms
from app.core.rate_limiter import check_rate_limit
from app.db.models import ChatLog, EscalationEvent, FailedTask, Order, OrderStatus, Organization, User
from app.db.session import async_session_factory, redis_client
from app.integrations.iiko_client import IikoClient
from app.integrations.telegram import EscalationAlertExtras, send_tg_fallback_alert
from app.integrations.twilio_client import verify_twilio_signature
from app.integrations.twilio_media import mulaw_8k_to_wav
from app.integrations.whatsapp import download_media_bytes, send_voice_message
from app.services.prepayment_legal import append_prepayment_legal_disclaimer
from app.services.ai_brain import (
    call_ai_with_audio,
    call_openai,
    is_openai_fallback_escalation_reply,
    transcribe_voice,
    voice_supported,
)
from app.services.chat_delivery import apply_whatsapp_status_webhook
from app.services.context_engine import fetch_ai_read_context
from app.services.restaurant_context_cache import cached_format_org_current_time_block
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
    sync_user_dialog_state_to_db_then_redis,
    update_user_session_fields_in_db,
)
from app.services.ai_usage import schedule_log_ai_error, schedule_log_ai_usage
from app.services.pipeline_latency import schedule_log_pipeline_latency
from app.services.events import publish_event
from app.services.billing_guard import tenant_billing_blocks_inbound
from app.services.org_resolve import organization_id_for_whatsapp_value
from app.services.intent_router import (
    cancel_booking,
    cancel_order,
    confirm_booking,
    confirm_order,
    get_or_create_user,
    route_intent,
)
from app.services.order_logic import (
    build_menu_context_for_ai,
    build_summary_text_from_stored_items,
    detect_payment_method_from_text,
    format_draft_order_context_for_prompt,
    format_whatsapp_order_card,
    merge_total_into_items_json,
)
from app.services.sales_strategy import build_sales_strategy, format_strategy_for_prompt
from app.services.whatsapp_idempotency import (
    cache_whatsapp_inbound_done_redis,
    mark_whatsapp_inbound_done,
    mark_whatsapp_inbound_failed,
    redis_whatsapp_inbound_done_cache_hit,
    try_start_whatsapp_inbound_in_db,
)
from app.services.tts_edge import synthesize_speech_mp3
from app.services.trace_context import build_conversation_id, build_trace_id, merge_trace_meta, trace_payload

logger = logging.getLogger(__name__)


def _redact_msisdn_for_log(phone: str) -> str:
    """Минимизация PII в логах: хвост номера вместо полного E.164."""
    p = (phone or "").strip()
    if len(p) <= 4:
        return "***"
    return f"…{p[-4:]}"


def _verify_whatsapp_hub_signature256(raw_body: bytes, signature_header: str | None) -> bool:
    """Проверка X-Hub-Signature-256 (Meta): sha256=<hex>."""
    secret = (settings.whatsapp_app_secret or "").strip().encode()
    if not secret:
        return False
    hdr = (signature_header or "").strip()
    if not hdr.startswith("sha256="):
        return False
    theirs = hdr[7:].strip().lower()
    mac = hmac.new(secret, raw_body, hashlib.sha256).hexdigest().lower()
    return secrets.compare_digest(theirs, mac)

router = APIRouter(prefix="/whatsapp", tags=["WhatsApp Webhook"])

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


def _is_plain_greeting(text: str) -> bool:
    """
    Простое приветствие без запроса (детерминированный short-circuit до LLM).
    """
    raw = (text or "").strip().lower()
    if not raw:
        return False
    norm = re.sub(r"[^\w\s]+", " ", raw, flags=re.UNICODE)
    words = [w for w in re.split(r"\s+", norm) if w]
    if not words or len(words) > 4:
        return False
    greeting_words = {
        "привет",
        "здравствуйте",
        "здравствуй",
        "салам",
        "ассаламуалейкум",
        "hello",
        "hi",
        "добрый",
        "день",
        "вечер",
        "утро",
    }
    intent_words = {
        "меню",
        "заказ",
        "доставка",
        "самовывоз",
        "бронь",
        "бронирование",
        "адрес",
        "часы",
        "время",
        "order",
        "menu",
    }
    if any(w in intent_words for w in words):
        return False
    return all(w in greeting_words for w in words)


def _greeting_reply() -> str:
    return "Здравствуйте! Чем могу помочь?"


_POLITE_ACK_RE = re.compile(
    r"^(спасибо|благодарю|thanks|thank\s*you|мерси)\s*[!.\s]*$",
    re.IGNORECASE | re.UNICODE,
)


def _is_polite_ack_only(text: str) -> bool:
    """Короткое «спасибо» без запроса — безопасный fast-path до LLM (CHATTING)."""
    raw = (text or "").strip()
    if not raw or len(raw) > 48:
        return False
    return bool(_POLITE_ACK_RE.match(raw))


async def _save_chat_log(
    db: AsyncSession,
    phone: str,
    user_text: str,
    reply_text: str,
    assistant_meta: dict | None = None,
    *,
    organization_id: int,
    outbound_whatsapp: bool = True,
    trace_id: str | None = None,
    conversation_id: str | None = None,
) -> int | None:
    """
    Сохраняет пару сообщений (user + assistant) в ChatLog.
    Для исходящего ответа в WhatsApp — assistant со статусом sending; возвращает id строки assistant.
    """
    user = await get_or_create_user(db, phone, organization_id)
    db.add(
        ChatLog(
            organization_id=organization_id,
            user_id=user.id,
            role="user",
            content=user_text,
            meta_json=(
                trace_payload(
                    trace_id=trace_id,
                    conversation_id=conversation_id,
                )
                if trace_id and conversation_id else None
            ),
        ),
    )
    now = datetime.now(timezone.utc)
    assistant_kwargs: dict[str, Any] = {
        "organization_id": organization_id,
        "user_id": user.id,
        "role": "assistant",
        "content": reply_text,
        "meta_json": (
            merge_trace_meta(
                assistant_meta,
                trace_id=trace_id,
                conversation_id=conversation_id,
            )
            if trace_id and conversation_id else assistant_meta
        ),
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
                evt = await apply_whatsapp_status_webhook(db, mid, st, errs)
                await db.commit()
            if evt is not None:
                await publish_event("message_status_updated", evt)
        except Exception as exc:
            logger.warning("WhatsApp status update failed for %s: %s", mid[:20], exc)


async def _send_order_to_iiko(
    order_id: int,
    phone: str,
    items_json: dict[str, Any] | None,
    *,
    restaurant_organization_id: int,
) -> tuple[bool, str | None, dict[str, Any] | None]:
    """
    Попытка отправить подтверждённый заказ в iiko.

    Returns:
        (True, None, response_json) — успех; response_json — тело ответа iiko (correlationId, orderInfo…).
        (False, None, None) — iiko не настроен в .env (ошибку в БД не пишем).
        (False, msg, None) — настроен, но отправка не удалась (msg для админки).
    """
    from app.services.org_iiko import resolve_org_iiko_credentials

    try:
        async with async_session_factory() as _db:
            creds = await resolve_org_iiko_credentials(_db, int(restaurant_organization_id))
    except Exception:
        creds = None
    if creds is None:
        logger.info("iiko не настроен для org=%s — заказ сохранён только в БД", restaurant_organization_id)
        log_pipeline_stage(
            "iiko_skip", phone=phone, extra={"order_id": order_id, "reason": "not_configured"},
        )
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
        try:
            gc = int(meta.get("guest_count"))
            if gc > 0:
                comment_bits.append(f"гостей: {gc}")
        except (TypeError, ValueError):
            pass
        diet = str(meta.get("dietary_allergy_notes") or "").strip()
        if diet:
            comment_bits.append(f"ограничения: {diet[:400]}")
        comment = " · ".join(comment_bits)

        terminal_group = (creds.terminal_group_id or "").strip()
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
        # Некоторые аккаунты iiko валидируют обязательность customer.name.
        customer_name = f"Гость #{order_id}"
        async with IikoClient(api_login=creds.api_login) as client:
            iiko_response = await client.create_delivery_order(
                organization_id=creds.iiko_organization_id,
                order_data={
                    "customer": {
                        "phone": phone_e164,
                        "name": customer_name,
                    },
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


async def handle_confirmation(
    phone: str,
    message_text: str,
    organization_id: int,
    *,
    trace_id: str = "",
    conversation_id: str = "",
) -> str | None:
    """
    Обработка ответа на подтверждение заказа.
    При «Да» → подтверждаем. При «Нет» → отменяем.
    Иначе → None (клиент хочет изменить заказ — process_message перенаправит в LLM).
    """
    word = message_text.lower().strip().rstrip("!.,")
    order_id = await get_pending_order(redis_client, phone, organization_id=organization_id)

    if not order_id:
        await clear_pending_order(redis_client, phone, organization_id=organization_id)
        return "Заказ не найден — возможно, истекло время ожидания. Назовите блюда заново."

    if word in CONFIRM_WORDS:
        async with async_session_factory() as db:
            order_row = await db.get(Order, order_id)
            if order_row and order_row.items_json:
                om = (order_row.items_json.get("order_meta") or {})
                if om.get("requires_order_prepayment"):
                    org_ent = await db.get(Organization, organization_id)
                    prepay_enf = bool(org_ent.prepayment_enforced) if org_ent else True
                    if prepay_enf:
                        pst = (order_row.prepayment_status or "").strip().lower()
                        if pst not in ("paid", "waived"):
                            core = (
                                "По сумме заказа нужна **предоплата**. Пока платёж не отмечен оператором, "
                                "подтвердить заказ нельзя — как только оплатите, оператор отметит в системе, "
                                "и вы сможете ответить «Да» ещё раз."
                            )
                            return await append_prepayment_legal_disclaimer(db, organization_id, core)

            order = await confirm_order(
                db,
                order_id,
                trace_id=trace_id,
                conversation_id=conversation_id,
                source="webhooks.handle_confirmation",
            )
            await db.commit()

        if not order:
            await clear_pending_order(redis_client, phone, organization_id=organization_id)
            return "Заказ не найден. Попробуйте оформить заново."

        await publish_event("order_updated", {
            "order_id": order.id,
            "status": OrderStatus.CONFIRMED,
            "phone": phone,
            "total_price": float(order.total_price),
            "iiko_last_error": None,
            "organization_id": organization_id,
            "trace_id": trace_id or None,
            "conversation_id": conversation_id or None,
            **({"created_at": order.created_at.isoformat()} if getattr(order, "created_at", None) else {}),
        })

        await clear_pending_order(redis_client, phone, organization_id=organization_id)
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
            await cancel_order(
                db,
                order_id,
                trace_id=trace_id,
                conversation_id=conversation_id,
                source="webhooks.handle_confirmation",
            )
            await db.commit()

        await publish_event("order_updated", {
            "order_id": order_id,
            "status": OrderStatus.CANCELLED,
            "phone": phone,
            "organization_id": organization_id,
            "trace_id": trace_id or None,
            "conversation_id": conversation_id or None,
        })

        await clear_pending_order(redis_client, phone, organization_id=organization_id)
        return (
            "Заказ отменён. Вы можете:\n"
            "  • Назвать новые блюда — я оформлю новый заказ\n"
            "  • Написать что изменить — например «уберите лагман, добавьте плов»\n"
            "  • Или просто продолжить общение 😊"
        )

    return None  # не «Да» и не «Нет» — вернём None, process_message пропустит через LLM


async def handle_booking_confirmation(
    phone: str,
    message_text: str,
    organization_id: int,
    *,
    trace_id: str = "",
    conversation_id: str = "",
) -> str:
    """
    Обработка подтверждения бронирования.
    При «Да» → подтверждаем. При «Нет» → отменяем.
    """
    word = message_text.lower().strip().rstrip("!.,")
    booking_id = await get_pending_booking(redis_client, phone, organization_id=organization_id)

    if not booking_id:
        await clear_pending_booking(redis_client, phone, organization_id=organization_id)
        return "Бронирование не найдено. Назовите дату и время заново."

    if word in CONFIRM_WORDS:
        async with async_session_factory() as db:
            booking, err = await confirm_booking(
                db,
                booking_id,
                trace_id=trace_id,
                conversation_id=conversation_id,
                source="webhooks.handle_booking_confirmation",
            )
            await db.commit()

        if err == "vip_conflict":
            await clear_pending_booking(redis_client, phone, organization_id=organization_id)
            return (
                "К сожалению, VIP-зал на это время только что заняли.\n"
                "Напишите другую дату/время или выберите Зал 1 / Зал 2 — оформим бронь заново."
            )

        if not booking:
            await clear_pending_booking(redis_client, phone, organization_id=organization_id)
            return "Бронирование не найдено. Попробуйте заново."

        await clear_pending_booking(redis_client, phone, organization_id=organization_id)
        return (
            f"Отлично! Бронь #{booking.id} подтверждена! 🎉\n"
            f"Ждём вас {booking.booking_date.strftime('%d.%m.%Y')} "
            f"в {booking.booking_time.strftime('%H:%M')} "
            f"на {booking.guests} гостей."
        )

    if word in CANCEL_WORDS:
        async with async_session_factory() as db:
            await cancel_booking(
                db,
                booking_id,
                trace_id=trace_id,
                conversation_id=conversation_id,
                source="webhooks.handle_booking_confirmation",
            )
            await db.commit()

        await clear_pending_booking(redis_client, phone, organization_id=organization_id)
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


async def handle_order_payment_choice(
    phone: str,
    message_text: str,
    organization_id: int,
    *,
    trace_id: str = "",
    conversation_id: str = "",
) -> str | None:
    """
    После черновика заказа: фиксируем способ оплаты в items_json, затем переход к Да/Нет.
    None → текст не распознан как оплата/отмена — process_message перенаправит в LLM.
    """
    order_id = await get_pending_order(redis_client, phone, organization_id=organization_id)
    if not order_id:
        await clear_pending_order(redis_client, phone, organization_id=organization_id)
        return "Заказ не найден — возможно, истекло время. Назовите блюда заново."

    word = message_text.lower().strip().rstrip("!.,")

    if word in CANCEL_WORDS:
        async with async_session_factory() as db:
            await cancel_order(
                db,
                order_id,
                trace_id=trace_id,
                conversation_id=conversation_id,
                source="webhooks.handle_order_payment_choice",
            )
            await db.commit()
        await publish_event("order_updated", {
            "order_id": order_id,
            "status": OrderStatus.CANCELLED,
            "phone": phone,
            "organization_id": organization_id,
            "trace_id": trace_id or None,
            "conversation_id": conversation_id or None,
        })
        await clear_pending_order(redis_client, phone, organization_id=organization_id)
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
            await clear_pending_order(redis_client, phone, organization_id=organization_id)
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
        org_ent = await db.get(Organization, organization_id)
        if org_ent and not org_ent.prepayment_enforced:
            needs_prepay = False
        prep_st = (order.prepayment_status or "").strip().lower()

        await publish_event("order_updated", {
            "order_id": order.id,
            "status": OrderStatus.DRAFT,
            "phone": phone,
            "total_price": total,
            "payment_method": pm,
            "organization_id": organization_id,
            **({"created_at": order.created_at.isoformat()} if getattr(order, "created_at", None) else {}),
        })

    if needs_prepay and prep_st not in ("paid", "waived"):
        try:
            from app.integrations.telegram import send_prepayment_large_order_alert

            await send_prepayment_large_order_alert(
                organization_id=int(organization_id),
                order_id=int(order.id),
                phone=phone,
                total=float(order.total_price or 0),
                threshold=float(settings.order_prepayment_threshold_kzt),
            )
        except Exception as exc:
            logger.warning("Telegram prepayment alert skipped: %s", exc)
        await sync_user_dialog_state_to_db_then_redis(
            redis_client,
            phone=phone,
            organization_id=organization_id,
            new_state=UserState.CHATTING,
        )
        prepay_reply = (
            f"Принял способ оплаты: {pay_human}.\n\n"
            f"{body}\n\n"
            f"💳 Сумма от **{int(settings.order_prepayment_threshold_kzt):,}** ₸ — нужна предоплата. "
            "Оператор пришлёт реквизиты или ссылку. После оплаты вы сможете подтвердить заказ ответом «Да»."
        )
        async with async_session_factory() as db_legal:
            prepay_reply = await append_prepayment_legal_disclaimer(
                db_legal, organization_id, prepay_reply,
            )
        return prepay_reply

    await sync_user_dialog_state_to_db_then_redis(
        redis_client,
        phone=phone,
        organization_id=organization_id,
        new_state=UserState.CONFIRMING_ORDER,
    )
    return reply


MAX_RETRIES = 3


async def _save_failed_task(
    phone: str,
    text: str,
    error: str,
    attempts: int,
    *,
    organization_id: int | None = None,
) -> None:
    """Best-effort: сохранить необработанное сообщение для диагностики."""
    try:
        async with async_session_factory() as db:
            db.add(
                FailedTask(
                    organization_id=organization_id,
                    phone=phone,
                    message_text=text[:4000],
                    error=error[:2000],
                    attempts=attempts,
                ),
            )
            await db.commit()
    except Exception as exc:
        logger.error("Не удалось сохранить FailedTask для %s: %s", phone, exc)


async def process_with_retry(
    phone: str,
    message_text: str = "",
    *,
    whatsapp_message_id: str = "",
    voice_audio: tuple[bytes, str] | None = None,
    webhook_value: dict[str, Any] | None = None,
    organization_id: int | None = None,
) -> None:
    """
    Обёртка с retry + exponential backoff.
    После MAX_RETRIES неудач → сохраняет в FailedTask и извиняется перед клиентом.
    """
    org_id = int(organization_id) if organization_id is not None else int(settings.default_organization_id)
    org_active = True
    pipeline_sw = PipelineStopwatch()
    if webhook_value is not None and organization_id is None:
        try:
            async with async_session_factory() as db:
                org_id = await organization_id_for_whatsapp_value(db, webhook_value)
                org_ent = await db.get(Organization, int(org_id))
                org_active = bool(org_ent.is_active) if org_ent is not None else True
        except Exception as exc:
            logger.warning("organization_id resolve: %s", exc)
    elif organization_id is not None:
        try:
            async with async_session_factory() as db:
                org_ent = await db.get(Organization, int(org_id))
                org_active = bool(org_ent.is_active) if org_ent is not None else True
        except Exception as exc:
            logger.warning("organization activity check failed (org=%s): %s", org_id, exc)
    if not org_active:
        logger.info("org=%s inactive: webhook message ignored", org_id)
        return
    wmid = (whatsapp_message_id or "").strip()
    if wmid:
        try:
            async with async_session_factory() as db:
                can = await try_start_whatsapp_inbound_in_db(db, message_id=wmid, phone=phone)
                await db.commit()
            if not can:
                return
        except Exception as exc:
            # Если дедуп-таблица недоступна, лучше не терять сообщение: продолжаем без БД-идемпотентности.
            logger.warning("WhatsApp dedupe start failed (mid=%s): %s", wmid[:24], exc)

    if settings.pipeline_timing_enabled:
        pipeline_sw.split("dedupe")

    last_exc: BaseException | None = None
    for attempt in range(MAX_RETRIES):
        try:
            await process_message(
                phone,
                message_text,
                whatsapp_message_id=wmid,
                voice_audio=voice_audio,
                organization_id=org_id,
                pipeline_sw=pipeline_sw,
            )
            if wmid:
                try:
                    async with async_session_factory() as db:
                        await mark_whatsapp_inbound_done(db, wmid)
                        await db.commit()
                except Exception as exc:
                    logger.warning("WhatsApp dedupe mark done failed (mid=%s): %s", wmid[:24], exc)
                else:
                    await cache_whatsapp_inbound_done_redis(wmid)
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
    await _save_failed_task(
        phone, message_text, str(last_exc), MAX_RETRIES, organization_id=org_id,
    )
    if wmid:
        try:
            async with async_session_factory() as db:
                await mark_whatsapp_inbound_failed(db, wmid, str(last_exc or "unknown_error"))
                await db.commit()
        except Exception as exc:
            logger.warning("WhatsApp dedupe mark failed error (mid=%s): %s", wmid[:24], exc)
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
    organization_id: int,
    pipeline_sw: PipelineStopwatch | None = None,
) -> None:
    """
    Полный цикл обработки входящего сообщения с учётом State Machine.
    """
    try:
        pipe_sw = pipeline_sw or PipelineStopwatch()
        conversation_id = build_conversation_id(organization_id, phone)
        trace_id = build_trace_id(whatsapp_message_id)
        state = await get_user_state(redis_client, phone, organization_id=organization_id)
        if message_text and _is_plain_greeting(message_text):
            if state == UserState.CHATTING and voice_audio is None:
                quick_reply = _greeting_reply()
                outbound_id_quick: int | None = None
                async with async_session_factory() as db_quick:
                    outbound_id_quick = await _save_chat_log(
                        db_quick,
                        phone,
                        message_text,
                        quick_reply,
                        organization_id=organization_id,
                        trace_id=trace_id,
                        conversation_id=conversation_id,
                    )
                    await db_quick.commit()
                await append_to_history(redis_client, phone, "user", message_text, organization_id=organization_id)
                await append_to_history(redis_client, phone, "assistant", quick_reply, organization_id=organization_id)
                await publish_event("new_message", {
                    "phone": phone,
                    "role": "assistant",
                    "content": quick_reply,
                    "id": outbound_id_quick,
                    "delivery_status": "sending",
                    "organization_id": organization_id,
                    "trace_id": trace_id,
                    "conversation_id": conversation_id,
                })
                await send_customer_text(phone, quick_reply, outbound_chat_log_id=outbound_id_quick)
                return

        wmid = (whatsapp_message_id or "").strip()
        if not await check_rate_limit(phone):
            logger.warning("Rate limit: %s заблокирован", phone)
            await send_customer_text(phone, "Слишком много сообщений. Подождите минуту и попробуйте снова.")
            return

        voice_bytes: bytes | None = None
        voice_mime = ""
        if voice_audio:
            voice_bytes, voice_mime = voice_audio

        async with async_session_factory() as db_u:
            u_row = await db_u.scalar(
                select(User).where(
                    User.phone == phone,
                    User.organization_id == organization_id,
                ),
            )
            ai_paused_db = bool(u_row and getattr(u_row, "ai_paused", False))
            ai_snooze_active = False
            if u_row is not None:
                from app.services.ai_snooze import ai_snooze_is_active, clear_ai_snooze_if_expired

                await clear_ai_snooze_if_expired(db_u, u_row)
                ai_snooze_active = ai_snooze_is_active(u_row)
                await db_u.commit()

        # Голос без мультимодального чата: сначала текст (подтверждения, оператор)
        if voice_bytes is not None and (
            state == UserState.AWAITING_ORDER_PAYMENT
            or state == UserState.CONFIRMING_ORDER
            or state == UserState.CONFIRMING_BOOKING
            or state == UserState.HUMAN_MODE
            or ai_paused_db
            or ai_snooze_active
        ):
            if not voice_supported():
                await send_customer_text(
                    phone,
                    "Голосовые недоступны: задайте OPENAI_API_KEY или GEMINI_API_KEY в настройках сервера.",
                )
                return
            message_text = await transcribe_voice(voice_bytes, voice_mime)
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
            "phone": phone,
            "role": "user",
            "content": user_evt,
            "organization_id": organization_id,
            "trace_id": trace_id,
            "conversation_id": conversation_id,
        })

        # ─── HUMAN_MODE / ai_paused / ai_snooze: AI молчит, только логируем ─────────
        # Состояние в Redis: ключ user:state:{phone} (см. dialog_mgr.get_user_state), не вызываем LLM.
        # Временная пауза: User.ai_snoozed_until (UTC) — без перевода Redis в HUMAN_MODE; см. app.services.ai_snooze.
        if state == UserState.HUMAN_MODE or ai_paused_db or ai_snooze_active:
            async with async_session_factory() as db:
                await _save_chat_log(
                    db,
                    phone,
                    message_text,
                    "[OPERATOR_ONLY — AI не отвечает]",
                    organization_id=organization_id,
                    outbound_whatsapp=False,
                    trace_id=trace_id,
                    conversation_id=conversation_id,
                )
                await db.commit()
            await append_to_history(
                redis_client, phone, "user", message_text, organization_id=organization_id,
            )
            logger.info(
                "operator_only: сообщение от %s сохранено, AI не вызван (human=%s paused=%s snooze=%s)",
                phone,
                state == UserState.HUMAN_MODE,
                ai_paused_db,
                ai_snooze_active,
            )
            return

        # ─── AWAITING_ORDER_PAYMENT: способ оплаты, правка заказа или Да/Нет ───
        if state == UserState.AWAITING_ORDER_PAYMENT:
            final_reply = await handle_order_payment_choice(
                phone,
                message_text,
                organization_id,
                trace_id=trace_id,
                conversation_id=conversation_id,
            )

            if final_reply is not None:
                outbound_id: int | None = None
                async with async_session_factory() as db:
                    outbound_id = await _save_chat_log(
                        db, phone, message_text, final_reply, organization_id=organization_id,
                        trace_id=trace_id, conversation_id=conversation_id,
                    )
                    await db.commit()

                await append_to_history(
                    redis_client, phone, "user", message_text, organization_id=organization_id,
                )
                await append_to_history(
                    redis_client, phone, "assistant", final_reply, organization_id=organization_id,
                )
                await publish_event("new_message", {
                    "phone": phone,
                    "role": "assistant",
                    "content": final_reply,
                    "id": outbound_id,
                    "delivery_status": "sending",
                    "organization_id": organization_id,
                    "trace_id": trace_id,
                    "conversation_id": conversation_id,
                })
                await send_customer_text(
                    phone, final_reply, outbound_chat_log_id=outbound_id,
                )
                return
            # None → клиент хочет изменить заказ; переключаем в CHATTING и идём в OpenAI
            await sync_user_dialog_state_to_db_then_redis(
                redis_client,
                phone=phone,
                organization_id=organization_id,
                new_state=UserState.CHATTING,
                source="webhooks.process_message",
                reason="payment_choice_unrecognized_reenter_llm",
                context=trace_payload(trace_id=trace_id, conversation_id=conversation_id),
            )
            state = UserState.CHATTING

        # ─── CONFIRMING_ORDER: ждём Да/Нет или правку заказа ──────
        if state == UserState.CONFIRMING_ORDER:
            final_reply = await handle_confirmation(
                phone,
                message_text,
                organization_id,
                trace_id=trace_id,
                conversation_id=conversation_id,
            )

            if final_reply is not None:
                outbound_id_o: int | None = None
                async with async_session_factory() as db:
                    outbound_id_o = await _save_chat_log(
                        db, phone, message_text, final_reply, organization_id=organization_id,
                        trace_id=trace_id, conversation_id=conversation_id,
                    )
                    await db.commit()

                await append_to_history(
                    redis_client, phone, "user", message_text, organization_id=organization_id,
                )
                await append_to_history(
                    redis_client, phone, "assistant", final_reply, organization_id=organization_id,
                )
                await publish_event("new_message", {
                    "phone": phone,
                    "role": "assistant",
                    "content": final_reply,
                    "id": outbound_id_o,
                    "delivery_status": "sending",
                    "organization_id": organization_id,
                    "trace_id": trace_id,
                    "conversation_id": conversation_id,
                })
                await send_customer_text(
                    phone, final_reply, outbound_chat_log_id=outbound_id_o,
                )
                return
            # None → клиент хочет изменить заказ; переключаем в CHATTING и идём в OpenAI
            await sync_user_dialog_state_to_db_then_redis(
                redis_client,
                phone=phone,
                organization_id=organization_id,
                new_state=UserState.CHATTING,
                source="webhooks.process_message",
                reason="order_confirmation_unrecognized_reenter_llm",
                context=trace_payload(trace_id=trace_id, conversation_id=conversation_id),
            )
            state = UserState.CHATTING

        # ─── CONFIRMING_BOOKING: ждём Да/Нет ─────────────────
        if state == UserState.CONFIRMING_BOOKING:
            final_reply = await handle_booking_confirmation(
                phone,
                message_text,
                organization_id,
                trace_id=trace_id,
                conversation_id=conversation_id,
            )

            outbound_id_b: int | None = None
            async with async_session_factory() as db:
                outbound_id_b = await _save_chat_log(
                    db, phone, message_text, final_reply, organization_id=organization_id,
                    trace_id=trace_id, conversation_id=conversation_id,
                )
                await db.commit()

            await append_to_history(
                redis_client, phone, "user", message_text, organization_id=organization_id,
            )
            await append_to_history(
                redis_client, phone, "assistant", final_reply, organization_id=organization_id,
            )
            await publish_event("new_message", {
                "phone": phone,
                "role": "assistant",
                "content": final_reply,
                "id": outbound_id_b,
                "delivery_status": "sending",
                "organization_id": organization_id,
                "trace_id": trace_id,
                "conversation_id": conversation_id,
            })
            await send_customer_text(
                phone, final_reply, outbound_chat_log_id=outbound_id_b,
            )
            return

        # ─── CHATTING: обычный AI-флоу ──────────────────────
        had_voice = voice_bytes is not None
        history = await get_chat_history(redis_client, phone, organization_id=organization_id)

        if had_voice:
            if not voice_supported():
                await send_customer_text(
                    phone,
                    "Голосовые недоступны: задайте OPENAI_API_KEY или GEMINI_API_KEY в настройках сервера.",
                )
                return
        else:
            await append_to_history(
                redis_client, phone, "user", message_text, organization_id=organization_id,
            )

        if (
            settings.whatsapp_fast_ack_enabled
            and message_text.strip()
            and not had_voice
            and _is_polite_ack_only(message_text)
        ):
            ack_reply = (
                "Рады были помочь! Если захотите заказать или что-то уточнить по меню — напишите."
            )
            outbound_ack: int | None = None
            async with async_session_factory() as db_ack:
                outbound_ack = await _save_chat_log(
                    db_ack,
                    phone,
                    message_text,
                    ack_reply,
                    organization_id=organization_id,
                    trace_id=trace_id,
                    conversation_id=conversation_id,
                )
                await db_ack.commit()
            await append_to_history(
                redis_client, phone, "assistant", ack_reply, organization_id=organization_id,
            )
            await publish_event("new_message", {
                "phone": phone,
                "role": "assistant",
                "content": ack_reply,
                "id": outbound_ack,
                "delivery_status": "sending",
                "organization_id": organization_id,
                "trace_id": trace_id,
                "conversation_id": conversation_id,
            })
            await send_customer_text(phone, ack_reply, outbound_chat_log_id=outbound_ack)
            if settings.pipeline_timing_enabled:
                pipe_sw.split("preflight")
                pipe_sw.split("short_circuit_ack")
                log_pipeline_rm_stage_ms(
                    phone_tail=_redact_msisdn_for_log(phone),
                    rm_stage_ms=pipe_sw.rm_stage_ms,
                    extra={"organization_id": organization_id, "path": "fast_ack"},
                )
            return

        if settings.pipeline_timing_enabled:
            pipe_sw.split("preflight")

        outbound_id_chat: int | None = None
        tg_alert_ctx: EscalationAlertExtras | None = None
        # 1) DB: параллельное чтение (несколько коротких сессий), затем сбор строк без I/O
        read_ctx = await fetch_ai_read_context(phone, organization_id)
        menu_items = read_ctx.menu_items
        menu_context = await build_menu_context_for_ai(menu_items, message_text)
        u_row = read_ctx.user
        customer_ctx = read_ctx.customer_ctx
        org_ent = read_ctx.org
        current_time_ctx = cached_format_org_current_time_block(
            organization_id,
            getattr(org_ent, "timezone", None) if org_ent is not None else "Etc/GMT-5",
            getattr(org_ent, "schedule_json", None) if org_ent is not None else None,
        )
        kb_context = read_ctx.kb_context
        draft_row = read_ctx.draft_row
        draft_ctx = format_draft_order_context_for_prompt(
            draft_row.items_json if draft_row else None,
        )
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
        else:
            strategy_ctx = ""
            sales_gastro_hint = ""
            sales_target_iiko_ids = []

        if settings.pipeline_timing_enabled:
            pipe_sw.split("context")

        # 2) OpenAI: без DB-сессии
        if had_voice:
            if voice_bytes is None:
                return
            ai_response = await call_ai_with_audio(
                history,
                voice_bytes,
                voice_mime,
                menu_context,
                kb_context,
                draft_order_context=draft_ctx,
                sales_strategy_context=strategy_ctx,
                customer_context=customer_ctx,
                current_time_context=current_time_ctx,
            )
            user_log_text = (ai_response.recognized_speech or "").strip() or "🎤 голосовое сообщение"
            await append_to_history(
                redis_client, phone, "user", user_log_text, organization_id=organization_id,
            )
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
                current_time_context=current_time_ctx,
                # Временный сбой провайдера → тот же путь, что и «позвать человека»:
                # escalate + HUMAN_MODE + Telegram + human_needed (без 3× retry на исключении).
                raise_on_transient=False,
            )

        if settings.pipeline_timing_enabled:
            pipe_sw.split("llm")

        log_pipeline_stage(
            "llm_ok",
            phone=phone,
            extra={"intent": ai_response.intent, "voice": had_voice},
        )
        schedule_log_ai_usage(organization_id, getattr(ai_response, "_usage", None))
        # P4: регистрируем транзиентную AI-ошибку (fallback = провайдер недоступен)
        if is_openai_fallback_escalation_reply(ai_response.reply_text):
            schedule_log_ai_error(organization_id)

        # 3) DB: короткая мутация/запись результатов
        post_commit_state: UserState | None = None
        post_commit_pending_order: int | None = None
        post_commit_pending_booking: int | None = None
        async with async_session_factory() as db:
            result = await route_intent(
                db,
                phone,
                ai_response,
                menu_items=menu_items,
                organization_id=organization_id,
                inbound_message_id=wmid,
                sales_gastro_hint=sales_gastro_hint,
                sales_target_iiko_ids=sales_target_iiko_ids,
                trace_id=trace_id,
                conversation_id=conversation_id,
            )
            log_pipeline_stage(
                "route_ok",
                phone=phone,
                extra={
                    "intent": ai_response.intent,
                    "pending_order_id": result.pending_order_id,
                },
            )

            # Durable state (source of truth) — в этой же транзакции БД.
            db_state_kwargs: dict[str, Any] = {
                "phone": phone,
                "organization_id": organization_id,
                "current_state": (result.new_state.value if result.new_state else None),
                "transition_source": "webhooks.process_message",
                "transition_reason": f"intent:{ai_response.intent}",
                "transition_context": trace_payload(trace_id=trace_id, conversation_id=conversation_id),
            }
            if result.pending_order_id is not None:
                db_state_kwargs["current_pending_order_id"] = (
                    int(result.pending_order_id) if result.pending_order_id else None
                )
            if result.pending_booking_id is not None:
                db_state_kwargs["current_pending_booking_id"] = (
                    int(result.pending_booking_id) if result.pending_booking_id else None
                )
            await update_user_session_fields_in_db(db, **db_state_kwargs)

            post_commit_state = result.new_state
            post_commit_pending_order = result.pending_order_id
            post_commit_pending_booking = result.pending_booking_id

            assistant_meta = {
                "intent": ai_response.intent,
                "monologue": (
                    f"Распознан интент: {ai_response.intent}. "
                    "Ответ сформирован моделью (OpenAI)."
                ),
            }
            outbound_id_chat = await _save_chat_log(
                db,
                phone,
                user_log_text,
                result.reply_text,
                assistant_meta,
                organization_id=organization_id,
                trace_id=trace_id,
                conversation_id=conversation_id,
            )
            if result.new_state == UserState.HUMAN_MODE:
                db.add(
                    EscalationEvent(
                        organization_id=organization_id,
                        phone=phone,
                        user_message=(user_log_text or "")[:2000],
                        reason=(result.reply_text or "")[:2000],
                    ),
                )
                draft_total_str: str | None = None
                if draft_row is not None and draft_row.total_price is not None:
                    try:
                        n = float(draft_row.total_price)
                        draft_total_str = f"{n:,.0f}".replace(",", " ") + " \u20b8"
                    except (TypeError, ValueError):
                        draft_total_str = str(draft_row.total_price)
                p_ord = await get_pending_order(
                    redis_client, phone, organization_id=organization_id,
                )
                p_book = await get_pending_booking(
                    redis_client, phone, organization_id=organization_id,
                )
                tg_alert_ctx = EscalationAlertExtras(
                    intent=ai_response.intent,
                    fsm_state=state.value,
                    had_voice=had_voice,
                    detected_language=ai_response.detected_language,
                    draft_order_id=draft_row.id if draft_row else None,
                    draft_order_total=draft_total_str,
                    customer_name=(u_row.name or "").strip() or None if u_row else None,
                    technical_fallback=is_openai_fallback_escalation_reply(result.reply_text),
                    outbound_chat_log_id=outbound_id_chat,
                    pending_order_id=p_ord,
                    pending_booking_id=p_book,
                )
            await db.commit()

        if settings.pipeline_timing_enabled:
            pipe_sw.split("route")

        # Cache update после commit — иначе риск рассинхрона Redis↔БД при падении процесса
        if post_commit_state:
            await set_user_state(redis_client, phone, post_commit_state, organization_id=organization_id)
        if post_commit_pending_order:
            await set_pending_order(redis_client, phone, post_commit_pending_order, organization_id=organization_id)
        if post_commit_pending_booking:
            await set_pending_booking(redis_client, phone, post_commit_pending_booking, organization_id=organization_id)

        for evt_type, evt_data in (result.events or []):
            evt_data.setdefault("trace_id", trace_id)
            evt_data.setdefault("conversation_id", conversation_id)
            await publish_event(evt_type, evt_data)

        await append_to_history(
            redis_client, phone, "assistant", result.reply_text, organization_id=organization_id,
        )
        from app.services.trace_context import publish_chat_event
        await publish_chat_event(
            phone=phone,
            role="assistant",
            content=result.reply_text,
            organization_id=organization_id,
            chat_log_id=outbound_id_chat,
            trace_id=trace_id,
            conversation_id=conversation_id,
            intent=ai_response.intent,
        )
        # E8: интерактивное сообщение (кнопки или CTA) вместо plain text если задано
        _sent_interactive = False
        if result.interactive_buttons:
            try:
                from app.integrations.whatsapp import send_interactive_buttons
                ir = await send_interactive_buttons(phone, result.reply_text, result.interactive_buttons)
                _sent_interactive = ir.ok
            except Exception as _ie:
                logger.warning("send_interactive_buttons failed, fallback to text: %s", _ie)
        elif result.cta_url:
            try:
                from app.integrations.whatsapp import send_cta_url_button
                ir = await send_cta_url_button(
                    phone, result.reply_text, "💳 Оплатить", result.cta_url,
                )
                _sent_interactive = ir.ok
            except Exception as _ce:
                logger.warning("send_cta_url_button failed, fallback to text: %s", _ce)
        if not _sent_interactive:
            await send_customer_text(
                phone, result.reply_text, outbound_chat_log_id=outbound_id_chat,
            )

        if settings.pipeline_timing_enabled:
            pipe_sw.split("reply")
            log_pipeline_rm_stage_ms(
                phone_tail=_redact_msisdn_for_log(phone),
                rm_stage_ms=pipe_sw.rm_stage_ms,
                extra={"organization_id": organization_id, "intent": ai_response.intent},
            )
            # P4: fire-and-forget latency recording для SLA мониторинга
            schedule_log_pipeline_latency(
                organization_id,
                pipe_sw.rm_stage_ms,
                pipeline_type="whatsapp_voice" if had_voice else "whatsapp_text",
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
            from app.services.trace_context import publish_human_event
            await publish_human_event(
                phone=phone,
                organization_id=organization_id,
                reason=ai_response.reply_text,
                user_message=user_log_text or "",
                trace_id=trace_id,
                conversation_id=conversation_id,
                intent=ai_response.intent,
            )
            try:
                await send_tg_fallback_alert(
                    phone,
                    user_log_text,
                    result.reply_text,
                    extras=tg_alert_ctx,
                    organization_id=organization_id,
                )
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
    webhook_value: dict[str, Any] | None = None,
) -> None:
    """
    Голосовое WhatsApp: скачать → STT (Whisper или Gemini, по AI_PROVIDER) + чат текущего AI в CHATTING
    или только STT в подтверждениях.
    """
    try:
        if not voice_supported():
            logger.info("Голосовое от %s: ни OPENAI_API_KEY, ни GEMINI_API_KEY не заданы", phone)
            await send_customer_text(
                phone,
                "Голосовые сообщения пока не настроены. Задайте OPENAI_API_KEY или GEMINI_API_KEY на сервере (и AI_PROVIDER), или напишите текстом.",
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
            webhook_value=webhook_value,
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
    if not voice_supported():
        logger.warning("Twilio voice: AI-провайдер не настроен для голоса (нет ключа OPENAI/GEMINI)")
        return
    wav = mulaw_8k_to_wav(mulaw)
    text = await transcribe_voice(wav, "audio/wav")
    text = (text or "").strip()
    if not text:
        return
    mid = f"twilio:{call_sid}:{uuid.uuid4().hex}"
    tok = twilio_call_context(call_sid)
    try:
        await process_message(
            phone,
            text,
            whatsapp_message_id=mid,
            organization_id=int(settings.default_organization_id),
        )
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
    raw_body = await request.body()
    wa_secret_cfg = bool((settings.whatsapp_app_secret or "").strip())
    if settings.is_prod_like and not wa_secret_cfg:
        logger.error(
            "WhatsApp webhook: в prod-like окружении (APP_ENV=production|staging) должен быть задан "
            "WHATSAPP_APP_SECRET (подпись X-Hub-Signature-256)",
        )
        return Response(content="Service Unavailable", status_code=503)
    if wa_secret_cfg:
        sig = request.headers.get("x-hub-signature-256") or request.headers.get("X-Hub-Signature-256")
        if not _verify_whatsapp_hub_signature256(raw_body, sig):
            logger.warning("WhatsApp webhook: неверная или отсутствующая подпись X-Hub-Signature-256")
            return Response(content="Forbidden", status_code=403)

    try:
        body = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        logger.warning("WhatsApp webhook: тело не JSON")
        return Response(content="Bad Request", status_code=400)

    logger.debug(
        "Входящий вебхук WhatsApp: entries=%s",
        len(body.get("entry", [])) if isinstance(body, dict) else 0,
    )

    try:
        entry = body.get("entry", [{}])[0]
        changes = entry.get("changes", [{}])[0]
        value = changes.get("value", {})
        try:
            async with async_session_factory() as db:
                org_id = await organization_id_for_whatsapp_value(db, value)
                org_ent = await db.get(Organization, int(org_id))
                if org_ent is not None and not bool(org_ent.is_active):
                    logger.info("org=%s inactive: incoming webhook ignored", org_id)
                    return {"status": "ok"}
                if org_ent is not None and await tenant_billing_blocks_inbound(db, org_ent):
                    logger.info("org=%s tenant billing suspended: incoming webhook ignored", org_id)
                    return {"status": "ok"}
        except Exception as exc:
            logger.warning("organization activity check failed: %s", exc)
        statuses = value.get("statuses", []) or []
        if statuses:
            from app.services.task_queue import dispatch_arq_or_background

            await dispatch_arq_or_background(
                "whatsapp_process_statuses",
                background_tasks,
                statuses=list(statuses),
            )

        messages = value.get("messages", []) or []

        for msg in messages:
            if not isinstance(msg, dict):
                continue
            phone = (msg.get("from") or "").strip()
            msg_type = (msg.get("type") or "").strip().lower()
            message_id = (msg.get("id") or "").strip()

            if not phone:
                continue

            if message_id and await redis_whatsapp_inbound_done_cache_hit(message_id):
                logger.info(
                    "Дубликат WhatsApp message_id=%s от %s (redis после done) — пропущен",
                    message_id,
                    _redact_msisdn_for_log(phone),
                )
                continue

            if msg_type == "audio":
                media_id = (msg.get("audio") or {}).get("id") or ""
                if media_id:
                    from app.services.task_queue import dispatch_arq_or_background

                    await dispatch_arq_or_background(
                        "whatsapp_process_voice",
                        background_tasks,
                        phone=phone,
                        media_id=media_id,
                        whatsapp_message_id=message_id,
                        webhook_value=value,
                    )
                    logger.info("Голосовое от %s поставлено в очередь", phone)
            elif msg_type == "interactive":
                # E8: кнопки quick-reply — клиент нажал одну из кнопок
                interactive = msg.get("interactive") or {}
                interactive_type = (interactive.get("type") or "").strip()
                message_text = ""
                if interactive_type == "button_reply":
                    br = interactive.get("button_reply") or {}
                    btn_id = (br.get("id") or "").strip()
                    btn_title = (br.get("title") or "").strip()
                    # Отзывы: обрабатываем до LLM
                    if btn_id in ("review_pos", "review_neg"):
                        _org_id = value.get("metadata", {}).get("organization_id") or settings.default_organization_id
                        from app.services.review_requests import (
                            save_customer_feedback,
                            send_review_positive_reply,
                            send_review_negative_alert,
                        )
                        _rating = "positive" if btn_id == "review_pos" else "negative"
                        asyncio.create_task(save_customer_feedback(
                            org_id=_org_id, phone=phone, rating=_rating,
                        ))
                        if btn_id == "review_pos":
                            asyncio.create_task(send_review_positive_reply(phone, _org_id))
                        else:
                            asyncio.create_task(send_review_negative_alert(phone, _org_id))
                        continue  # не передаём в LLM
                    # Маппинг стандартных ID на слова, которые понимает CONFIRM_WORDS / CANCEL_WORDS
                    if btn_id == "confirm":
                        message_text = "да"
                    elif btn_id == "cancel":
                        message_text = "нет"
                    else:
                        message_text = btn_title or btn_id
                elif interactive_type == "list_reply":
                    lr = interactive.get("list_reply") or {}
                    message_text = (lr.get("title") or lr.get("id") or "").strip()
                if message_text:
                    from app.services.task_queue import dispatch_arq_or_background

                    await dispatch_arq_or_background(
                        "whatsapp_process_text",
                        background_tasks,
                        phone=phone,
                        message_text=message_text,
                        whatsapp_message_id=message_id,
                        webhook_value=value,
                    )
                    logger.info(
                        "Interactive (%s) от %s → '%s' поставлено в очередь",
                        interactive_type, phone, message_text,
                    )
            else:
                message_text = (msg.get("text") or {}).get("body") or ""
                if message_text:
                    from app.services.task_queue import dispatch_arq_or_background

                    await dispatch_arq_or_background(
                        "whatsapp_process_text",
                        background_tasks,
                        phone=phone,
                        message_text=message_text,
                        whatsapp_message_id=message_id,
                        webhook_value=value,
                    )
                    logger.info("Сообщение от %s поставлено в очередь обработки", phone)

    except (IndexError, KeyError, TypeError) as exc:
        logger.error("Ошибка парсинга вебхука: %s", exc)

    return {"status": "ok"}
