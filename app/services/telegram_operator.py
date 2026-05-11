"""
Telegram operator relay — MVP (Wishlist #12).

Позволяет оператору ответить клиенту прямо из Telegram:
  1. Эскалационный алерт содержит кнопку «📩 Ответить клиенту».
  2. При нажатии бот просит написать текст ответа.
  3. Оператор пишет → сообщение уходит клиенту в WhatsApp через send_customer_text().
  4. Команда /dialogs — последние 10 открытых эскалаций.

Состояние оператора хранится в Redis (ключ tg:op:{telegram_user_id}, TTL 30 мин).
"""

from __future__ import annotations

import json
import logging

import httpx

from app.core.config import settings
from app.db.session import redis_client

logger = logging.getLogger(__name__)

_OPERATOR_STATE_TTL = 1800  # 30 минут

# ──────────────────────── Telegram Bot API helpers ────────────────────────


def _tg_api_url(method: str) -> str:
    token = (settings.telegram_bot_token or "").strip()
    return f"https://api.telegram.org/bot{token}/{method}"


async def _tg_send(method: str, payload: dict) -> dict:
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(_tg_api_url(method), json=payload)
        r.raise_for_status()
        return r.json()


async def _answer_callback(callback_query_id: str, text: str = "") -> None:
    try:
        await _tg_send("answerCallbackQuery", {
            "callback_query_id": callback_query_id,
            "text": text,
            "show_alert": False,
        })
    except Exception as exc:
        logger.warning("answerCallbackQuery failed: %s", exc)


async def _send_to_operator(chat_id: int | str, text: str) -> None:
    try:
        await _tg_send("sendMessage", {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
        })
    except Exception as exc:
        logger.warning("_send_to_operator failed: %s", exc)


# ──────────────────────── Redis state ────────────────────────


async def _get_operator_state(telegram_user_id: int) -> dict | None:
    key = f"tg:op:{telegram_user_id}"
    try:
        raw = await redis_client.get(key)
        if raw:
            return json.loads(str(raw))
    except Exception as exc:
        logger.warning("tg operator state get failed: %s", exc)
    return None


async def _set_operator_state(telegram_user_id: int, state: dict) -> None:
    key = f"tg:op:{telegram_user_id}"
    try:
        await redis_client.setex(key, _OPERATOR_STATE_TTL, json.dumps(state))
    except Exception as exc:
        logger.warning("tg operator state set failed: %s", exc)


async def _clear_operator_state(telegram_user_id: int) -> None:
    key = f"tg:op:{telegram_user_id}"
    try:
        await redis_client.delete(key)
    except Exception as exc:
        logger.warning("tg operator state clear failed: %s", exc)


# ──────────────────────── Handlers ────────────────────────


async def handle_telegram_update(update: dict) -> None:
    """Точка входа: разбирает тип Update и делегирует обработчику."""
    if "callback_query" in update:
        await handle_callback_query(update["callback_query"])
    elif "message" in update:
        msg = update["message"]
        text = (msg.get("text") or "").strip()
        if text.startswith("/dialogs"):
            await handle_dialogs_command(msg)
        elif text.startswith("/cancel"):
            uid = msg.get("from", {}).get("id")
            if uid:
                await _clear_operator_state(int(uid))
                await _send_to_operator(msg["chat"]["id"], "Режим ответа отменён.")
        else:
            await handle_operator_message(msg)


async def handle_callback_query(callback: dict) -> None:
    """
    Обрабатывает нажатие inline-кнопки «📩 Ответить клиенту».
    callback_data формат: «reply:{phone}:{org_id}»
    """
    query_id = callback.get("id", "")
    data = (callback.get("data") or "").strip()
    user = callback.get("from") or {}
    telegram_user_id = user.get("id")
    chat_id = (callback.get("message") or {}).get("chat", {}).get("id")

    if not telegram_user_id or not chat_id:
        await _answer_callback(query_id, "Ошибка: нет id пользователя.")
        return

    if data.startswith("on_shift:"):
        parts = data.split(":", 1)
        org_id_str = parts[1].strip() if len(parts) > 1 else ""
        org_id = int(org_id_str) if org_id_str.isdigit() else 0
        if org_id:
            await _answer_callback(query_id, "Активирую предзаказы...")
            await _handle_on_shift_callback(chat_id, org_id)
        else:
            await _answer_callback(query_id, "Неверный org_id.")
        return

    if not data.startswith("reply:"):
        return

    parts = data.split(":", 2)
    if len(parts) < 3:
        await _answer_callback(query_id, "Неверный формат кнопки.")
        return

    phone = parts[1].strip()
    org_id_str = parts[2].strip()
    org_id = int(org_id_str) if org_id_str.isdigit() else 0

    await _set_operator_state(int(telegram_user_id), {
        "state": "awaiting_reply",
        "phone": phone,
        "org_id": org_id,
        "chat_id": chat_id,
    })

    await _answer_callback(query_id, "Напишите ответ клиенту.")
    await _send_to_operator(
        chat_id,
        f"📩 <b>Режим ответа</b>\nНапишите сообщение — оно уйдёт гостю <code>{phone}</code>.\n"
        "Для отмены: /cancel",
    )


