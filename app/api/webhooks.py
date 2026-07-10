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
from datetime import datetime, timedelta, timezone
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
from app.services.context_engine import (
    build_llm_prompt_bundle,
    fetch_ai_read_context,
    schedule_save_ai_context_snapshot,
)
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
    apply_user_state_transition_in_db,
    clear_pending_booking,
    clear_pending_order,
    clear_pending_order_durable,
    clear_human_mode_ttl_meta,
    get_chat_history,
    get_pending_booking,
    get_pending_order,
    get_user_state,
    is_cancel_all_message,
    parse_human_mode_until,
    set_pending_booking,
    set_pending_order,
    set_user_state,
    sync_user_dialog_state_to_db_then_redis,
    update_user_session_fields_in_db,
)
from app.services.conversation_state import ConversationState, normalize_conversation_state
from app.services.ai_usage import schedule_log_ai_error, schedule_log_ai_usage
from app.services.async_tasks import spawn_tracked
from app.services.message_accounting import schedule_log_message
from app.services.pipeline_latency import schedule_log_pipeline_latency
from app.services.events import publish_event
from app.services.billing_guard import tenant_billing_blocks_inbound
from app.services.system_events import BusinessEvent, emit_event
from app.services.decision_engine import decision_engine, emit_kitchen_gate_order_blocked_events
from app.services.faq_cache import (
    get_cached_faq_reply,
    kb_fingerprint_from_text,
    save_faq_reply,
    should_save_faq_reply,
)
from app.services.org_resolve import organization_id_for_whatsapp_value
from app.services.prompt_metrics import apply_prompt_size_controls
from app.services.quick_replies import (
    QuickReplyHit,
    _build_menu_probe_reply_from_items,
    build_recommendation_quick_reply_from_items,
    load_quick_reply_preload,
    peek_quick_reply_trigger,
    try_quick_reply,
)
from app.services.tenant_scope import ensure_default_location
from app.services.trace_context import (
    build_conversation_id,
    build_trace_id,
    get_trace_id,
    merge_trace_meta,
    publish_chat_event,
    publish_human_event,
    publish_state_event,
    trace_context,
    trace_log_prefix,
    trace_payload,
)
from app.services.intent_router import (
    cancel_all_draft_orders_for_phone,
    cancel_booking,
    cancel_order,
    confirm_booking,
    confirm_order,
    get_open_draft_order,
    get_or_create_user,
    RouteResult,
    route_intent,
)
from app.services.order_logic import (
    build_summary_text_from_stored_items,
    detect_payment_method_from_text,
    format_whatsapp_order_card,
    merge_total_into_items_json,
)
from app.services.stoplist_session import (
    load_seen_stopped_keys,
    newly_stopped_names,
    save_seen_stopped_keys,
)
from app.services.bot_sla_status import (
    SLOW_CHAT_SECONDS,
    clear_chat_slow,
    get_slow_chat_count,
    is_org_in_short_mode,
    last_msg_key,
    mark_chat_slow_once,
    sla_payload,
    SHORT_MODE_THRESHOLD,
)
from app.services.whatsapp_idempotency import (
    cache_whatsapp_inbound_done_redis,
    mark_whatsapp_inbound_done,
    mark_whatsapp_inbound_failed,
    redis_whatsapp_inbound_done_cache_hit,
    try_start_whatsapp_inbound_in_db,
)
from app.services.tts_edge import synthesize_speech_mp3
from app.services.chat_serializer import ChatMessagePayload, run_serialized_chat_pipeline
from app.schemas.ai_schemas import AIBrainResponse

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

# Twilio CallSid → From (E.164) / org_id, если Redis выключен — только один инстанс
_twilio_caller_memory: dict[str, str] = {}
_twilio_org_memory: dict[str, int] = {}
_twilio_mode_memory: dict[str, str] = {}
_twilio_location_memory: dict[str, int] = {}


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


async def _store_twilio_caller(
    call_sid: str,
    phone: str,
    org_id: int | None = None,
    *,
    voice_mode: str | None = None,
    location_id: int | None = None,
) -> None:
    if not call_sid or not phone:
        return
    key = f"twilio:caller:{call_sid}"
    if settings.redis_enabled:
        try:
            await redis_client.setex(key, 600, phone)
            if org_id is not None:
                await redis_client.setex(f"twilio:org:{call_sid}", 600, str(int(org_id)))
            if voice_mode:
                await redis_client.setex(f"twilio:mode:{call_sid}", 600, voice_mode)
            if location_id is not None:
                await redis_client.setex(f"twilio:location:{call_sid}", 600, str(int(location_id)))
            return
        except Exception as exc:
            logger.warning("Redis twilio caller cache: %s", exc)
    _twilio_caller_memory[call_sid] = phone
    if org_id is not None:
        _twilio_org_memory[call_sid] = int(org_id)
    if voice_mode:
        _twilio_mode_memory[call_sid] = voice_mode
    if location_id is not None:
        _twilio_location_memory[call_sid] = int(location_id)


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
            logger.debug("twilio caller redis lookup failed call_sid=%s", call_sid, exc_info=True)
    return (_twilio_caller_memory.get(call_sid) or "").strip()


async def _get_twilio_org_id(call_sid: str) -> int:
    if not call_sid:
        return int(settings.default_organization_id)
    if settings.redis_enabled:
        try:
            raw = await redis_client.get(f"twilio:org:{call_sid}")
            if raw:
                return int(str(raw).strip())
        except Exception:
            logger.debug("twilio org redis lookup failed call_sid=%s", call_sid, exc_info=True)
    return int(_twilio_org_memory.get(call_sid) or settings.default_organization_id)


async def _get_twilio_voice_mode(call_sid: str) -> str:
    if not call_sid:
        return "stt_fallback"
    if settings.redis_enabled:
        try:
            raw = await redis_client.get(f"twilio:mode:{call_sid}")
            if raw:
                mode = str(raw).strip().lower()
                return "realtime" if mode == "realtime" else "stt_fallback"
        except Exception:
            logger.debug("twilio mode redis lookup failed call_sid=%s", call_sid, exc_info=True)
    return _twilio_mode_memory.get(call_sid) or "stt_fallback"


async def _get_twilio_location_id(call_sid: str) -> int | None:
    if not call_sid:
        return None
    if settings.redis_enabled:
        try:
            raw = await redis_client.get(f"twilio:location:{call_sid}")
            if raw:
                return int(str(raw).strip())
        except Exception:
            logger.debug("twilio location redis lookup failed call_sid=%s", call_sid, exc_info=True)
    loc = _twilio_location_memory.get(call_sid)
    return int(loc) if loc is not None else None


def _normalize_phone_e164(phone: str) -> str:
    from app.services.phone_normalize import normalize_phone_e164

    return normalize_phone_e164(phone)


def _canonical_whatsapp_phone(phone: str) -> str:
    from app.services.phone_normalize import canonical_user_phone

    return canonical_user_phone(phone)


async def _delayed_processing_feedback(
    phone: str,
    whatsapp_message_id: str,
    *,
    delay_sec: float,
) -> None:
    """Typing indicator (wamid) или короткий ack после задержки — не блокирует LLM."""
    try:
        await asyncio.sleep(max(0.0, delay_sec))
        wmid = (whatsapp_message_id or "").strip()
        if wmid:
            from app.integrations.whatsapp import send_typing_indicator

            await send_typing_indicator(phone, wmid)
            return
        ack = (settings.bot_slow_ack_message or "").strip()
        if ack:
            await send_customer_text(phone, ack)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.debug("slow processing feedback failed phone=%s", phone[-4:], exc_info=True)


def _start_slow_processing_feedback(
    phone: str,
    whatsapp_message_id: str,
    *,
    delay_sec: float | None = None,
) -> asyncio.Task[None] | None:
    if not settings.bot_slow_ack_enabled:
        return None
    delay = float(settings.bot_slow_ack_delay_sec or 2.0) if delay_sec is None else float(delay_sec)
    try:
        return asyncio.create_task(
            _delayed_processing_feedback(phone, whatsapp_message_id, delay_sec=delay),
            name=f"bot_slow_ack_{phone[-4:]}",
        )
    except RuntimeError:
        return None


