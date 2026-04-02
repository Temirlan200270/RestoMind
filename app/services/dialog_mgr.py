"""
Менеджер диалогов — управляет памятью разговора и состоянием пользователя через Redis.
Каждый диалог привязан к номеру телефона и живёт 24 часа.

State Recovery: при каждом изменении состояния дублируем его в User (БД).
Если Redis потерял ключ (eviction / перезапуск), восстанавливаем из БД.
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
    AWAITING_ORDER_PAYMENT = "awaiting_order_payment"
    CONFIRMING_ORDER = "confirming_order"
    CONFIRMING_BOOKING = "confirming_booking"
    HUMAN_MODE = "human_mode"


def _history_key(phone: str) -> str:
    return f"chat:history:{phone}"


def _state_key(phone: str) -> str:
    return f"user:state:{phone}"


def _pending_order_key(phone: str) -> str:
    return f"user:pending_order:{phone}"


def _pending_booking_key(phone: str) -> str:
    return f"user:pending_booking:{phone}"


# ─── DB sync (best-effort) ────────────────────────────────

async def _db_sync(phone: str, **fields: Any) -> None:
    """Best-effort: зеркалируем сессионные поля в User для восстановления после потери Redis."""
    if not fields:
        return
    try:
        from sqlalchemy import update as sa_update

        from app.db.models import User
        from app.db.session import async_session_factory

        async with async_session_factory() as db:
            await db.execute(sa_update(User).where(User.phone == phone).values(**fields))
            await db.commit()
    except Exception as exc:
        logger.warning("DB session sync for %s: %s", phone, exc)


async def _db_recover_session(phone: str, redis: Any) -> UserState:
    """
    Полное восстановление сессии из БД при Redis miss.
    Возвращает UserState и восстанавливает все ключи Redis.
    """
    try:
        from sqlalchemy import select as sa_select

        from app.db.models import User
        from app.db.session import async_session_factory

        async with async_session_factory() as db:
            user = await db.scalar(sa_select(User).where(User.phone == phone))
            if not user:
                return UserState.CHATTING

            state_raw = getattr(user, "current_state", None) or "chatting"
            try:
                state = UserState(state_raw)
            except ValueError:
                state = UserState.CHATTING

            await redis.set(_state_key(phone), state.value, ex=STATE_TTL)

            pending_oid: int | None = getattr(user, "current_pending_order_id", None)
            if pending_oid is not None:
                await redis.set(_pending_order_key(phone), str(pending_oid), ex=STATE_TTL)

            pending_bid: int | None = getattr(user, "current_pending_booking_id", None)
            if pending_bid is not None:
                await redis.set(_pending_booking_key(phone), str(pending_bid), ex=STATE_TTL)

            if state != UserState.CHATTING or pending_oid or pending_bid:
                logger.info(
                    "Session recovered from DB: %s state=%s order=%s booking=%s",
                    phone, state.value, pending_oid, pending_bid,
                )
            return state
    except Exception as exc:
        logger.warning("DB session recovery for %s: %s", phone, exc)
        return UserState.CHATTING


async def _db_recover_pending_order(phone: str) -> int | None:
    """Точечное восстановление pending_order_id из БД."""
    try:
        from sqlalchemy import select as sa_select

        from app.db.models import User
        from app.db.session import async_session_factory

        async with async_session_factory() as db:
            user = await db.scalar(sa_select(User).where(User.phone == phone))
            return getattr(user, "current_pending_order_id", None) if user else None
    except Exception:
        return None


async def _db_recover_pending_booking(phone: str) -> int | None:
    """Точечное восстановление pending_booking_id из БД."""
    try:
        from sqlalchemy import select as sa_select

        from app.db.models import User
        from app.db.session import async_session_factory

        async with async_session_factory() as db:
            user = await db.scalar(sa_select(User).where(User.phone == phone))
            return getattr(user, "current_pending_booking_id", None) if user else None
    except Exception:
        return None


# ─── Управление состоянием пользователя ──────────────────

async def get_user_state(redis: Any, phone: str) -> UserState:
    """Получить состояние. При Redis miss — восстановление из БД."""
    raw = await redis.get(_state_key(phone))
    if raw is not None:
        try:
            return UserState(raw)
        except ValueError:
            return UserState.CHATTING
    return await _db_recover_session(phone, redis)


async def set_user_state(redis: Any, phone: str, state: UserState) -> None:
    """Установить состояние с TTL 24 ч + зеркало в БД."""
    await redis.set(_state_key(phone), state.value, ex=STATE_TTL)
    logger.info("Состояние %s → %s", phone, state.value)
    await _db_sync(phone, current_state=state.value)


async def set_pending_order(redis: Any, phone: str, order_id: int) -> None:
    """Сохранить ID заказа, ожидающего подтверждения."""
    await redis.set(_pending_order_key(phone), str(order_id), ex=STATE_TTL)
    await _db_sync(phone, current_pending_order_id=order_id)


async def get_pending_order(redis: Any, phone: str) -> int | None:
    """Получить ID pending-заказа. При Redis miss — из БД."""
    raw = await redis.get(_pending_order_key(phone))
    if raw:
        return int(raw)
    val = await _db_recover_pending_order(phone)
    if val is not None:
        await redis.set(_pending_order_key(phone), str(val), ex=STATE_TTL)
    return val


async def clear_pending_order(redis: Any, phone: str) -> None:
    """Очистить ожидающий заказ и вернуть состояние в CHATTING."""
    await redis.delete(_pending_order_key(phone))
    await set_user_state(redis, phone, UserState.CHATTING)
    await _db_sync(phone, current_pending_order_id=None)


async def set_pending_booking(redis: Any, phone: str, booking_id: int) -> None:
    """Сохранить ID бронирования, ожидающего подтверждения."""
    await redis.set(_pending_booking_key(phone), str(booking_id), ex=STATE_TTL)
    await _db_sync(phone, current_pending_booking_id=booking_id)


async def get_pending_booking(redis: Any, phone: str) -> int | None:
    """Получить ID pending-бронирования. При Redis miss — из БД."""
    raw = await redis.get(_pending_booking_key(phone))
    if raw:
        return int(raw)
    val = await _db_recover_pending_booking(phone)
    if val is not None:
        await redis.set(_pending_booking_key(phone), str(val), ex=STATE_TTL)
    return val


async def clear_pending_booking(redis: Any, phone: str) -> None:
    """Очистить ожидающее бронирование и вернуть состояние в CHATTING."""
    await redis.delete(_pending_booking_key(phone))
    await set_user_state(redis, phone, UserState.CHATTING)
    await _db_sync(phone, current_pending_booking_id=None)


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
