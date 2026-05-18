"""
Менеджер диалогов — управляет памятью разговора и состоянием пользователя через Redis.
Ключи изолированы по organization_id + phone (мультитенантность).

State Recovery: при каждом изменении состояния дублируем его в User (БД).
Если Redis потерял ключ (eviction / перезапуск), восстанавливаем из БД.
"""

import json
import logging
from enum import StrEnum
from typing import Any

from app.services.conversation_state import (
    ConversationState,
    check_conversation_transition,
    normalize_conversation_state,
)

_NOCHANGE = object()

logger = logging.getLogger(__name__)

MAX_HISTORY_LENGTH = 20
HISTORY_TTL = 86400
STATE_TTL = 86400

CONFIRM_WORDS = frozenset({
    "да", "yes", "ок", "подтверждаю", "верно", "давай", "aga", "конечно",
})
CANCEL_WORDS = frozenset({
    "нет", "no", "отмена", "отменить", "отмени", "отмените",
    "не надо", "cancel", "стоп", "сбрось", "сбросить",
})

# Фразы полной отмены корзины / всех черновиков (CHATTING, до LLM)
CANCEL_ALL_PHRASES = frozenset({
    "отмени всё", "отмени все", "отмените всё", "отмените все",
    "отмени заказ", "отменить заказ", "отмените заказ",
    "сбрось заказ", "сбросить заказ", "очисти корзину", "очистить корзину",
    "удали заказ", "удалить заказ", "убери всё", "убери все",
    "начать заново", "с нуля",
})


class UserState(StrEnum):
    """Состояния пользователя в диалоге."""

    CHATTING = "chatting"
    AWAITING_ORDER_PAYMENT = "awaiting_order_payment"
    CONFIRMING_ORDER = "confirming_order"
    CONFIRMING_BOOKING = "confirming_booking"
    HUMAN_MODE = "human_mode"


def _effective_org(organization_id: int | None) -> int:
    """Если org не передан — default из настроек (тесты / legacy)."""
    if organization_id is not None:
        return int(organization_id)
    from app.core.config import settings

    return int(settings.default_organization_id)


def _scoped(organization_id: int | None, phone: str) -> str:
    return f"{_effective_org(organization_id)}:{phone}"


def _history_key(organization_id: int | None, phone: str) -> str:
    return f"chat:history:{_scoped(organization_id, phone)}"


def _state_key(organization_id: int | None, phone: str) -> str:
    return f"user:state:{_scoped(organization_id, phone)}"


def _legacy_state_key(phone: str) -> str:
    """До мультитенанта: только phone."""
    return f"user:state:{phone}"


def _pending_order_key(organization_id: int | None, phone: str) -> str:
    return f"user:pending_order:{_scoped(organization_id, phone)}"


def _pending_booking_key(organization_id: int | None, phone: str) -> str:
    return f"user:pending_booking:{_scoped(organization_id, phone)}"


# ─── Durable DB updates (must be called in caller txn) ─────

async def update_user_session_fields_in_db(
    db: Any,
    *,
    phone: str,
    organization_id: int | None,
    current_state: str | None = None,
    current_pending_order_id: int | None | object = _NOCHANGE,
    current_pending_booking_id: int | None | object = _NOCHANGE,
    transition_source: str = "session_update",
    transition_reason: str | None = None,
    transition_context: dict[str, Any] | None = None,
) -> None:
    """
    Обновляет durable-поля сессии в таблице User *в рамках текущей DB-транзакции*.
    Никаких отдельный сессий/commit здесь быть не должно: иначе возможен рассинхрон Redis↔БД при падении.
    """
    from sqlalchemy import select as sa_select
    from sqlalchemy import update as sa_update

    from app.db.models import User
    from app.services.system_events import emit_system_event

    org = _effective_org(organization_id)
    patch: dict[str, Any] = {}
    prev_state_raw: str | None = None
    user_id: int | None = None
    if current_state is not None:
        row = await db.execute(
            sa_select(User.id, User.current_state)
            .where(User.phone == phone, User.organization_id == org)
            .limit(1),
        )
        existing = row.first()
        if existing is not None:
            user_id = int(existing[0])
            prev_state_raw = str(existing[1] or "")
        patch["current_state"] = normalize_conversation_state(current_state).value
    if current_pending_order_id is not _NOCHANGE:
        patch["current_pending_order_id"] = current_pending_order_id
    if current_pending_booking_id is not _NOCHANGE:
        patch["current_pending_booking_id"] = current_pending_booking_id
    if not patch:
        return
    await db.execute(
        sa_update(User)
        .where(User.phone == phone, User.organization_id == org)
        .values(**patch),
    )
    if current_state is not None:
        prev_state = normalize_conversation_state(prev_state_raw)
        next_state = normalize_conversation_state(current_state)
        if prev_state != next_state:
            await emit_system_event(
                db,
                organization_id=org,
                event_type="conversation_state_changed",
                source=transition_source,
                entity_type="user",
                entity_id=user_id or phone,
                payload={
                    "phone": phone,
                    "from_state": prev_state.value,
                    "to_state": next_state.value,
                    "reason": (transition_reason or "").strip() or None,
                    "context": transition_context or {},
                },
            )