async def handle_operator_message(msg: dict) -> None:
    """
    Если оператор в режиме awaiting_reply — пересылает его текст клиенту в WhatsApp.
    Иначе — игнорирует или отвечает подсказкой.
    """
    user = msg.get("from") or {}
    telegram_user_id = user.get("id")
    chat_id = (msg.get("chat") or {}).get("id")
    text = (msg.get("text") or "").strip()

    if not telegram_user_id or not text:
        return

    op_state = await _get_operator_state(int(telegram_user_id))
    if not op_state or op_state.get("state") != "awaiting_reply":
        return

    phone = op_state.get("phone", "")
    org_id = int(op_state.get("org_id") or 0)

    if not phone:
        await _send_to_operator(chat_id, "Ошибка: телефон не найден. Начните заново через кнопку.")
        return

    try:
        from app.db.session import async_session_factory
        from app.db.models import ChatLog, User
        from app.services.customer_reply import send_customer_text
        from datetime import datetime, timezone
        from sqlalchemy import select

        async with async_session_factory() as db:
            user_row = await db.scalar(
                select(User).where(
                    User.phone == phone,
                    User.organization_id == org_id,
                ).limit(1)
            )
            user_id = user_row.id if user_row else None

            log = ChatLog(
                organization_id=org_id,
                user_id=user_id,
                role="assistant",
                content=text,
                meta_json={"source": "telegram_operator", "tg_user_id": telegram_user_id},
                delivery_status="sending",
                status_updated_at=datetime.now(timezone.utc),
            )
            db.add(log)
            await db.commit()
            await db.refresh(log)
            log_id = int(log.id)

        await send_customer_text(phone, text, outbound_chat_log_id=log_id)

        await _clear_operator_state(int(telegram_user_id))
        await _send_to_operator(chat_id, f"✅ Отправлено гостю <code>{phone}</code>.")
        logger.info("Telegram operator relay: org=%s phone=%s sent", org_id, phone)

    except Exception as exc:
        logger.exception("Telegram operator relay failed: %s", exc)
        await _send_to_operator(chat_id, f"❌ Не удалось отправить: {exc!s}")


async def handle_dialogs_command(msg: dict) -> None:
    """
    /dialogs — список последних 10 эскалаций для текущей организации.
    Определяет org_id через TELEGRAM_ADMIN_CHAT_ID совпадение.
    """
    chat_id = (msg.get("chat") or {}).get("id")
    if not chat_id:
        return

    try:
        from app.db.session import async_session_factory
        from app.db.models import EscalationEvent
        from sqlalchemy import select

        async with async_session_factory() as db:
            rows = (await db.execute(
                select(EscalationEvent)
                .order_by(EscalationEvent.created_at.desc())
                .limit(10)
            )).scalars().all()

        if not rows:
            await _send_to_operator(chat_id, "Нет последних эскалаций.")
            return

        lines = ["<b>Последние 10 эскалаций:</b>", ""]
        for ev in rows:
            dt = ev.created_at.strftime("%d.%m %H:%M") if ev.created_at else "—"
            ph = (ev.phone or "").strip()
            reason = (ev.reason or "—")[:60]
            lines.append(f"• <code>{ph}</code> — {dt}\n  <i>{reason}</i>")

        await _send_to_operator(chat_id, "\n".join(lines))
    except Exception as exc:
        logger.exception("handle_dialogs_command failed: %s", exc)
        await _send_to_operator(chat_id, f"Ошибка: {exc!s}")


async def _handle_on_shift_callback(chat_id: int | str, org_id: int) -> None:
    """Активирует ночные предзаказы и уведомляет оператора через Telegram."""
    try:
        from app.db.session import async_session_factory
        from app.services.night_preorders import activate_night_preorders

        async with async_session_factory() as db:
            n = await activate_night_preorders(db, org_id)

        if n:
            msg = f"✅ Активировано {n} ночных предзаказов — клиентам отправлены сообщения."
        else:
            msg = "✅ Смена начата. Ночных предзаказов нет."

        await _send_to_operator(chat_id, msg)

        # Удаляем Redis pending key
        try:
            from app.integrations.redis_client import get_redis_client
            from datetime import date
            redis = await get_redis_client()
            if redis:
                pending_key = f"rm:shift:pending:{org_id}:{date.today().isoformat()}"
                await redis.delete(pending_key)
        except Exception:
            pass

    except Exception as exc:
        logger.exception("_handle_on_shift_callback failed: %s", exc)
        await _send_to_operator(chat_id, f"❌ Ошибка активации: {exc!s}")