def _soft_ai_unavailable_response(
    *,
    message_text: str,
    menu_items: list[Any] | None,
    has_draft: bool,
) -> AIBrainResponse:
    """Useful guest-facing answer when the AI provider exhausted retries."""
    trigger = peek_quick_reply_trigger(message_text)
    available_menu = [
        item
        for item in (menu_items or [])
        if bool(getattr(item, "is_available", True)) and str(getattr(item, "name", "") or "").strip()
    ]
    menu_probe_candidates = [
        item
        for item in (menu_items or [])
        if str(getattr(item, "name", "") or "").strip()
    ]

    if trigger == "menu_probe":
        probe = _build_menu_probe_reply_from_items(menu_probe_candidates, message_text)
        if probe:
            return AIBrainResponse(intent="faq", reply_text=probe)

    if trigger in {"recommendation_request", "menu_request"}:
        if trigger == "menu_request":
            sample = "\n".join(
                f"• {str(getattr(item, 'name', '')).strip()}"
                + (f" — {float(getattr(item, 'price', 0) or 0):.0f} ₸" if float(getattr(item, "price", 0) or 0) > 0 else "")
                for item in available_menu[:6]
            )
            if sample:
                return AIBrainResponse(
                    intent="faq",
                    reply_text=f"Вот что могу подсказать по меню сейчас:\n{sample}\n\nЧто добавить в заказ?",
                )
        recommendation = build_recommendation_quick_reply_from_items(available_menu)
        return AIBrainResponse(intent="faq", reply_text=recommendation)

    if has_draft:
        return AIBrainResponse(
            intent="faq",
            reply_text=(
                "Я на связи и продолжу заказ. Напишите одним сообщением, что нужно изменить: "
                "добавить или убрать блюдо, количество, получение и оплату."
            ),
        )

    return AIBrainResponse(
        intent="faq",
        reply_text=(
            "Я помогу с заказом. Напишите, что хотите: блюдо или категорию, например "
            "«плов», «мясное», «напитки», либо сразу количество и получение."
        ),
    )


def _quick_reply_allowed_before_llm(trigger: str | None) -> bool:
    """Only service-like shortcuts bypass the agent; menu/order talk stays LLM-first."""
    if not trigger:
        return False
    return trigger not in {
        "recommendation_request",
        "menu_request",
        "menu_probe",
    }