async def set_user_state_durable(
    redis: Any,
    *,
    phone: str,
    organization_id: int,
    new_state: UserState,
    source: str = "session_update",
    reason: str | None = None,
    context: dict[str, Any] | None = None,
) -> None:
    """Persist and validate a state transition before updating Redis."""
    current_state = await get_user_state(redis, phone, organization_id=organization_id)
    check = check_conversation_transition(current_state.value, new_state.value)
    if not check.allowed:
        raise ValueError(check.reason)
    from app.db.session import async_session_factory

    async with async_session_factory() as db:
        await update_user_session_fields_in_db(
            db,
            phone=phone,
            organization_id=organization_id,
            current_state=check.to_state.value,
            transition_source=source,
            transition_reason=reason,
            transition_context=context,
        )
        await db.commit()
    await set_user_state(redis, phone, new_state, organization_id=organization_id)


async def sync_user_dialog_state_to_db_then_redis(
    redis: Any,
    *,
    phone: str,
    organization_id: int,
    new_state: UserState,
    source: str = "session_update",
    reason: str | None = None,
    context: dict[str, Any] | None = None,
) -> None:
    """
    Durable state first: одна короткая транзакция User.current_state → commit → кэш в Redis.
    Паттерн как в process_message после route_intent (без открытой LLM-транзакции).
    """
    await set_user_state_durable(
        redis,
        phone=phone,
        organization_id=organization_id,
        new_state=new_state,
        source=source,
        reason=reason,
        context=context,
    )


async def _db_recover_session(phone: str, redis: Any, organization_id: int | None) -> UserState:
    """
    Полное восстановление сессии из БД при Redis miss.
    Возвращает UserState и восстанавливает все ключи Redis.
    """
    try:
        from sqlalchemy import select as sa_select

        from app.db.models import User
        from app.db.session import async_session_factory

        org = _effective_org(organization_id)
        async with async_session_factory() as db:
            user = await db.scalar(
                sa_select(User).where(User.phone == phone, User.organization_id == org),
            )
            if not user:
                return UserState.CHATTING

            state_raw = getattr(user, "current_state", None) or "chatting"
            state = UserState(normalize_conversation_state(state_raw).value)

            await redis.set(_state_key(org, phone), state.value, ex=STATE_TTL)

            pending_oid: int | None = getattr(user, "current_pending_order_id", None)
            if pending_oid is not None:
                await redis.set(_pending_order_key(org, phone), str(pending_oid), ex=STATE_TTL)

            pending_bid: int | None = getattr(user, "current_pending_booking_id", None)
            if pending_bid is not None:
                await redis.set(_pending_booking_key(org, phone), str(pending_bid), ex=STATE_TTL)

            if state != UserState.CHATTING or pending_oid or pending_bid:
                logger.info(
                    "Session recovered from DB: org=%s %s state=%s order=%s booking=%s",
                    org, phone, state.value, pending_oid, pending_bid,
                )
            return state
    except Exception as exc:
        logger.warning("DB session recovery for %s: %s", phone, exc)
        return UserState.CHATTING


