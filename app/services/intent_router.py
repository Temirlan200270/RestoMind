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
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import Booking, MenuItem, Order, OrderStatus, User
from app.services.booking_halls import (
    BOOKING_HALL_VIP,
    HALL_LABEL_RU,
    normalize_hall_key,
    vip_slot_occupied,
)
from app.schemas.ai_schemas import AIBrainResponse, PaymentSplit
from app.services.dialog_mgr import UserState
from app.services.events import publish_event
from app.services.order_logic import (
    ValidatedOrder,
    build_order_items_json,
    classify_packaging_kind,
    draft_food_lines_to_order_items,
    enrich_merged_items_from_menu,
    format_order_confirmation_summary,
    load_available_menu,
    merge_cart_actions,
    merge_total_into_items_json,
    validate_mixed_payment_total,
    validate_order,
)

logger = logging.getLogger(__name__)


async def get_open_draft_order(db: AsyncSession, phone: str) -> Order | None:
    """Последний заказ пользователя в статусе DRAFT (для merge и обновления корзины)."""
    result = await db.execute(select(User).where(User.phone == phone))
    user = result.scalar_one_or_none()
    if user is None:
        return None
    q = await db.execute(
        select(Order)
        .where(Order.user_id == user.id, Order.status == OrderStatus.DRAFT)
        .order_by(Order.id.desc())
        .limit(1)
    )
    return q.scalar_one_or_none()