_POLITE_ACK_RE = re.compile(
    r"^(?:нет[,.\s]+)?(?:спасибо|благодарю|thanks|thank\s*you|мерси|рахмет|no\s*thanks)\s*[!.\s]*$",
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
    channel: str | None = None,
    trace_id: str | None = None,
    conversation_id: str | None = None,
    known_user_id: int | None = None,
    location_id: int | None = None,
    save_user: bool = True,
) -> int | None:
    """
    Сохраняет пару сообщений (user + assistant) в ChatLog.
    Для исходящего ответа в WhatsApp — assistant со статусом sending; возвращает id строки assistant.
    known_user_id: если передан — пропускает get_or_create_user (уже загружен в preflight).
    """
    from app.services.telegram_customer import current_customer_channel, normalize_customer_channel

    msg_channel = normalize_customer_channel(channel or current_customer_channel())
    if known_user_id is not None:
        uid = known_user_id
    else:
        user = await get_or_create_user(db, phone, organization_id)
        uid = user.id
    if save_user:
        db.add(
            ChatLog(
                organization_id=organization_id,
                location_id=location_id,
                user_id=uid,
                role="user",
                content=user_text,
                channel=msg_channel,
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
        "location_id": location_id,
        "user_id": uid,
        "role": "assistant",
        "content": reply_text,
        "channel": msg_channel,
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


async def _save_inbound_chat_log(
    db: AsyncSession,
    phone: str,
    user_text: str,
    *,
    organization_id: int,
    channel: str | None = None,
    whatsapp_message_id: str = "",
    trace_id: str | None = None,
    conversation_id: str | None = None,
    location_id: int | None = None,
) -> tuple[int, int, bool]:
    from app.services.telegram_customer import current_customer_channel, normalize_customer_channel

    user = await get_or_create_user(db, phone, organization_id)
    inbound_mid = (whatsapp_message_id or "").strip()
    if inbound_mid:
        existing_log = await db.scalar(
            select(ChatLog)
            .where(
                ChatLog.organization_id == organization_id,
                ChatLog.user_id == user.id,
                ChatLog.role == "user",
                ChatLog.provider_message_id == inbound_mid,
            )
            .order_by(ChatLog.id.asc())
        )
        if existing_log is not None:
            return int(user.id), int(existing_log.id), False
    log = ChatLog(
        organization_id=organization_id,
        location_id=location_id,
        user_id=user.id,
        role="user",
        content=user_text,
        channel=normalize_customer_channel(channel or current_customer_channel()),
        provider_message_id=inbound_mid or None,
        meta_json=(
            trace_payload(trace_id=trace_id, conversation_id=conversation_id)
            if trace_id and conversation_id else None
        ),
    )
    db.add(log)
    await db.flush()
    return int(user.id), int(log.id), True


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
    prefix = trace_log_prefix()
    order_meta = (items_json or {}).get("order_meta") if isinstance(items_json, dict) else {}
    meta_trace = (order_meta or {}).get("trace_id") if isinstance(order_meta, dict) else None
    if meta_trace and not get_trace_id():
        prefix = f"[trace_id={meta_trace}] "
    logger.info("%siiko send start order=%d org=%d", prefix, order_id, restaurant_organization_id)
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
        async with async_session_factory() as db:
            await clear_pending_order_durable(
                redis_client, db, phone=phone, organization_id=organization_id,
            )
            await db.commit()
        return "Заказ не найден — возможно, истекло время ожидания. Назовите блюда заново."

    if word in CONFIRM_WORDS:
        async with async_session_factory() as db:
            order_row = await db.get(Order, order_id)
            if order_row and order_row.items_json:
                om = (order_row.items_json.get("order_meta") or {})
                if isinstance(om, dict):
                    summary_core = build_summary_text_from_stored_items(order_row.items_json)
                    body = format_whatsapp_order_card(order_row.items_json, summary_core)
                    pay_key = str(om.get("payment_method") or "").strip().lower()
                    pay_human = _PAYMENT_LABEL_RU.get(pay_key, pay_key or "не указан")
                    fulfillment_reply = _missing_fulfillment_after_payment_reply(
                        om,
                        pay_human=pay_human,
                        body=body,
                    )
                    if fulfillment_reply:
                        applied_state = await apply_user_state_transition_in_db(
                            db,
                            redis_client,
                            phone=phone,
                            organization_id=organization_id,
                            new_state=UserState.CHATTING,
                        )
                        await db.commit()
                        await set_user_state(redis_client, phone, applied_state, organization_id=organization_id)
                        return fulfillment_reply

                from app.services.order_confirm_gate import validate_order_ready_to_confirm

                gate = await validate_order_ready_to_confirm(
                    db, order_row, check_fulfillment=True, order_meta=om if isinstance(om, dict) else {},
                )
                if not gate.ok:
                    applied_state = await apply_user_state_transition_in_db(
                        db,
                        redis_client,
                        phone=phone,
                        organization_id=organization_id,
                        new_state=UserState.CHATTING,
                    )
                    await db.commit()
                    await set_user_state(redis_client, phone, applied_state, organization_id=organization_id)
                    return (
                        f"⚠️ {gate.reason}\n\n"
                        "Исправьте детали заказа — напишите, что изменить, или уточните адрес/время."
                    )

                if isinstance(om, dict) and om.get("requires_order_prepayment"):
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
            async with async_session_factory() as db_clr:
                await clear_pending_order_durable(
                    redis_client, db_clr, phone=phone, organization_id=organization_id,
                )
                await db_clr.commit()
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

        async with async_session_factory() as db_clr:
            await clear_pending_order_durable(
                redis_client, db_clr, phone=phone, organization_id=organization_id,
            )
            await db_clr.commit()
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
            await clear_pending_order_durable(
                redis_client, db, phone=phone, organization_id=organization_id,
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

        return (
            "Заказ отменён. Вы можете:\n"
            "  • Назвать новые блюда — я оформлю новый заказ\n"
            "  • Написать что изменить — например «уберите лагман, добавьте плов»\n"
            "  • Или просто продолжить общение"
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
            "  • Или просто продолжить общение"
        )

    return "Пожалуйста, ответьте «Да» для подтверждения или «Нет» для отмены бронирования."


_PAYMENT_LABEL_RU = {
    "cash": "Наличные при получении",
    "card": "Карта при получении",
    "remote": "Удалённая оплата (перевод / ссылка)",
}


def _missing_fulfillment_after_payment_reply(
    order_meta: dict[str, object],
    *,
    pay_human: str,
    body: str,
) -> str:
    ot = str(order_meta.get("order_type") or "").strip().lower()
    delivery_address = str(order_meta.get("delivery_address") or "").strip()
    pickup_note = str(order_meta.get("pickup_time_note") or "").strip()
    if ot == "delivery" and not delivery_address:
        return (
            f"Принял способ оплаты: {pay_human}.\n\n"
            f"{body}\n\n"
            "Перед подтверждением нужен адрес доставки: улица, дом и квартира/подъезд, если есть."
        )
    if ot == "pickup" and not pickup_note:
        return (
            f"Принял способ оплаты: {pay_human}.\n\n"
            f"{body}\n\n"
            "Перед подтверждением уточните, пожалуйста, к какому времени подготовить самовывоз."
        )
    return ""


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
        async with async_session_factory() as db:
            await clear_pending_order_durable(
                redis_client, db, phone=phone, organization_id=organization_id,
            )
            await db.commit()
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
            await clear_pending_order_durable(
                redis_client, db, phone=phone, organization_id=organization_id,
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
        return (
            "Заказ отменён. Вы можете:\n"
            "  • Назвать новые блюда — я оформлю новый заказ\n"
            "  • Написать что изменить\n"
            "  • Или просто продолжить общение"
        )

    pm = detect_payment_method_from_text(message_text)
    if not pm:
        return None  # не способ оплаты и не отмена — вернём None, process_message пропустит через LLM

    async with async_session_factory() as db:
        order = await db.get(Order, order_id)
        if not order or order.status != OrderStatus.DRAFT:
            await clear_pending_order_durable(
                redis_client, db, phone=phone, organization_id=organization_id,
            )
            await db.commit()
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
        fulfillment_reply = _missing_fulfillment_after_payment_reply(
            order_meta,
            pay_human=pay_human,
            body=body,
        )
        if fulfillment_reply:
            applied_state = await apply_user_state_transition_in_db(
                db,
                redis_client,
                phone=phone,
                organization_id=organization_id,
                new_state=UserState.CHATTING,
            )
            await db.commit()
            await set_user_state(redis_client, phone, applied_state, organization_id=organization_id)
            return fulfillment_reply
        reply = (
            f"Принял способ оплаты: {pay_human}.\n\n"
            f"{body}\n\n"
            "✅ Подтверждаете заказ? (Да / Нет)"
        )
        total = float(order.total_price)
        meta_after = (order.items_json or {}).get("order_meta") or {}
        needs_prepay = bool(meta_after.get("requires_order_prepayment"))
        org_ent = await db.get(Organization, organization_id)
        if org_ent and not org_ent.prepayment_enforced:
            needs_prepay = False
        prep_st = (order.prepayment_status or "").strip().lower()
        next_state = UserState.CHATTING if needs_prepay and prep_st not in ("paid", "waived") else UserState.CONFIRMING_ORDER
        applied_state = await apply_user_state_transition_in_db(
            db,
            redis_client,
            phone=phone,
            organization_id=organization_id,
            new_state=next_state,
        )
        await db.commit()
        await set_user_state(redis_client, phone, applied_state, organization_id=organization_id)

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


async def process_inbound_message(
    phone: str,
    message_text: str = "",
    *,
    organization_id: int | None = None,
    channel: str = "whatsapp",
    inbound_message_id: str = "",
    voice_audio: tuple[bytes, str] | None = None,
    webhook_value: dict[str, Any] | None = None,
    trace_id: str | None = None,
    channel_connection_id: int | None = None,
    external_chat_id: str | None = None,
) -> None:
    """
    Shared inbound pipeline for customer channels (WhatsApp, Telegram, …).
    WhatsApp-only: dedupe by ``inbound_message_id``, org resolve from webhook payload.
    """
    from app.services.telegram_customer import current_telegram_chat_id, normalize_customer_channel

    msg_channel = normalize_customer_channel(channel)
    org_id = int(organization_id) if organization_id is not None else int(settings.default_organization_id)
    org_active = True
    pipeline_sw = PipelineStopwatch()
    from app.services.wa_queue_metrics import pop_queue_wait_ms

    wmid = (inbound_message_id or "").strip() if msg_channel == "whatsapp" else ""
    if msg_channel == "whatsapp":
        queue_wait_ms = await pop_queue_wait_ms(
            trace_id=trace_id,
            whatsapp_message_id=wmid,
        )
        if queue_wait_ms is not None:
            pipeline_sw.rm_stage_ms["queue_wait"] = queue_wait_ms
            logger.info(
                "[trace_id=%s] WhatsApp queue_wait_ms=%.0f wmid=%s",
                trace_id or "-",
                queue_wait_ms,
                wmid[:24],
            )
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
        logger.info("org=%s inactive: inbound message ignored", org_id)
        return
    if msg_channel == "whatsapp" and wmid:
        try:
            async with async_session_factory() as db:
                can = await try_start_whatsapp_inbound_in_db(db, message_id=wmid, phone=phone)
                await db.commit()
            if not can:
                return
        except Exception as exc:
            logger.warning("WhatsApp dedupe start failed (mid=%s): %s", wmid[:24], exc)

    if settings.pipeline_timing_enabled:
        pipeline_sw.split("dedupe")

    async def _process_payload_item(item: ChatMessagePayload) -> None:
        nonlocal last_exc
        from app.services.telegram_customer import customer_channel_context, reset_customer_channel_context

        item_channel = normalize_customer_channel(getattr(item, "channel", msg_channel))
        wmid_item = (item.whatsapp_message_id or "").strip() if item_channel == "whatsapp" else ""
        tokens = customer_channel_context(
            item_channel,
            telegram_chat_id=int(getattr(item, "telegram_chat_id", 0) or 0),
            channel_connection_id=int(getattr(item, "channel_connection_id", 0) or 0),
            external_chat_id=str(getattr(item, "external_chat_id", "") or ""),
        )
        try:
            for attempt in range(MAX_RETRIES):
                try:
                    await process_message(
                        item.phone,
                        item.message_text,
                        whatsapp_message_id=wmid_item or (inbound_message_id or ""),
                        voice_audio=item.voice_audio,
                        organization_id=int(item.organization_id),
                        pipeline_sw=pipeline_sw,
                        trace_id=trace_id,
                    )
                    if item_channel == "whatsapp" and wmid_item:
                        try:
                            async with async_session_factory() as db:
                                await mark_whatsapp_inbound_done(db, wmid_item)
                                await db.commit()
                        except Exception as exc:
                            logger.warning("WhatsApp dedupe mark done failed (mid=%s): %s", wmid_item[:24], exc)
                        else:
                            await cache_whatsapp_inbound_done_redis(wmid_item)
                    return
                except Exception as exc:
                    last_exc = exc
                    logger.warning(
                        "Retry %d/%d для %s: %s", attempt + 1, MAX_RETRIES, item.phone, exc,
                    )
                    if attempt < MAX_RETRIES - 1:
                        await asyncio.sleep(2 ** attempt)
    
            logger.error("Все %d попыток исчерпаны для %s: %s", MAX_RETRIES, item.phone, last_exc)
            log_pipeline_stage(
                "failed_queue",
                phone=item.phone,
                extra={"error": str(last_exc)[:500] if last_exc else "", "attempts": MAX_RETRIES},
            )
            await _save_failed_task(
                item.phone, item.message_text, str(last_exc), MAX_RETRIES, organization_id=org_id,
            )
            if item_channel == "whatsapp" and wmid_item:
                try:
                    async with async_session_factory() as db:
                        await mark_whatsapp_inbound_failed(db, wmid_item, str(last_exc or "unknown_error"))
                        await db.commit()
                except Exception as exc:
                    logger.warning("WhatsApp dedupe mark failed error (mid=%s): %s", wmid_item[:24], exc)
            try:
                await send_customer_text(item.phone, "Извините, произошла техническая ошибка. Мы уже работаем над ней — попробуйте написать чуть позже.")
            except Exception:
                logger.debug("failed to send serialized-chat retry exhaustion notice phone=%s", item.phone[-4:], exc_info=True)
        finally:
            reset_customer_channel_context(tokens)

    initial = ChatMessagePayload(
        phone=phone,
        message_text=message_text,
        whatsapp_message_id=wmid,
        organization_id=org_id,
        voice_audio=voice_audio,
        channel=msg_channel,
        telegram_chat_id=current_telegram_chat_id() if msg_channel == "telegram" else 0,
        channel_connection_id=int(channel_connection_id or 0),
        external_chat_id=(external_chat_id or "").strip(),
    )
    last_exc: BaseException | None = None
    acquired = await run_serialized_chat_pipeline(
        org_id,
        phone,
        initial,
        process_one=_process_payload_item,
    )
    if acquired:
        return

    logger.info(
        "chat_serializer.deferred org_id=%s phone=%s wmid=%s channel=%s",
        org_id,
        _redact_msisdn_for_log(phone),
        wmid[:16] if wmid else "",
        msg_channel,
    )


async def process_with_retry(
    phone: str,
    message_text: str = "",
    *,
    whatsapp_message_id: str = "",
    voice_audio: tuple[bytes, str] | None = None,
    webhook_value: dict[str, Any] | None = None,
    organization_id: int | None = None,
    trace_id: str | None = None,
) -> None:
    """
    Обёртка с retry + exponential backoff.
    После MAX_RETRIES неудач → сохраняет в FailedTask и извиняется перед клиентом.
    """
    await process_inbound_message(
        phone,
        message_text,
        organization_id=organization_id,
        channel="whatsapp",
        inbound_message_id=whatsapp_message_id,
        voice_audio=voice_audio,
        webhook_value=webhook_value,
        trace_id=trace_id,
    )


_OPERATOR_ONLY_REPLY = (
    "Ваш вопрос уже у менеджера. Он ответит вам здесь в ближайшее время."
)


async def _dispatch_bypass_assistant_reply(
    *,
    phone: str,
    organization_id: int,
    message_text: str,
    reply_text: str,
    trace_id: str,
    conversation_id: str,
    pipeline_stage: str,
    qr_hit: QuickReplyHit | None = None,
    active_location_id: int | None = None,
    u_row: User | None = None,
    pipe_sw: PipelineStopwatch | None = None,
) -> None:
    """Отправка детерминированного ответа (quick reply / FAQ cache) без LLM."""
    outbound_id: int | None = None
    async with async_session_factory() as db:
        if active_location_id is None:
            active_location_id = await _resolve_default_location_id(db, organization_id)
        if qr_hit is not None and "cancel_open_draft" in qr_hit.side_effects:
            await cancel_all_draft_orders_for_phone(
                db,
                phone,
                organization_id,
                trace_id=trace_id,
                conversation_id=conversation_id,
                source="webhooks.quick_reply",
            )
            await clear_pending_order_durable(
                redis_client,
                db,
                phone=phone,
                organization_id=organization_id,
            )
        if qr_hit is not None and qr_hit.set_human_mode:
            db.add(
                EscalationEvent(
                    organization_id=organization_id,
                    phone=phone,
                    user_message=(message_text or "")[:2000],
                    reason=(reply_text or "")[:2000],
                ),
            )
            await emit_event(
                db,
                BusinessEvent(
                    org_id=organization_id,
                    type="ai.escalated",
                    actor="quick_reply",
                    entity_type="user",
                    entity_id=phone,
                    payload={
                        "phone": phone,
                        "reason": (reply_text or "")[:500],
                        "template_id": qr_hit.template_id,
                    },
                ),
            )
        outbound_id = await _save_chat_log(
            db,
            phone,
            message_text,
            reply_text,
            organization_id=organization_id,
            trace_id=trace_id,
            conversation_id=conversation_id,
            location_id=active_location_id,
            known_user_id=int(u_row.id) if u_row is not None and u_row.id is not None else None,
            save_user=False,
        )
        await db.commit()

    await append_to_history(
        redis_client, phone, "assistant", reply_text, organization_id=organization_id,
    )
    await publish_chat_event(
        phone=phone,
        role="assistant",
        content=reply_text,
        organization_id=organization_id,
        chat_log_id=outbound_id,
        trace_id=trace_id,
        conversation_id=conversation_id,
        meta={"bypass": pipeline_stage, "template_id": qr_hit.template_id if qr_hit else None},
    )
    await send_customer_text(phone, reply_text, outbound_chat_log_id=outbound_id)

    if qr_hit is not None and qr_hit.set_human_mode:
        await set_user_state(redis_client, phone, UserState.HUMAN_MODE, organization_id=organization_id)
        await publish_human_event(
            phone=phone,
            organization_id=organization_id,
            reason=reply_text,
            user_message=message_text or "",
            trace_id=trace_id,
            conversation_id=conversation_id,
            intent="escalate",
        )
        await publish_state_event(
            phone=phone,
            state=UserState.HUMAN_MODE.value,
            organization_id=organization_id,
            trace_id=trace_id,
            conversation_id=conversation_id,
        )
        try:
            await send_tg_fallback_alert(
                phone,
                message_text,
                reply_text,
                extras=EscalationAlertExtras(intent="escalate", fsm_state=UserState.HUMAN_MODE.value),
                organization_id=organization_id,
            )
        except Exception as tg_exc:
            logger.warning("Telegram quick_reply escalation alert failed: %s", tg_exc)
    elif qr_hit is not None and qr_hit.new_state is not None:
        await set_user_state(
            redis_client, phone, qr_hit.new_state, organization_id=organization_id,
        )

    log_pipeline_stage(
        pipeline_stage,
        phone=phone,
        extra={
            "organization_id": organization_id,
            "template_id": qr_hit.template_id if qr_hit else None,
        },
    )
    if pipe_sw is not None and settings.pipeline_timing_enabled:
        pipe_sw.split(pipeline_stage)
        log_pipeline_rm_stage_ms(
            phone_tail=_redact_msisdn_for_log(phone),
            rm_stage_ms=pipe_sw.rm_stage_ms,
            extra={"organization_id": organization_id, "path": pipeline_stage},
        )


async def _reset_stale_cart_if_needed(
    phone: str,
    organization_id: int,
    history: list[dict[str, str]],
    message_text: str = "",
    *,
    trace_id: str = "",
    conversation_id: str = "",
) -> bool:
    """
    Если Redis-история пуста (TTL 24 ч), а в БД остался DRAFT — сбрасываем «чужую» корзину.
    """
    async with async_session_factory() as db:
        draft = await get_open_draft_order(db, phone, organization_id)
        if draft is None:
            return False
        should_reset = not history
        if not should_reset:
            should_reset = _should_reset_existing_draft_for_message(draft, message_text)
        if not should_reset:
            return False
        n = await cancel_all_draft_orders_for_phone(
            db,
            phone,
            organization_id,
            trace_id=trace_id,
            conversation_id=conversation_id,
            source="webhooks.stale_cart_reset",
        )
        await clear_pending_order_durable(
            redis_client,
            db,
            phone=phone,
            organization_id=organization_id,
            reset_state=True,
        )
        await db.commit()
    if n:
        logger.info(
            "stale_cart_reset: org=%s phone=%s cancelled_drafts=%d",
            organization_id, phone, n,
        )
    return n > 0


_NEW_CONVERSATION_HINTS = (
    "здравствуйте",
    "добрый день",
    "добрый вечер",
    "доброе утро",
    "привет",
    "хочу сделать заказ",
    "хочу заказать",
    "можно заказать",
    "что есть",
    "что у вас есть",
    "меню",
    "скиньте меню",
    "есть плов",
    "плов есть",
)


def _should_reset_existing_draft_for_message(draft: Order, message_text: str) -> bool:
    updated = getattr(draft, "updated_at", None) or getattr(draft, "created_at", None)
    if updated is None:
        return False
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - updated
    if age >= timedelta(hours=12):
        return True

    text = (message_text or "").strip().lower()
    if not text:
        return False
    compact = re.sub(r"\s+", " ", text)
    if age >= timedelta(minutes=45) and any(h in compact for h in _NEW_CONVERSATION_HINTS):
        return True
    return False


def _should_resume_bot_from_human_mode(message_text: str) -> bool:
    """Explicit new-session phrases let a customer return to bot without operator action."""
    text = (message_text or "").strip().lower()
    if not text:
        return False
    compact = re.sub(r"\s+", " ", text)
    return any(h in compact for h in _NEW_CONVERSATION_HINTS)


async def _handle_cancel_all_in_chatting(
    phone: str,
    message_text: str,
    organization_id: int,
    *,
    trace_id: str = "",
    conversation_id: str = "",
) -> str:
    """Детерминированная отмена всех черновиков (без LLM)."""
    async with async_session_factory() as db:
        n = await cancel_all_draft_orders_for_phone(
            db,
            phone,
            organization_id,
            trace_id=trace_id,
            conversation_id=conversation_id,
            source="webhooks.cancel_all",
        )
        await clear_pending_order_durable(
            redis_client,
            db,
            phone=phone,
            organization_id=organization_id,
            reset_state=True,
        )
        await db.commit()
    if n:
        return (
            "Хорошо, отменил текущий заказ. Можете собрать новый с нуля — "
            "просто напишите, что хотите заказать."
        )
    return (
        "Сейчас активного заказа нет. Напишите блюда — оформлю новый заказ."
    )


async def _is_org_overloaded(redis_client: Any, organization_id: int, location_id: int | None = None) -> bool:
    """G4 Auto-Short: True если у org > 3 чатов ждут ответа > 5 минут.

    Используем Redis-счётчик с TTL=10мин. Инкрементируется при каждом входящем
    сообщении если предыдущий ответ по этому диалогу был > 5 мин назад.
    Счётчик декрементируется при успешном ответе бота.
    """
    return await is_org_in_short_mode(redis_client, organization_id, location_id)


async def _track_slow_chat(
    redis_client: Any,
    organization_id: int,
    phone: str,
    location_id: int | None = None,
) -> bool:
    """Отмечает чат как медленный (инкремент счётчика); убирается после ответа."""
    try:
        last_key = last_msg_key(organization_id, phone, location_id)
        last_ts_raw = await redis_client.get(last_key)
        now_ts = datetime.now(tz=timezone.utc).timestamp()
        await redis_client.setex(last_key, 600, str(now_ts))
        if last_ts_raw:
            elapsed = now_ts - float(last_ts_raw)
            if elapsed > SLOW_CHAT_SECONDS:  # > 5 минут — считаем медленным
                return await mark_chat_slow_once(redis_client, organization_id, phone, location_id)
    except Exception:
        logger.debug("slow chat tracking failed org=%s phone=%s", organization_id, phone[-4:], exc_info=True)
    return False


async def _untrack_slow_chat(
    redis_client: Any,
    organization_id: int,
    phone: str | None = None,
    location_id: int | None = None,
) -> bool:
    """Декремент счётчика перегруза после успешного ответа бота."""
    return await clear_chat_slow(redis_client, organization_id, phone, location_id)


async def _publish_bot_sla_status(
    redis_client: Any,
    organization_id: int,
    *,
    location_id: int | None = None,
    phone: str | None = None,
    chat_slow: bool | None = None,
) -> None:
    try:
        slow_chats = await get_slow_chat_count(redis_client, organization_id, location_id)
        await publish_event(
            "bot_sla_status",
            sla_payload(
                organization_id=organization_id,
                location_id=location_id,
                slow_chats=slow_chats,
                bot_short_mode=slow_chats > SHORT_MODE_THRESHOLD,
                phone=phone,
                chat_slow=chat_slow,
            ),
        )
    except Exception:
        logger.debug("bot_sla_status publish failed", exc_info=True)


async def _resolve_default_location_id(db: AsyncSession, organization_id: int) -> int | None:
    try:
        loc = await ensure_default_location(db, int(organization_id))
        return int(loc.id) if loc and loc.id is not None else None
    except Exception:
        logger.debug("default location resolve failed for org=%s", organization_id, exc_info=True)
        return None


async def process_message(
    phone: str,
    message_text: str = "",
    *,
    whatsapp_message_id: str = "",
    voice_audio: tuple[bytes, str] | None = None,
    organization_id: int,
    pipeline_sw: PipelineStopwatch | None = None,
    trace_id: str | None = None,
) -> None:
    """
    Полный цикл обработки входящего сообщения с учётом State Machine.
    """
    phone = _canonical_whatsapp_phone(phone)
    conversation_id = build_conversation_id(organization_id, phone)
    effective_trace_id = trace_id or build_trace_id(whatsapp_message_id)
    try:
        with trace_context(effective_trace_id, conversation_id):
            await _process_message_inner(
                phone,
                message_text,
                whatsapp_message_id=whatsapp_message_id,
                voice_audio=voice_audio,
                organization_id=organization_id,
                pipeline_sw=pipeline_sw,
                trace_id=effective_trace_id,
                conversation_id=conversation_id,
            )
    except Exception as exc:
        logger.error(
            "%sОшибка обработки сообщения от %s: %s",
            trace_log_prefix(),
            phone,
            exc,
            exc_info=True,
        )
        raise


async def _process_message_inner(
    phone: str,
    message_text: str = "",
    *,
    whatsapp_message_id: str = "",
    voice_audio: tuple[bytes, str] | None = None,
    organization_id: int,
    pipeline_sw: PipelineStopwatch | None = None,
    trace_id: str,
    conversation_id: str,
) -> None:
    logger.info(
        "%sprocess_message start org=%d phone=%s",
        trace_log_prefix(),
        organization_id,
        _redact_msisdn_for_log(phone),
    )
    try:
        pipe_sw = pipeline_sw or PipelineStopwatch()
        active_location_id: int | None = None
        state = await get_user_state(redis_client, phone, organization_id=organization_id)
        preflight_session_version: int | None = None

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
            preflight_session_version = (
                int(getattr(u_row, "session_version", 0) or 0) if u_row is not None else None
            )
            db_human_mode = False
            if u_row is not None:
                db_human_mode = (
                    normalize_conversation_state(
                        getattr(u_row, "current_state", None) or "",
                    )
                    == ConversationState.HUMAN_MODE
                )
            ai_snooze_active = False
            if u_row is not None:
                from app.services.ai_snooze import ai_snooze_is_active, clear_ai_snooze_if_expired

                await clear_ai_snooze_if_expired(db_u, u_row)
                ai_snooze_active = ai_snooze_is_active(u_row)
                await db_u.commit()
            if u_row is not None and db_human_mode and not ai_paused_db:
                human_mode_until = parse_human_mode_until(getattr(u_row, "meta_json", None))
                if human_mode_until is not None and human_mode_until <= datetime.now(timezone.utc):
                    u_row.meta_json = clear_human_mode_ttl_meta(getattr(u_row, "meta_json", None))
                    await update_user_session_fields_in_db(
                        db_u,
                        phone=phone,
                        organization_id=organization_id,
                        current_state=UserState.CHATTING.value,
                        transition_source="webhooks.auto_resume",
                        transition_reason="human_mode_ttl_expired",
                        transition_context=trace_payload(trace_id=trace_id, conversation_id=conversation_id),
                    )
                    await db_u.commit()
                    await set_user_state(
                        redis_client, phone, UserState.CHATTING, organization_id=organization_id,
                    )
                    state = UserState.CHATTING
                    db_human_mode = False
                    preflight_session_version = None

        if db_human_mode and state != UserState.HUMAN_MODE:
            state = UserState.HUMAN_MODE
            await set_user_state(redis_client, phone, UserState.HUMAN_MODE, organization_id=organization_id)

        if (
            db_human_mode
            and not ai_paused_db
            and not ai_snooze_active
            and _should_resume_bot_from_human_mode(message_text)
        ):
            async with async_session_factory() as db_resume:
                resume_user = await db_resume.scalar(
                    select(User).where(
                        User.phone == phone,
                        User.organization_id == organization_id,
                    ),
                )
                if resume_user is not None:
                    resume_user.meta_json = clear_human_mode_ttl_meta(getattr(resume_user, "meta_json", None))
                    await update_user_session_fields_in_db(
                        db_resume,
                        phone=phone,
                        organization_id=organization_id,
                        current_state=UserState.CHATTING.value,
                        transition_source="webhooks.auto_resume",
                        transition_reason="new_conversation_hint",
                        transition_context=trace_payload(trace_id=trace_id, conversation_id=conversation_id),
                    )
                await db_resume.commit()
            await set_user_state(
                redis_client, phone, UserState.CHATTING, organization_id=organization_id,
            )
            state = UserState.CHATTING
            db_human_mode = False
            preflight_session_version = None

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
        inbound_user_id: int | None = None
        inbound_log_id: int | None = None
        inbound_log_created = False
        if user_evt:
            try:
                async with async_session_factory() as db_in:
                    inbound_user_id, inbound_log_id, inbound_log_created = await _save_inbound_chat_log(
                        db_in,
                        phone,
                        user_evt,
                        organization_id=organization_id,
                        whatsapp_message_id=wmid,
                        trace_id=trace_id,
                        conversation_id=conversation_id,
                        location_id=active_location_id,
                    )
                    await db_in.commit()
                if inbound_log_created:
                    await publish_chat_event(
                        phone=phone,
                        role="user",
                        content=user_evt,
                        organization_id=organization_id,
                        chat_log_id=inbound_log_id,
                        trace_id=trace_id,
                        conversation_id=conversation_id,
                    )
            except Exception:
                logger.exception("inbound ChatLog early save failed org=%s phone=%s", organization_id, phone[-4:])
                await publish_event("new_message", {
                    "phone": phone,
                    "role": "user",
                    "content": user_evt,
                    "organization_id": organization_id,
                    "trace_id": trace_id,
                    "conversation_id": conversation_id,
                })

        # ─── HUMAN_MODE / ai_paused / ai_snooze: AI молчит ─────────
        operator_only = (
            state == UserState.HUMAN_MODE
            or db_human_mode
            or ai_paused_db
            or ai_snooze_active
        )
        if operator_only:
            outbound_op: int | None = None
            operator_meta = {"operator_only": True}
            async with async_session_factory() as db:
                outbound_op = await _save_chat_log(
                    db,
                    phone,
                    message_text,
                    _OPERATOR_ONLY_REPLY,
                    operator_meta,
                    organization_id=organization_id,
                    trace_id=trace_id,
                    conversation_id=conversation_id,
                    known_user_id=inbound_user_id,
                    save_user=False,
                )
                await db.commit()
            await append_to_history(
                redis_client, phone, "user", message_text, organization_id=organization_id,
            )
            await append_to_history(
                redis_client, phone, "assistant", _OPERATOR_ONLY_REPLY, organization_id=organization_id,
            )
            await publish_chat_event(
                phone=phone,
                role="assistant",
                content=_OPERATOR_ONLY_REPLY,
                organization_id=organization_id,
                chat_log_id=outbound_op,
                trace_id=trace_id,
                conversation_id=conversation_id,
                meta=operator_meta,
            )
            if state == UserState.HUMAN_MODE or db_human_mode:
                await publish_state_event(
                    phone=phone,
                    state=UserState.HUMAN_MODE.value,
                    organization_id=organization_id,
                    trace_id=trace_id,
                    conversation_id=conversation_id,
                )
            await send_customer_text(phone, _OPERATOR_ONLY_REPLY, outbound_chat_log_id=outbound_op)
            logger.info(
                "operator_only: %s human=%s db_human=%s paused=%s snooze=%s",
                phone,
                state == UserState.HUMAN_MODE,
                db_human_mode,
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
                        known_user_id=inbound_user_id, save_user=False,
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
                        known_user_id=inbound_user_id, save_user=False,
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
                    known_user_id=inbound_user_id, save_user=False,
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

        await _reset_stale_cart_if_needed(
            phone,
            organization_id,
            history,
            message_text,
            trace_id=trace_id,
            conversation_id=conversation_id,
        )

        if (
            message_text.strip()
            and not had_voice
            and is_cancel_all_message(message_text)
        ):
            cancel_reply = await _handle_cancel_all_in_chatting(
                phone,
                message_text,
                organization_id,
                trace_id=trace_id,
                conversation_id=conversation_id,
            )
            outbound_cancel: int | None = None
            async with async_session_factory() as db_cancel:
                outbound_cancel = await _save_chat_log(
                    db_cancel,
                    phone,
                    message_text,
                    cancel_reply,
                    organization_id=organization_id,
                    trace_id=trace_id,
                    conversation_id=conversation_id,
                    known_user_id=inbound_user_id,
                    save_user=False,
                )
                await db_cancel.commit()
            await append_to_history(
                redis_client, phone, "user", message_text, organization_id=organization_id,
            )
            await append_to_history(
                redis_client, phone, "assistant", cancel_reply, organization_id=organization_id,
            )
            await publish_event("new_message", {
                "phone": phone,
                "role": "assistant",
                "content": cancel_reply,
                "id": outbound_cancel,
                "delivery_status": "sending",
                "organization_id": organization_id,
                "trace_id": trace_id,
                "conversation_id": conversation_id,
            })
            await send_customer_text(phone, cancel_reply, outbound_chat_log_id=outbound_cancel)
            return

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
            try:
                from app.services.dialog_events import emit_dialog_started_once

                async with async_session_factory() as db_dialog:
                    await emit_dialog_started_once(
                        db_dialog,
                        organization_id=organization_id,
                        phone=phone,
                    )
                    await db_dialog.commit()
            except Exception:
                logger.debug("emit_dialog_started_once skipped", exc_info=True)

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
                    known_user_id=inbound_user_id,
                    save_user=False,
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
        slow_feedback_task = _start_slow_processing_feedback(phone, wmid, delay_sec=0.0)

        if settings.quick_replies_enabled and message_text.strip() and not had_voice:
            qr_trigger = peek_quick_reply_trigger(message_text)
            quick_reply_pre_llm_allowed = _quick_reply_allowed_before_llm(qr_trigger)
        else:
            quick_reply_pre_llm_allowed = False

        if quick_reply_pre_llm_allowed:
            async with async_session_factory() as db_qr:
                qr_preload = await load_quick_reply_preload(
                    db_qr,
                    phone=phone,
                    organization_id=organization_id,
                    message_text=message_text,
                )
            qr_hit = await try_quick_reply(
                phone=phone,
                organization_id=organization_id,
                message_text=message_text,
                state=state,
                has_open_draft=qr_preload.has_open_draft,
                org=qr_preload.org,
                menu_preview=qr_preload.menu_preview,
                recommendation_preview=qr_preload.recommendation_preview,
                menu_probe_text=qr_preload.menu_probe_text,
                order_status_text=qr_preload.order_status_text,
            )
            if qr_hit is not None:
                if slow_feedback_task is not None:
                    slow_feedback_task.cancel()
                    try:
                        await slow_feedback_task
                    except asyncio.CancelledError:
                        pass
                await _dispatch_bypass_assistant_reply(
                    phone=phone,
                    organization_id=organization_id,
                    message_text=message_text,
                    reply_text=qr_hit.reply_text,
                    trace_id=trace_id,
                    conversation_id=conversation_id,
                    pipeline_stage="quick_reply_bypass",
                    qr_hit=qr_hit,
                    u_row=None,
                    pipe_sw=pipe_sw,
                )
                return

        # 1) Параллельное чтение контекста + Redis stoplist keys
        read_ctx, prev_stopped_keys = await asyncio.gather(
            fetch_ai_read_context(phone, organization_id, location_id=active_location_id),
            load_seen_stopped_keys(redis_client, phone, organization_id),
        )
        menu_items = read_ctx.menu_items
        if prev_stopped_keys:
            fresh_stopped = newly_stopped_names(prev_stopped_keys, menu_items)
        else:
            fresh_stopped = []
        prompt_bundle = await build_llm_prompt_bundle(
            read_ctx,
            organization_id=organization_id,
            message_text=message_text,
            fresh_stopped=fresh_stopped,
        )
        menu_context = prompt_bundle.menu_context
        kb_context = prompt_bundle.kb_context
        draft_ctx = prompt_bundle.draft_ctx
        strategy_ctx = prompt_bundle.strategy_ctx
        customer_ctx = prompt_bundle.customer_ctx
        current_time_ctx = prompt_bundle.current_time_ctx
        sales_gastro_hint = prompt_bundle.sales_gastro_hint
        sales_target_iiko_ids = prompt_bundle.sales_target_iiko_ids
        draft_row = read_ctx.draft_row
        u_row = read_ctx.user

        if settings.pipeline_timing_enabled:
            pipe_sw.split("context")

        # Phase 3 OS: сохранить снимок контекста до LLM (fire-and-forget, не блокирует)
        ai_snapshot_id = schedule_save_ai_context_snapshot(
            phone,
            organization_id,
            read_ctx,
            menu_context_text=menu_context,
            trace_id=trace_id,
            conversation_id=conversation_id,
        )

        # G4 Money MVP: трекинг медленных чатов + краткий режим при перегрузе
        active_location_id = getattr(draft_row, "location_id", None)
        if active_location_id is None:
            async with async_session_factory() as db_loc:
                active_location_id = await _resolve_default_location_id(db_loc, organization_id)
                await db_loc.commit()
        short_before = await _is_org_overloaded(redis_client, organization_id, active_location_id)
        chat_marked_slow = await _track_slow_chat(redis_client, organization_id, phone, active_location_id)
        short_after = await _is_org_overloaded(redis_client, organization_id, active_location_id)
        if chat_marked_slow or short_after != short_before:
            await _publish_bot_sla_status(
                redis_client,
                organization_id,
                location_id=active_location_id,
                phone=phone,
                chat_slow=True,
            )
        if short_after:
            menu_context = "[КРАТКИЙ РЕЖИМ] Очередь загружена — отвечай коротко и по делу, без апсейла.\n" + menu_context

        kb_fp = kb_fingerprint_from_text(kb_context)
        if (
            settings.faq_cache_enabled
            and not had_voice
            and draft_row is None
            and state == UserState.CHATTING
        ):
            faq_reply = await get_cached_faq_reply(
                org_id=organization_id,
                message_text=message_text,
                kb_fingerprint=kb_fp,
            )
            if faq_reply:
                if slow_feedback_task is not None:
                    slow_feedback_task.cancel()
                    try:
                        await slow_feedback_task
                    except asyncio.CancelledError:
                        pass
                await _dispatch_bypass_assistant_reply(
                    phone=phone,
                    organization_id=organization_id,
                    message_text=message_text,
                    reply_text=faq_reply,
                    trace_id=trace_id,
                    conversation_id=conversation_id,
                    pipeline_stage="faq_cache_hit",
                    active_location_id=active_location_id,
                    u_row=u_row,
                    pipe_sw=pipe_sw,
                )
                return

        if settings.prompt_size_metric_enabled:
            history, menu_context, size_before, size_after, trimmed = apply_prompt_size_controls(
                history,
                menu_context=menu_context,
                kb_context=kb_context,
                draft_ctx=draft_ctx,
                strategy_ctx=strategy_ctx,
                customer_ctx=customer_ctx,
                current_time_ctx=current_time_ctx,
                user_text=message_text,
                soft_limit=settings.prompt_max_tokens_soft,
                hard_limit=settings.prompt_max_tokens_hard,
                min_keep=settings.prompt_history_min_keep,
            )
            log_pipeline_stage(
                "prompt_size",
                phone=phone,
                extra={
                    "organization_id": organization_id,
                    "tokens": size_before.estimated_tokens,
                    "parts": size_before.parts,
                    "trimmed": trimmed,
                    "tokens_after": size_after.estimated_tokens if size_after else None,
                },
            )
            if trimmed and size_after is not None:
                logger.warning(
                    "prompt_oversize org=%d tokens_before=%d tokens_after=%d history_len=%d",
                    organization_id,
                    size_before.estimated_tokens,
                    size_after.estimated_tokens,
                    len(history),
                )
            if size_after is not None and size_after.estimated_tokens > settings.prompt_max_tokens_hard:
                logger.warning(
                    "prompt_hard_limit org=%d tokens=%d hard=%d",
                    organization_id,
                    size_after.estimated_tokens,
                    settings.prompt_max_tokens_hard,
                )

        # 2) OpenAI: без DB-сессии
        try:
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
                    trace_id=trace_id,
                    has_draft=draft_row is not None,
                )
        finally:
            if slow_feedback_task is not None:
                slow_feedback_task.cancel()
                try:
                    await slow_feedback_task
                except asyncio.CancelledError:
                    pass

        if settings.pipeline_timing_enabled:
            pipe_sw.split("llm")

        ai_provider_soft_fallback = False
        if is_openai_fallback_escalation_reply(ai_response.reply_text):
            ai_provider_soft_fallback = True
            logger.warning(
                "%sAI provider fallback softened org=%s phone=%s trigger=%s has_draft=%s",
                trace_log_prefix(),
                organization_id,
                _redact_msisdn_for_log(phone),
                peek_quick_reply_trigger(message_text) or "generic",
                draft_row is not None,
            )
            ai_response = _soft_ai_unavailable_response(
                message_text=message_text,
                menu_items=menu_items,
                has_draft=draft_row is not None,
            )

        log_pipeline_stage(
            "llm_ok",
            phone=phone,
            extra={"intent": ai_response.intent, "voice": had_voice, "soft_fallback": ai_provider_soft_fallback},
        )
        schedule_log_ai_usage(organization_id, getattr(ai_response, "_usage", None))
        # P4: регистрируем транзиентную AI-ошибку (fallback = провайдер недоступен)
        if ai_provider_soft_fallback:
            schedule_log_ai_error(organization_id)

        from app.services.fulfillment_infer import enrich_ai_fulfillment_from_message
        from app.services.upsell_safety_gate import (
            UpsellSafetyContext,
            should_suppress_upsell,
            strip_upsell_from_ai_response,
        )

        ai_response = enrich_ai_fulfillment_from_message(
            ai_response,
            message_text,
            has_draft=draft_row is not None,
        )

        draft_meta: dict = {}
        if draft_row and isinstance(draft_row.items_json, dict):
            om = draft_row.items_json.get("order_meta")
            if isinstance(om, dict):
                draft_meta = om

        upsell_ctx = UpsellSafetyContext(
            user_message=message_text,
            dialog_state=state,
            order_meta=draft_meta,
            intent=ai_response.intent,
        )
        if should_suppress_upsell(upsell_ctx):
            ai_response = strip_upsell_from_ai_response(ai_response)

        from app.services.plov_kazan_schedule import enrich_plov_kazan_reply_if_needed

        ai_response = enrich_plov_kazan_reply_if_needed(
            ai_response,
            message_text,
            menu_items,
            timezone_name=getattr(read_ctx.org, "timezone", None) if read_ctx.org else None,
            org_meta_json=getattr(read_ctx.org, "meta_json", None) if read_ctx.org else None,
        )

        if should_save_faq_reply(ai_response, has_draft=draft_row is not None):
            await save_faq_reply(
                org_id=organization_id,
                message_text=message_text,
                kb_fingerprint=kb_fp,
                reply=ai_response.reply_text,
            )

        # Phase 4 OS: Decision Engine — валидация AI-ответа до исполнения
        try:
            de_result = await decision_engine.validate(
                ai_response, read_ctx, read_ctx.org,
                tenant=read_ctx.tenant,
                billing_suspended=getattr(read_ctx.org, "is_active", True) is False,
                user_message=message_text,
            )
            if not de_result.is_valid and de_result.corrected_response is not None:
                logger.info(
                    "DecisionEngine blocked intent=%s org=%d violations=%s",
                    ai_response.intent, organization_id,
                    [v.rule for v in de_result.block_violations],
                )
                ai_response = de_result.corrected_response
        except Exception:
            logger.exception("DecisionEngine.validate failed, proceeding with original response")

        # 3) DB: короткая мутация/запись результатов
        post_commit_state: UserState | None = None
        post_commit_pending_order: int | None = None
        post_commit_pending_booking: int | None = None
        db_state_applied = True
        async with async_session_factory() as db:
            current_db_row = await db.execute(
                select(User.current_state, User.session_version).where(
                    User.phone == phone,
                    User.organization_id == organization_id,
                ),
            )
            current_db_state, current_session_version = (
                current_db_row.first() or (None, None)
            )
            session_version_changed = (
                preflight_session_version is not None
                and current_session_version is not None
                and int(current_session_version or 0) != preflight_session_version
            )
            if (
                normalize_conversation_state(current_db_state) != normalize_conversation_state(state.value)
                or session_version_changed
            ):
                db_state_applied = False
                result = RouteResult(reply_text="")
                logger.info(
                    "Skip stale LLM route org=%s phone=%s expected_state=%s actual_state=%s "
                    "expected_version=%s actual_version=%s",
                    organization_id,
                    phone,
                    state.value,
                    normalize_conversation_state(current_db_state).value,
                    preflight_session_version,
                    current_session_version,
                )
            else:
                result = await route_intent(
                    db,
                    phone,
                    ai_response,
                    menu_items=menu_items,
                    organization_id=organization_id,
                    inbound_message_id=wmid,
                    sales_gastro_hint=sales_gastro_hint,
                    sales_target_iiko_ids=sales_target_iiko_ids,
                    newly_stopped_names=fresh_stopped,
                    trace_id=trace_id,
                    conversation_id=conversation_id,
                    location_id=active_location_id,
                    draft_order=read_ctx.draft_row,
                    user=read_ctx.user,
                    org=read_ctx.org,
                    user_message=message_text,
                )
            if not db_state_applied:
                await db.commit()
                return
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
            db_state_applied = await update_user_session_fields_in_db(
                db,
                **db_state_kwargs,
                expected_current_state=state.value,
                expected_session_version=preflight_session_version,
            )

            if db_state_applied:
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
            if ai_provider_soft_fallback:
                assistant_meta["ai_provider_soft_fallback"] = True
            if is_openai_fallback_escalation_reply(result.reply_text):
                assistant_meta["technical_fallback"] = True
            if ai_snapshot_id:
                assistant_meta["snapshot_id"] = ai_snapshot_id
            outbound_id_chat = await _save_chat_log(
                db,
                phone,
                user_log_text,
                result.reply_text,
                assistant_meta,
                organization_id=organization_id,
                trace_id=trace_id,
                conversation_id=conversation_id,
                known_user_id=u_row.id if u_row is not None else None,
                location_id=active_location_id,
                save_user=False,
            )
            # Phase 5 OS: счётчик AI-ответов для event-driven аналитики
            _usage = getattr(ai_response, "_usage", None)
            await emit_event(
                db,
                BusinessEvent(
                    org_id=organization_id,
                    type="ai.response.generated",
                    actor="ai",
                    payload={
                        "intent": ai_response.intent,
                        "tokens": getattr(_usage, "total_tokens", None),
                    },
                ),
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
                await emit_event(
                    db,
                    BusinessEvent(
                        org_id=organization_id,
                        type="ai.escalated",
                        actor="ai",
                        entity_type="user",
                        entity_id=phone,
                        payload={
                            "phone": phone,
                            "reason": (result.reply_text or "")[:500],
                            "fsm_state": state.value,
                        },
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

        await save_seen_stopped_keys(redis_client, phone, organization_id, menu_items)

        schedule_log_message(organization_id, "outbound", "ai", "voice" if had_voice else "text")
        # G4: декремент счётчика перегруза — бот ответил, чат обработан
        short_before = await _is_org_overloaded(redis_client, organization_id, active_location_id)
        chat_cleared_slow = await _untrack_slow_chat(redis_client, organization_id, phone, active_location_id)
        short_after = await _is_org_overloaded(redis_client, organization_id, active_location_id)
        if chat_cleared_slow:
            await _publish_bot_sla_status(
                redis_client,
                organization_id,
                location_id=active_location_id,
                phone=phone,
                chat_slow=False,
            )
        if short_after != short_before:
            await _publish_bot_sla_status(redis_client, organization_id, location_id=active_location_id)

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
        await publish_chat_event(
            phone=phone,
            role="assistant",
            content=result.reply_text,
            organization_id=organization_id,
            chat_log_id=outbound_id_chat,
            trace_id=trace_id,
            conversation_id=conversation_id,
            intent=ai_response.intent,
            meta=assistant_meta,
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
            await publish_human_event(
                phone=phone,
                organization_id=organization_id,
                reason=ai_response.reply_text,
                user_message=user_log_text or "",
                trace_id=trace_id,
                conversation_id=conversation_id,
                intent=ai_response.intent,
            )
            await publish_state_event(
                phone=phone,
                state=UserState.HUMAN_MODE.value,
                organization_id=organization_id,
                trace_id=trace_id,
                conversation_id=conversation_id,
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
            "%sСообщение обработано: phone=%s, intent=%s, state=%s",
            trace_log_prefix(),
            phone, ai_response.intent, state.value,
        )
    except Exception:
        raise


async def process_voice_message(
    phone: str,
    media_id: str,
    *,
    whatsapp_message_id: str = "",
    webhook_value: dict[str, Any] | None = None,
    trace_id: str | None = None,
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
            trace_id=trace_id or build_trace_id(whatsapp_message_id),
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


async def _flush_twilio_voice_chunk(
    phone: str,
    call_sid: str,
    mulaw: bytes,
    *,
    org_id: int,
    mode: str = "stt_fallback",
) -> None:
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
    try:
        from app.db.session import async_session_factory
        from app.services.voice_ai import record_voice_call

        async with async_session_factory() as db:
            location_id = await _get_twilio_location_id(call_sid)
            await record_voice_call(
                db,
                org_id=int(org_id),
                call_sid=call_sid,
                phone=phone,
                status="transcribed",
                transcript=text,
                mode=mode,
                location_id=location_id,
            )
            await db.commit()
    except Exception:
        logger.debug("voice call transcript log skipped callSid=%s", call_sid)
    mid = f"twilio:{call_sid}:{uuid.uuid4().hex}"
    tok = twilio_call_context(call_sid)
    try:
        await process_message(
            phone,
            text,
            whatsapp_message_id=mid,
            organization_id=int(org_id),
        )
    finally:
        reset_twilio_call_context(tok)


async def _await_twilio_stream_start(
    websocket: WebSocket,
) -> tuple[str, str, int, str, str, str]:
    """Read Twilio WS until «start»; return call_sid, phone, org_id, voice_mode, org_name, stream_sid."""
    call_sid = ""
    phone = ""
    org_id = int(settings.default_organization_id)
    voice_mode = "stt_fallback"
    org_name = ""
    stream_sid = ""
    while not call_sid:
        try:
            raw = await websocket.receive_text()
        except WebSocketDisconnect:
            return "", "", org_id, voice_mode, org_name, stream_sid
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if data.get("event") == "connected":
            continue
        if data.get("event") != "start":
            continue
        st = data.get("start") or {}
        call_sid = (st.get("callSid") or data.get("callSid") or "").strip()
        stream_sid = (st.get("streamSid") or data.get("streamSid") or "").strip()
        phone = await _get_twilio_caller(call_sid) if call_sid else ""
        org_id = await _get_twilio_org_id(call_sid) if call_sid else org_id
        voice_mode = await _get_twilio_voice_mode(call_sid) if call_sid else voice_mode
        try:
            async with async_session_factory() as db:
                org = await db.get(Organization, org_id)
                org_name = (org.name if org else "") or ""
        except Exception:
            logger.debug("voice org name resolve failed org=%s callSid=%s", org_id, call_sid, exc_info=True)
    return call_sid, phone, org_id, voice_mode, org_name, stream_sid


async def _run_stt_fallback_voice_stream(
    websocket: WebSocket,
    *,
    call_sid: str,
    phone: str,
    org_id: int,
) -> None:
    """
    Twilio Media Streams (входящий μ-law 8 kHz).
    Накопление буфера → транскрипт Whisper → process_message → Twilio Say.
    """
    buf = bytearray()
    processing = False
    threshold = int(settings.twilio_voice_buffer_bytes)
    mode = "stt_fallback"

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
            await _flush_twilio_voice_chunk(phone, call_sid, chunk, org_id=org_id, mode=mode)
        finally:
            processing = False

    logger.info(
        "Twilio STT stream callSid=%s phone=%s org=%s",
        (call_sid[:16] + "…") if len(call_sid) > 16 else call_sid,
        phone,
        org_id,
    )

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
                continue
            if ev == "media":
                media = data.get("media") or {}
                b64 = media.get("payload") or ""
                if b64:
                    try:
                        buf.extend(base64.b64decode(b64))
                    except Exception:
                        logger.debug("voice media payload decode failed callSid=%s", call_sid, exc_info=True)
                await maybe_flush(force=False)
                continue
            if ev == "stop":
                await maybe_flush(force=True)
                break
    finally:
        if phone and call_sid and buf and not processing:
            await _flush_twilio_voice_chunk(phone, call_sid, bytes(buf), org_id=org_id, mode=mode)


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
    to_phone = (params.get("To") or "").strip()
    voice_mode = "stt_fallback"
    org_id = int(settings.default_organization_id)
    org_name = ""

    try:
        from app.db.session import async_session_factory
        from app.services.twilio_routing import resolve_location_from_twilio_number, resolve_org_from_twilio_number
        from app.services.voice_ai import get_voice_mode, org_voice_enabled, record_voice_call

        async with async_session_factory() as db:
            org, org_id = await resolve_org_from_twilio_number(db, to_phone)
            org_name = (org.name if org else "") or ""
            voice_mode = get_voice_mode(org)
            location_id = await resolve_location_from_twilio_number(db, org_id, to_phone)
            if not org_voice_enabled(org):
                return Response(
                    content="""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say language="ru-RU">Голосовой AI сейчас выключен. Напишите нам в WhatsApp.</Say>
    <Hangup/>
</Response>""".strip(),
                    media_type="application/xml",
                )
            if call_sid and from_phone:
                await _store_twilio_caller(
                    call_sid,
                    from_phone,
                    org_id,
                    voice_mode=voice_mode,
                    location_id=location_id,
                )
            if call_sid:
                await record_voice_call(
                    db,
                    org_id=org_id,
                    call_sid=call_sid,
                    phone=from_phone,
                    status="started",
                    mode=voice_mode,
                    location_id=location_id,
                )
                await db.commit()
    except Exception:
        logger.exception("Voice AI feature flag check failed")
        return Response(content="Voice AI unavailable", status_code=503)

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
    Twilio Media Streams: realtime (OpenAI WSS) or stt_fallback (Whisper buffer).
    """
    await websocket.accept()
    call_sid, phone, org_id, voice_mode, org_name, stream_sid = await _await_twilio_stream_start(websocket)
    if not call_sid:
        return

    logger.info(
        "Twilio stream dispatch callSid=%s mode=%s org=%s",
        (call_sid[:16] + "…") if len(call_sid) > 16 else call_sid,
        voice_mode,
        org_id,
    )

    if voice_mode == "realtime":
        from app.services.voice_realtime import run_realtime_voice_bridge

        await run_realtime_voice_bridge(
            websocket,
            org_id=org_id,
            phone=phone,
            call_sid=call_sid,
            org_name=org_name,
            stream_sid=stream_sid,
        )
        return

    await _run_stt_fallback_voice_stream(
        websocket,
        call_sid=call_sid,
        phone=phone,
        org_id=org_id,
    )


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
            phone_raw = (msg.get("from") or "").strip()
            phone = _canonical_whatsapp_phone(phone_raw)
            msg_type = (msg.get("type") or "").strip().lower()
            message_id = (msg.get("id") or "").strip()

            if not phone:
                continue

            logger.debug("[WA webhook] raw from=%r canonical=%r", phone_raw, phone)

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
                    inbound_trace_id = build_trace_id(message_id)
                    from app.services.task_queue import dispatch_arq_or_background

                    await dispatch_arq_or_background(
                        "whatsapp_process_voice",
                        background_tasks,
                        phone=phone,
                        media_id=media_id,
                        whatsapp_message_id=message_id,
                        webhook_value=value,
                        trace_id=inbound_trace_id,
                    )
                    schedule_log_message(org_id, "inbound", "user", "voice")
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
                        spawn_tracked(
                            save_customer_feedback(org_id=_org_id, phone=phone, rating=_rating),
                            name=f"review_feedback_{_org_id}_{_rating}",
                            log=logger,
                        )
                        if btn_id == "review_pos":
                            spawn_tracked(
                                send_review_positive_reply(phone, _org_id),
                                name=f"review_positive_reply_{_org_id}",
                                log=logger,
                            )
                        else:
                            spawn_tracked(
                                send_review_negative_alert(phone, _org_id),
                                name=f"review_negative_alert_{_org_id}",
                                log=logger,
                            )
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
                    inbound_trace_id = build_trace_id(message_id)
                    from app.services.task_queue import dispatch_arq_or_background

                    await dispatch_arq_or_background(
                        "whatsapp_process_text",
                        background_tasks,
                        phone=phone,
                        message_text=message_text,
                        whatsapp_message_id=message_id,
                        webhook_value=value,
                        trace_id=inbound_trace_id,
                    )
                    schedule_log_message(org_id, "inbound", "user", "interactive")
                    logger.info(
                        "[trace_id=%s] Interactive (%s) от %s → '%s' поставлено в очередь",
                        inbound_trace_id,
                        interactive_type, phone, message_text,
                    )
            else:
                message_text = (msg.get("text") or {}).get("body") or ""
                if message_text:
                    inbound_trace_id = build_trace_id(message_id)
                    from app.services.task_queue import dispatch_arq_or_background

                    await dispatch_arq_or_background(
                        "whatsapp_process_text",
                        background_tasks,
                        phone=phone,
                        message_text=message_text,
                        whatsapp_message_id=message_id,
                        webhook_value=value,
                        trace_id=inbound_trace_id,
                    )
                    schedule_log_message(org_id, "inbound", "user", "text")
                    logger.info(
                        "[trace_id=%s] Сообщение от %s поставлено в очередь обработки",
                        inbound_trace_id,
                        phone,
                    )

    except (IndexError, KeyError, TypeError) as exc:
        logger.error("Ошибка парсинга вебхука: %s", exc)

    return {"status": "ok"}