async def _db_recover_pending_order(phone: str, organization_id: int | None) -> int | None:
    """Точечное восстановление pending_order_id из БД."""
    try:
        from sqlalchemy import select as sa_select

        from app.db.models import User
        from app.db.session import async_session_factory

        org = _effective_org(organization_id)
        async with async_session_factory() as db:
            user = await db.scalar(
                sa_select(User).where(User.phone == phone, User.organization_id == org),
            )
            return getattr(user, "current_pending_order_id", None) if user else None
    except Exception:
        return None


async def _db_recover_pending_booking(phone: str, organization_id: int | None) -> int | None:
    """Точечное восстановление pending_booking_id из БД."""
    try:
        from sqlalchemy import select as sa_select

        from app.db.models import User
        from app.db.session import async_session_factory

        org = _effective_org(organization_id)
        async with async_session_factory() as db:
            user = await db.scalar(
                sa_select(User).where(User.phone == phone, User.organization_id == org),
            )
            return getattr(user, "current_pending_booking_id", None) if user else None
    except Exception:
        return None


# ─── Управление состоянием пользователя ──────────────────

async def get_user_state(redis: Any, phone: str, organization_id: int | None = None) -> UserState:
    """Получить состояние. При Redis miss — восстановление из БД."""
    org = _effective_org(organization_id)
    key = _state_key(org, phone)
    raw = await redis.get(key)
    if raw is None:
        raw_old = await redis.get(_legacy_state_key(phone))
        if raw_old is not None:
            await redis.set(key, raw_old, ex=STATE_TTL)
            await redis.delete(_legacy_state_key(phone))
            raw = raw_old
    if raw is not None:
        try:
            return UserState(normalize_conversation_state(raw).value)
        except ValueError:
            return UserState.CHATTING
    return await _db_recover_session(phone, redis, organization_id)


async def set_user_state(
    redis: Any, phone: str, state: UserState, organization_id: int | None = None,
) -> None:
    """Установить состояние в Redis (cache only) с TTL 24 ч."""
    org = _effective_org(organization_id)
    await redis.set(_state_key(org, phone), state.value, ex=STATE_TTL)
    await redis.delete(_legacy_state_key(phone))
    logger.info("Состояние org=%s %s → %s", org, phone, state.value)


async def set_pending_order(
    redis: Any, phone: str, order_id: int, organization_id: int | None = None,
) -> None:
    """Сохранить ID заказа, ожидающего подтверждения."""
    org = _effective_org(organization_id)
    await redis.set(_pending_order_key(org, phone), str(order_id), ex=STATE_TTL)


async def get_pending_order(redis: Any, phone: str, organization_id: int | None = None) -> int | None:
    """Получить ID pending-заказа. При Redis miss — из БД."""
    org = _effective_org(organization_id)
    raw = await redis.get(_pending_order_key(org, phone))
    if raw:
        return int(raw)
    val = await _db_recover_pending_order(phone, organization_id)
    if val is not None:
        await redis.set(_pending_order_key(org, phone), str(val), ex=STATE_TTL)
    return val


def _normalize_cancel_text(text: str) -> str:
    return (text or "").lower().strip().rstrip("!.,")


def is_cancel_all_message(text: str) -> bool:
    """
    Запрос полной отмены корзины/черновиков в CHATTING (обрабатывается до LLM).

    Не путать с CANCEL_WORDS для FSM «Да/Нет»: «нет» в свободном чате — не отмена заказа.
  «отмени плов» — правка позиции (LLM), не полный сброс.
    """
    norm = _normalize_cancel_text(text)
    if not norm:
        return False
    if norm in CANCEL_ALL_PHRASES:
        return True
    if any(p in norm for p in CANCEL_ALL_PHRASES):
        return True
    # Только голое «отмени» / «отмените» без названия блюда
    if norm in ("отмени", "отмените", "отмена", "отменить"):
        return True
    return False


async def clear_pending_order(
    redis: Any,
    phone: str,
    organization_id: int | None = None,
    *,
    reset_state: bool = True,
) -> None:
    """
    Очистить ожидающий заказ в Redis.
    По умолчанию возвращает CHATTING, но не трогает HUMAN_MODE (эскалация на оператора).
    """
    org = _effective_org(organization_id)
    await redis.delete(_pending_order_key(org, phone))
    if not reset_state:
        return
    current = await get_user_state(redis, phone, organization_id=organization_id)
    if current != UserState.HUMAN_MODE:
        await set_user_state(redis, phone, UserState.CHATTING, organization_id=organization_id)


