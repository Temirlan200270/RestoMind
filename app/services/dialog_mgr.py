"""
Менеджер диалогов — управляет памятью разговора и состоянием пользователя через Redis.
Каждый диалог привязан к номеру телефона и живёт 24 часа.
"""

import json
import logging
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)

MAX_HISTORY_LENGTH = 20
HISTORY_TTL = 86400
STATE_TTL = 86400

CONFIRM_WORDS = frozenset({
    "да", "yes", "ок", "подтверждаю", "верно", "давай", "aga", "конечно",
})
CANCEL_WORDS = frozenset({
    "нет", "no", "отмена", "отменить", "не надо", "cancel", "стоп",
})


class UserState(StrEnum):
    """Состояния пользователя в диалоге."""

    CHATTING = "chatting"
    # Черновик заказа создан; ждём явный выбор оплаты — затем CONFIRMING_ORDER
    AWAITING_ORDER_PAYMENT = "awaiting_order_payment"
    CONFIRMING_ORDER = "confirming_order"
    CONFIRMING_BOOKING = "confirming_booking"
    HUMAN_MODE = "human_mode"


def _history_key(phone: str) -> str:
    """Redis-ключ для истории диалога конкретного пользователя."""
    return f"chat:history:{phone}"


def _state_key(phone: str) -> str:
    return f"user:state:{phone}"


def _pending_order_key(phone: str) -> str:
    return f"user:pending_order:{phone}"


def _pending_booking_key(phone: str) -> str:
    return f"user:pending_booking:{phone}"


# ─── Управление состоянием пользователя ──────────────────

async def get_user_state(redis: Any, phone: str) -> UserState:
    """Получить текущее состояние пользователя. По умолчанию — CHATTING."""
    raw = await redis.get(_state_key(phone))
    if raw is None:
        return UserState.CHATTING
    try:
        return UserState(raw)
    except ValueError:
        return UserState.CHATTING


async def set_user_state(redis: Any, phone: str, state: UserState) -> None:
    """Установить состояние пользователя с TTL 24 часа."""
    await redis.set(_state_key(phone), state.value, ex=STATE_TTL)
    logger.info("Состояние %s → %s", phone, state.value)


async def set_pending_order(redis: Any, phone: str, order_id: int) -> None:
    """Сохранить ID заказа, ожидающего подтверждения."""
    await redis.set(_pending_order_key(phone), str(order_id), ex=STATE_TTL)


async def get_pending_order(redis: Any, phone: str) -> int | None:
    """Получить ID заказа, ожидающего подтверждения."""
    raw = await redis.get(_pending_order_key(phone))
    return int(raw) if raw else None


async def clear_pending_order(redis: Any, phone: str) -> None:
    """Очистить ожидающий заказ и вернуть состояние в CHATTING."""
    await redis.delete(_pending_order_key(phone))
    await set_user_state(redis, phone, UserState.CHATTING)


async def set_pending_booking(redis: Any, phone: str, booking_id: int) -> None:
    """Сохранить ID бронирования, ожидающего подтверждения."""
    await redis.set(_pending_booking_key(phone), str(booking_id), ex=STATE_TTL)


async def get_pending_booking(redis: Any, phone: str) -> int | None:
    """Получить ID бронирования, ожидающего подтверждения."""
    raw = await redis.get(_pending_booking_key(phone))
    return int(raw) if raw else None


async def clear_pending_booking(redis: Any, phone: str) -> None:
    """Очистить ожидающее бронирование и вернуть состояние в CHATTING."""
    await redis.delete(_pending_booking_key(phone))
    await set_user_state(redis, phone, UserState.CHATTING)


async def get_chat_history(redis: Any, phone: str) -> list[dict[str, str]]:
    """
    Получить историю последних сообщений диалога.

    Returns:
        Список словарей [{role: "user"/"assistant", content: "..."}],
        отсортированный хронологически (старые → новые).
    """
    key = _history_key(phone)
    raw_messages = await redis.lrange(key, 0, -1)

    history = []
    for raw in raw_messages:
        try:
            history.append(json.loads(raw))
        except json.JSONDecodeError:
            logger.warning("Повреждённое сообщение в Redis для %s: %s", phone, raw)

    logger.debug("Загружена история для %s: %d сообщений", phone, len(history))
    return history


async def append_to_history(
    redis: Any,
    phone: str,
    role: str,
    text: str,
) -> None:
    """
    Добавить сообщение в историю диалога.
    Автоматически обрезает старые сообщения сверх лимита и продлевает TTL.

    Args:
        redis: Асинхронный клиент Redis.
        phone: Номер телефона клиента.
        role: Роль отправителя — "user" или "assistant".
        text: Текст сообщения.
    """
    key = _history_key(phone)
    message = json.dumps({"role": role, "content": text}, ensure_ascii=False)

    pipe = redis.pipeline()
    pipe.rpush(key, message)
    # Оставляем только последние N сообщений
    pipe.ltrim(key, -MAX_HISTORY_LENGTH, -1)
    # Продлеваем TTL при каждом сообщении
    pipe.expire(key, HISTORY_TTL)
    await pipe.execute()

    logger.debug("Добавлено сообщение [%s] для %s", role, phone)


async def clear_history(redis: Any, phone: str) -> None:
    """Полностью очистить историю диалога (например, после завершения заказа)."""
    key = _history_key(phone)
    await redis.delete(key)
    logger.info("История очищена для %s", phone)


async def purge_all_session_keys_for_phone(redis: Any, phone: str) -> None:
    """
    Удалить из Redis/памяти все ключи сессии по номеру (история, state, pending).
    Вызывать при удалении пользователя из БД (например, демо-данные).
    """
    for key_fn in (_history_key, _state_key, _pending_order_key, _pending_booking_key):
        await redis.delete(key_fn(phone))
    logger.debug("Redis-сессия сброшена для %s", phone)