def _blend_ai_with_stored_order_meta(ai: AIBrainResponse, meta: dict | None) -> AIBrainResponse:
    """
    При дельтах к корзине подставляем из черновика тип получения, оплату, адрес —
    если модель их не продублировала в сообщении.
    """
    if not meta or not isinstance(meta, dict):
        return ai
    pm_mode = str(meta.get("payment_mode") or ai.payment_mode)
    pm_meth = str(meta.get("payment_method") or ai.payment_method)
    ps = PaymentSplit()
    if pm_mode == "mixed":
        pd = meta.get("payment_details") if isinstance(meta.get("payment_details"), dict) else {}
        sp = pd.get("split") if isinstance(pd.get("split"), dict) else {}
        ps = PaymentSplit(
            cash=float(sp.get("cash") or 0),
            card=float(sp.get("card") or 0),
            remote=float(sp.get("remote") or 0),
        )
    ot = str(meta.get("order_type") or ai.order_type)
    da = (ai.delivery_address or "").strip() or str(meta.get("delivery_address") or "")
    pu = (ai.pickup_time_note or "").strip() or str(meta.get("pickup_time_note") or "")
    bt = ai.booking_time
    if bt is None and meta.get("booking_time"):
        bt = str(meta.get("booking_time"))
    ip = bool(meta.get("is_preorder", ai.is_preorder))

    out = ai.model_copy(
        update={
            "order_type": ot,
            "payment_mode": pm_mode,
            "payment_method": pm_meth,
            "payment_split": ps,
            "delivery_address": da,
            "pickup_time_note": pu,
            "booking_time": bt,
            "is_preorder": ip,
        }
    )
    mix_sum = float(ai.payment_split.cash) + float(ai.payment_split.card) + float(ai.payment_split.remote)
    if ai.payment_mode == "mixed" and mix_sum > 0.5:
        out = out.model_copy(
            update={"payment_mode": "mixed", "payment_split": ai.payment_split},
        )
    if (ai.delivery_address or "").strip():
        out = out.model_copy(update={"delivery_address": ai.delivery_address.strip()})
    if (ai.pickup_time_note or "").strip():
        out = out.model_copy(update={"pickup_time_note": ai.pickup_time_note.strip()})
    return out


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
    Валидация → создание или обновление DRAFT → запрос подтверждения у клиента.
    При активном черновике и непустом order_actions — merge с items_json (Phase 18).
    """
    if menu_items is None:
        menu_items = await load_available_menu(db)

    existing_draft = await get_open_draft_order(db, phone)
    use_merge = bool(ai_response.order_actions) and existing_draft is not None

    ai_eff: AIBrainResponse
    validated: ValidatedOrder

    if use_merge:
        raw_list = (existing_draft.items_json or {}).get("items")
        current_items = [x for x in (raw_list or []) if isinstance(x, dict)]
        merged = merge_cart_actions(current_items, ai_response.order_actions)
        if not merged:
            return RouteResult(
                reply_text=(
                    f"{ai_response.reply_text}\n\n"
                    "⚠️ После изменений в заказе не осталось ни одной позиции. "
                    "Назовите блюда, которые оставляем, или соберите заказ заново."
                ),
            )
        meta = (existing_draft.items_json or {}).get("order_meta")
        ai_eff = _blend_ai_with_stored_order_meta(
            ai_response, meta if isinstance(meta, dict) else None,
        )
        enriched = enrich_merged_items_from_menu(merged, menu_items)
        order_items = draft_food_lines_to_order_items(enriched)
        validated = await validate_order(order_items, menu_items=menu_items, db=db)
    elif ai_response.items:
        ai_eff = ai_response
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
    else:
        if ai_response.order_actions and not existing_draft:
            return RouteResult(
                reply_text=(
                    f"{ai_response.reply_text}\n\n"
                    "⚠️ Нет активного заказа для изменения. Сначала выберите блюда — "
                    "или опишите заказ целиком списком в `items`."
                ),
            )
        return RouteResult(reply_text=ai_response.reply_text)

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

    for vi in validated.valid_items:
        pk = classify_packaging_kind(str(vi.get("name", "")), str(vi.get("category", "")))
        if pk == "plov_1kg":
            ch = (vi.get("packaging_plov_1kg") or "").strip()
            if ch not in ("tabak", "foil_kazan"):
                return RouteResult(
                    reply_text=(
                        f"{ai_response.reply_text}\n\n"
                        "⚠️ Для **плова 1 кг** уточните упаковку:\n"
                        "  • **табак** (традиционный контейнер) — "
                        f"{int(settings.packaging_plov_1kg_tabak_unit_price)} ₸\n"
                        "  • **казан** / фольгированный казан — "
                        f"{int(settings.packaging_plov_1kg_foil_unit_price)} ₸\n"
                        "Напишите, что выбираете (одним сообщением можно дополнить заказ)."
                    ),
                )

    booking_row: Booking | None = None
    if existing_draft and existing_draft.booking_id:
        booking_row = await db.get(Booking, existing_draft.booking_id)
    elif ai_eff.order_type == "hall" and ai_eff.is_preorder:
        if not ai_eff.booking_details:
            return RouteResult(
                reply_text=(
                    f"{ai_response.reply_text}\n\n"
                    "⚠️ Для предзаказа в зале укажите **дату брони** (можно не сегодня), "
                    "**время визита** и **сколько гостей** — например: «завтра в 19:00, нас четверо, зал 1»."
                ),
            )
        bd = ai_eff.booking_details
        try:
            booking_date = date.fromisoformat(bd.date)
            booking_time = time.fromisoformat(bd.time)
        except ValueError:
            logger.warning(
                "Предзаказ в зале: неверная дата/время %s %s", bd.date, bd.time,
            )
            return RouteResult(
                reply_text=(
                    f"{ai_response.reply_text}\n\n"
                    "⚠️ Не удалось определить дату или время брони. Укажите дату (например «25.03») и время."
                ),
            )
        hall = normalize_hall_key(bd.hall)
        if hall == BOOKING_HALL_VIP and await vip_slot_occupied(db, booking_date, booking_time, None):
            return RouteResult(
                reply_text=(
                    f"{ai_response.reply_text}\n\n"
                    "⚠️ VIP-зал на это время уже занят. Предложите другое время или Зал 1 / Зал 2."
                ),
            )
        booking_row = Booking(
            user_id=user.id,
            booking_date=booking_date,
            booking_time=booking_time,
            guests=bd.guests,
            hall=hall,
            comment=bd.comment,
            status="draft",
        )
        db.add(booking_row)
        await db.flush()

    payload, grand_total = build_order_items_json(validated, ai_eff)
    mix_err = validate_mixed_payment_total(ai_eff, grand_total)
    if mix_err:
        return RouteResult(
            reply_text=f"{ai_response.reply_text}\n\n⚠️ {mix_err}",
        )

    items_json = merge_total_into_items_json(payload, grand_total)
    requires_big_order_prepay = bool(
        (items_json.get("order_meta") or {}).get("requires_order_prepayment"),
    )
    prepayment_status = (
        "pending" if (booking_row or requires_big_order_prepay) else "not_required"
    )

    if existing_draft and (use_merge or ai_response.items):
        expected_ver = int(existing_draft.row_version)
        new_booking_id = booking_row.id if booking_row is not None else existing_draft.booking_id
        upd = (
            await db.execute(
                update(Order)
                .where(
                    Order.id == existing_draft.id,
                    Order.status == OrderStatus.DRAFT.value,
                    Order.row_version == expected_ver,
                )
                .values(
                    items_json=items_json,
                    total_price=grand_total,
                    prepayment_status=prepayment_status,
                    booking_id=new_booking_id,
                    row_version=Order.row_version + 1,
                )
            )
        ).rowcount
        if upd != 1:
            logger.warning(
                "Optimistic lock: заказ id=%s версия=%s не обновлён (гонка)",
                existing_draft.id,
                expected_ver,
            )
            return RouteResult(
                reply_text=(
                    f"{ai_response.reply_text}\n\n"
                    "⚠️ Заказ только что изменился параллельно. Напишите ещё раз одним сообщением — "
                    "я пересчитаю состав."
                ),
            )
        await db.refresh(existing_draft)
        order = existing_draft
    else:
        order = Order(
            user_id=user.id,
            status=OrderStatus.DRAFT,
            items_json=items_json,
            total_price=grand_total,
            booking_id=booking_row.id if booking_row else None,
            prepayment_status=prepayment_status,
            row_version=1,
        )
        db.add(order)
        await db.flush()

    body_text = format_order_confirmation_summary(items_json, validated.summary_text)
    reply = ai_response.reply_text + "\n\n📋 Ваш заказ:\n" + body_text

    if validated.unknown_items:
        reply += "\n\nНе нашёл в меню некоторые позиции. Уточните, пожалуйста."

    # Если клиент уже указал оплату в сообщении, не переспрашиваем — сразу просим подтверждение.
    # Также, если для самовывоза не указано время, сначала уточняем время (коротко).
    ot = (ai_eff.order_type or "").strip().lower()
    is_mixed = ai_eff.payment_mode == "mixed"
    pm = (ai_eff.payment_method or "").strip().lower()
    pickup_note = (ai_eff.pickup_time_note or "").strip()
    delivery_addr = (ai_eff.delivery_address or "").strip()

    if not ot:
        reply += "\n\nКак удобнее получить заказ — **доставка**, **самовывоз** или **в зале**?"
        next_state = UserState.CHATTING
        log_hint = "уточняем тип получения"
    elif not is_mixed and not pm:
        reply += (
            "\n\n💳 **Как удобнее оплатить заказ?**\n"
            "  • наличными при получении\n"
            "  • картой при получении (терминал)\n"
            "  • удалённо (перевод / ссылка на оплату)\n"
            "\nНапишите один вариант."
        )
        next_state = UserState.AWAITING_ORDER_PAYMENT
        log_hint = "ждём способ оплаты"
    elif ot == "delivery" and not delivery_addr:
        reply += "\n\n📍 Подскажите адрес доставки (улица, дом/кв)."
        next_state = UserState.CHATTING
        log_hint = "уточняем адрес доставки"
    elif ot == "pickup" and not pickup_note:
        reply += "\n\n🕐 К какому времени вам удобно забрать заказ?"
        next_state = UserState.CHATTING
        log_hint = "уточняем время самовывоза"
    elif requires_big_order_prepay:
        reply += (
            "\n\n"
            f"💳 Сумма заказа от **{int(settings.order_prepayment_threshold_kzt):,}** ₸ — "
            "нужна **предоплата** (полная или частичная). Оператор пришлёт реквизиты или ссылку. "
            "После оплаты вы сможете подтвердить заказ ответом «Да»."
        )
        next_state = UserState.CHATTING
        log_hint = "ожидание предоплаты (крупный заказ)"
    else:
        reply += "\n\n✅ Подтверждаете заказ? (Да / Нет)"
        next_state = UserState.CONFIRMING_ORDER
        log_hint = "ждём подтверждение"

    logger.info(
        "Заказ #%d (DRAFT): %d позиций блюд, %.2f ₸ — %s",
        order.id, len(validated.valid_items), grand_total, log_hint,
    )

    await publish_event("order_updated", {
        "order_id": order.id, "status": OrderStatus.DRAFT,
        "phone": phone, "total_price": grand_total,
        "items": validated.valid_items,
        "order_type": ai_eff.order_type,
        "payment_method": ai_eff.payment_method,
        "booking_id": booking_row.id if booking_row else None,
    })

    return RouteResult(
        reply_text=reply,
        pending_order_id=order.id,
        new_state=next_state,
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
    """Подтвердить заказ — перевести из DRAFT в confirmed; связанную бронь — тоже."""
    result = await db.execute(select(Order).where(Order.id == order_id))
    order = result.scalar_one_or_none()

    if order and order.status == OrderStatus.DRAFT:
        if order.booking_id:
            _bk, err = await confirm_booking(db, order.booking_id)
            if err:
                return None
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
    """Отменить заказ — перевести из DRAFT в cancelled; черновую бронь — отменить."""
    result = await db.execute(select(Order).where(Order.id == order_id))
    order = result.scalar_one_or_none()

    if order and order.status == OrderStatus.DRAFT:
        order.status = OrderStatus.CANCELLED
        if order.booking_id:
            await cancel_booking(db, order.booking_id)
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