async def clear_pending_order_durable(
    redis: Any,
    db: Any,
    *,
    phone: str,
    organization_id: int,
    reset_state: bool = True,
) -> None:
    """Сброс pending_order_id в БД и Redis (без отдельного commit — в транзакции вызывающего)."""
    await update_user_session_fields_in_db(
        db,
        phone=phone,
        organization_id=organization_id,
        current_pending_order_id=None,
    )
    await clear_pending_order(
        redis, phone, organization_id=organization_id, reset_state=reset_state,
    )


async def set_pending_booking(
    redis: Any, phone: str, booking_id: int, organization_id: int | None = None,
) -> None:
    """Сохранить ID бронирования, ожидающего подтверждения."""
    org = _effective_org(organization_id)
    await redis.set(_pending_booking_key(org, phone), str(booking_id), ex=STATE_TTL)


async def get_pending_booking(redis: Any, phone: str, organization_id: int | None = None) -> int | None:
    """Получить ID pending-бронирования. При Redis miss — из БД."""
    org = _effective_org(organization_id)
    raw = await redis.get(_pending_booking_key(org, phone))
    if raw:
        return int(raw)
    val = await _db_recover_pending_booking(phone, organization_id)
    if val is not None:
        await redis.set(_pending_booking_key(org, phone), str(val), ex=STATE_TTL)
    return val


async def clear_pending_booking(redis: Any, phone: str, organization_id: int | None = None) -> None:
    """Очистить ожидающее бронирование и вернуть состояние в CHATTING."""
    org = _effective_org(organization_id)
    await redis.delete(_pending_booking_key(org, phone))
    await set_user_state(redis, phone, UserState.CHATTING, organization_id=organization_id)


async def get_chat_history(
    redis: Any, phone: str, organization_id: int | None = None,
) -> list[dict[str, str]]:
    """
    Получить историю последних сообщений диалога.

    Returns:
        Список словарей [{role: "user"/"assistant", content: "..."}],
        отсортированный хронологически (старые → новые).
    """
    key = _history_key(organization_id, phone)
    raw_messages = await redis.lrange(key, 0, -1)

    history = []
    for raw in raw_messages:
        try:
            history.append(json.loads(raw))
        except json.JSONDecodeError:
            logger.warning("Повреждённое сообщение в Redis для %s: %s", phone, raw)

    logger.debug("Загружена история для org=%s %s: %d сообщений", organization_id, phone, len(history))
    return history


async def append_to_history(
    redis: Any,
    phone: str,
    role: str,
    text: str,
    organization_id: int | None = None,
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
    key = _history_key(organization_id, phone)
    message = json.dumps({"role": role, "content": text}, ensure_ascii=False)

    pipe = redis.pipeline()
    pipe.rpush(key, message)
    pipe.ltrim(key, -MAX_HISTORY_LENGTH, -1)
    pipe.expire(key, HISTORY_TTL)
    await pipe.execute()

    logger.debug("Добавлено сообщение [%s] для org=%s %s", role, organization_id, phone)


async def clear_history(redis: Any, phone: str, organization_id: int | None = None) -> None:
    """Полностью очистить историю диалога (например, после завершения заказа)."""
    key = _history_key(organization_id, phone)
    await redis.delete(key)
    logger.info("История очищена для org=%s %s", organization_id, phone)


async def purge_all_session_keys_for_phone(
    redis: Any, phone: str, organization_id: int | None = None,
) -> None:
    """
    Удалить из Redis/памяти все ключи сессии по номеру (история, state, pending).
    Вызывать при удалении пользователя из БД (например, демо-данные).
    """
    org = _effective_org(organization_id)
    for key_fn in (_history_key, _state_key, _pending_order_key, _pending_booking_key):
        await redis.delete(key_fn(organization_id, phone))
    await redis.delete(_legacy_state_key(phone))
    await redis.delete(f"user:pending_order:{phone}")
    await redis.delete(f"user:pending_booking:{phone}")
    await redis.delete(f"chat:history:{phone}")
    logger.debug("Redis-сессия сброшена для org=%s %s", org, phone)
