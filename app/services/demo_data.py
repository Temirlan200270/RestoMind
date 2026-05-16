"""
Демо-данные для админ-панели: создаются и удаляются пакетно по префиксу телефона.
"""

import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AiUsageLog,
    Booking,
    BusinessRecommendation,
    ChatLog,
    EscalationEvent,
    FailedTask,
    IntelligenceConversation,
    IntelligenceMessage,
    OperationalInsight,
    Order,
    OrderStatus,
    PaymentEvent,
    PipelineLatencyLog,
    RestaurantStateSnapshot,
    SystemEvent,
    User,
)
from app.db.session import redis_client
from app.services.dialog_mgr import purge_all_session_keys_for_phone
from app.services.intelligence import (
    answer_intelligence_query,
    build_state_snapshot,
    detect_ai_incidents,
    generate_revenue_order_insights,
)
from app.services.order_logic import build_demo_order_payload
from app.services.recommendations import generate_recommendations

logger = logging.getLogger(__name__)

# Все демо-пользователи имеют телефон с этим префиксом (уникально для UI-демо)
DEMO_PHONE_PREFIX = "demo7700"


async def demo_data_exists(db: AsyncSession, *, organization_id: int) -> bool:
    """Есть ли в БД хотя бы один демо-пользователь у данного филиала."""
    q = await db.execute(
        select(func.count())
        .select_from(User)
        .where(
            User.phone.startswith(DEMO_PHONE_PREFIX),
            User.organization_id == int(organization_id),
        ),
    )
    return (q.scalar() or 0) > 0


def _line(name: str, qty: int, price_per_unit: float) -> dict:
    total = round(qty * price_per_unit, 2)
    return {
        "name": name,
        "quantity": qty,
        "price_per_unit": price_per_unit,
        "item_total": total,
        "exclude_ingredients": [],
    }


def _demo_order(
    *lines: dict,
    order_type: str = "delivery",
    payment_method: str = "cash",
    **meta: Any,
) -> dict:
    """Полный items_json v2 (блюда, fee_lines, order_meta, total_price)."""
    payload, _grand = build_demo_order_payload(
        list(lines),
        order_type,
        payment_method,
        delivery_address=str(meta.get("delivery_address") or ""),
        pickup_time_note=str(meta.get("pickup_time_note") or ""),
        is_preorder=bool(meta.get("is_preorder", False)),
        booking_time=meta.get("booking_time"),
    )
    extra_meta = {
        key: value
        for key, value in meta.items()
        if key not in {"delivery_address", "pickup_time_note", "is_preorder", "booking_time"}
    }
    if extra_meta:
        order_meta = payload.setdefault("order_meta", {})
        if isinstance(order_meta, dict):
            order_meta.update(extra_meta)
    return payload


def _extra_volume_orders(users: list[User]) -> list[tuple[int, str, dict, int]]:
    """
    Дополнительные заказы по 7 календарным дням UTC — график дашборда и канбан нагляднее.
    Типы доставки/самовывоза/зала и оплаты чередуются для демо v2.
    """
    if not users:
        return []
    n = len(users)
    dish_line_sets = [
        [_line("Самса с тыквой", 2, 690)],
        [_line("Шашлык из баранины", 2, 4290), _line("Лепёшка", 2, 350)],
        [_line("Борщ украинский", 2, 2190), _line("Сало с чесноком", 1, 1490)],
        [_line("Холодец домашний", 1, 2790)],
        [_line("Чак-чак", 1, 1990), _line("Чай улун 0,6 л", 1, 1190)],
        [_line("Бешбармак классический", 2, 3490)],
        [_line("Куырдак", 1, 3990), _line("Кумыс 0,5 л", 1, 1590)],
    ]
    otypes = ("delivery", "pickup", "hall")
    pays = ("cash", "card", "remote")
    statuses = [
        OrderStatus.COMPLETED.value,
        OrderStatus.COMPLETED.value,
        OrderStatus.CONFIRMED.value,
        OrderStatus.COMPLETED.value,
        OrderStatus.SENT_TO_IIKO.value,
    ]
    out: list[tuple[int, str, dict, int]] = []
    idx = 0
    for days_ago in range(7):
        for slot in range(5):
            uid = users[(days_ago + slot * 2) % n].id
            st = statuses[(days_ago + slot) % len(statuses)]
            lines = dish_line_sets[(days_ago + slot) % len(dish_line_sets)]
            ot = otypes[(days_ago + slot + idx) % 3]
            pm = pays[(days_ago + slot * 2) % 3]
            meta: dict[str, Any] = {}
            if ot == "delivery":
                meta["delivery_address"] = f"г. Алматы, ул. Демо {idx % 90 + 1}, кв. {slot + 1}"
            elif ot == "pickup":
                h1, h2 = 17 + slot % 3, 18 + slot % 2
                m1, m2 = (10 + days_ago * 3) % 60, (20 + idx % 40) % 60
                meta["pickup_time_note"] = f"{h1}:{m1:02d}–{h2}:{m2:02d}"
            else:
                meta["booking_time"] = f"2026-06-{15 + (idx % 10):02d} {19 + slot % 2}:00"
                meta["is_preorder"] = (idx % 2) == 0
            if idx % 2 == 0:
                accepted = idx % 6 != 0
                meta["recommendation_trace"] = [
                    {
                        "offered": "Р›РµРїС‘С€РєР°",
                        "accepted": accepted,
                        "accepted_revenue_kzt": 350 if accepted else 0,
                    },
                ]
            elif idx % 5 == 0:
                meta["recommendation_trace"] = [
                    {
                        "offered": "Р§Р°Р№ СѓР»СѓРЅ 0,6 Р»",
                        "accepted": False,
                        "accepted_revenue_kzt": 0,
                    },
                ]
            payload = _demo_order(*lines, order_type=ot, payment_method=pm, **meta)
            out.append((uid, st, payload, days_ago))
            idx += 1
    return out


def _extra_month_history_orders(users: list[User]) -> list[tuple[int, str, dict, int]]:
    """
    Заказы 8…29 суток назад (UTC) — в «Неделю» не попадают, в «Месяц» дают сумму больше, чем за неделю.
    """
    if not users:
        return []
    n = len(users)
    out: list[tuple[int, str, dict, int]] = []
    for days_ago in range(8, 30):
        uid = users[days_ago % n].id
        lines = [
            _line("Комбо-ланч демо", 1, 2590),
            _line("Чай чёрный 0,4 л", 1, 590),
        ]
        ot = "delivery" if days_ago % 2 == 0 else "pickup"
        pm = "remote" if days_ago % 3 == 0 else "cash"
        meta: dict[str, Any] = {}
        if ot == "delivery":
            meta["delivery_address"] = "г. Алматы, мкр. Демо-история, 12"
        else:
            meta["pickup_time_note"] = "вечером после 18:00"
        meta["recommendation_trace"] = [
            {
                "offered": "РљРѕРјР±Рѕ-РґРµСЃРµСЂС‚",
                "accepted": days_ago % 4 == 0,
                "accepted_revenue_kzt": 890 if days_ago % 4 == 0 else 0,
            },
        ]
        payload = _demo_order(*lines, order_type=ot, payment_method=pm, **meta)
        out.append((uid, OrderStatus.COMPLETED.value, payload, days_ago))
    return out


def _set_demo_user_profile(user: User, index: int, now: datetime) -> None:
    notes = [
        "VIP: любит тихий зал, без острой еды.",
        "Часто берёт самовывоз после работы.",
        "Нужен стол подальше от колонки.",
        "Просит не звонить, только WhatsApp.",
        "Предпочитает оплату ссылкой заранее.",
    ]
    if index < len(notes):
        user.operator_note = notes[index]
    user.meta_json = {
        "demo_segment": "vip" if index in {0, 4, 7} else "regular",
        "favorite_channel": "whatsapp",
    }
    if index == 2:
        user.ai_paused = True
    if index == 3:
        user.ai_snoozed_until = now + timedelta(hours=4)
    if index == 8:
        user.marketing_opt_out = True


async def _seed_demo_operational_context(
    db: AsyncSession,
    *,
    organization_id: int,
    demo_users: list[User],
    demo_orders: list[Order],
    now: datetime,
) -> dict[str, int]:
    created_payments = 0
    created_system_events = 0
    created_escalations = 0
    created_failed_tasks = 0
    created_ai_usage_rows = 0
    created_latency_rows = 0

    if demo_orders:
        payment_specs = [
            (demo_orders[2], "prepayment_confirmed", "admin", float(demo_orders[2].total_price or 0), "demo-prepay-ok"),
            (demo_orders[5], "webhook_paid", "webhook", float(demo_orders[5].total_price or 0), "demo-webhook-paid"),
            (demo_orders[10], "manual_reset", "admin", None, "demo-manual-reset"),
        ]
        for order, event_type, actor, amount, note in payment_specs:
            db.add(
                PaymentEvent(
                    order_id=int(order.id),
                    event_type=event_type,
                    actor=actor,
                    amount=amount,
                    note=note,
                    created_at=now - timedelta(hours=created_payments + 1),
                )
            )
            created_payments += 1

        demo_orders[2].prepayment_status = "paid"
        demo_orders[2].payment_provider = "cloudpayments"
        demo_orders[2].payment_link_url = "https://pay.demo.local/order/3"
        demo_orders[2].external_payment_id = "demo-cp-0003"
        demo_orders[2].payment_amount_captured = demo_orders[2].total_price
        demo_orders[3].iiko_last_error = "Кухня не подтвердила терминальную группу для доставки."
        demo_orders[5].prepayment_status = "paid"
        demo_orders[5].payment_provider = "cloudpayments"
        demo_orders[5].external_payment_id = "demo-cp-0006"
        demo_orders[5].payment_amount_captured = demo_orders[5].total_price
        demo_orders[10].kind = "night_preorder"

    escalation_specs = [
        (demo_users[4], "Клиент попросил менеджера по банкету", "Нужна помощь по большому заказу"),
        (demo_users[7], "Гость хочет подтвердить предзаказ в зале", "Уточнение по рассадке и торту"),
        (demo_users[2], "Просьба проверить аллергию", "Нужна ручная консультация по составу"),
    ]
    for idx, (user, user_message, reason) in enumerate(escalation_specs):
        db.add(
            EscalationEvent(
                organization_id=organization_id,
                phone=user.phone,
                user_message=user_message,
                reason=reason,
                created_at=now - timedelta(hours=2 + idx),
            )
        )
        created_escalations += 1

    failed_specs = [
        (demo_users[5], "Оплата по ссылке не открывается", "ARQ retry exhausted after payment notification timeout", False),
        (demo_users[1], "Нужно перенести бронь на час позже", "WhatsApp webhook duplicate payload dropped twice", True),
    ]
    for idx, (user, message_text, error, resolved) in enumerate(failed_specs):
        db.add(
            FailedTask(
                organization_id=organization_id,
                phone=user.phone,
                message_text=message_text,
                error=error,
                attempts=3,
                resolved=resolved,
                created_at=now - timedelta(hours=6 + idx),
            )
        )
        created_failed_tasks += 1

    system_specs = [
        (
            "stoplist_update",
            "demo.seed",
            {"items_added_to_stop": ["Р›РµРїС‘С€РєР°", "РЁР°С€Р»С‹Рє РёР· Р±Р°СЂР°РЅРёРЅС‹"], "source": "demo"},
            now - timedelta(hours=12),
        ),
        (
            "stoplist_update",
            "demo.seed",
            {"items_added_to_stop": ["Р›РµРїС‘С€РєР°"], "source": "demo"},
            now - timedelta(days=1, hours=3),
        ),
        (
            "stoplist_update",
            "demo.seed",
            {"items_added_to_stop": ["Р›РµРїС‘С€РєР°"], "source": "demo"},
            now - timedelta(days=2, hours=1),
        ),
        (
            "payment_webhook_applied",
            "demo.seed",
            {"order_id": int(demo_orders[5].id) if len(demo_orders) > 5 else None, "provider": "cloudpayments"},
            now - timedelta(hours=5),
        ),
    ]
    for idx, (event_type, source, payload_json, created_at) in enumerate(system_specs):
        db.add(
            SystemEvent(
                organization_id=organization_id,
                event_type=event_type,
                source=source,
                entity_type="demo",
                entity_id=f"demo-{idx}",
                idempotency_key=f"demo-system-{organization_id}-{idx}",
                payload_json=payload_json,
                created_at=created_at,
            )
        )
        created_system_events += 1

    for day_offset in range(7):
        day = (now - timedelta(days=6 - day_offset)).date()
        total_tokens = 1800 + day_offset * 220
        error_count = 0
        p95_latency_ms = 2600 + day_offset * 130
        if day_offset == 6:
            total_tokens = 14500
            error_count = 5
            p95_latency_ms = 6900
        db.add(
            AiUsageLog(
                organization_id=organization_id,
                day=day,
                provider="openai",
                model="gpt-4o-mini",
                prompt_tokens=int(total_tokens * 0.6),
                completion_tokens=int(total_tokens * 0.4),
                total_tokens=total_tokens,
                call_count=18 + day_offset,
                error_count=error_count,
                p95_latency_ms=p95_latency_ms,
            )
        )
        created_ai_usage_rows += 1

    for idx in range(8):
        db.add(
            PipelineLatencyLog(
                organization_id=organization_id,
                pipeline_type="whatsapp_text",
                dedupe_ms=40 + idx * 3,
                context_ms=120 + idx * 11,
                llm_ms=900 + idx * 120,
                route_ms=80 + idx * 5,
                reply_ms=110 + idx * 8,
                total_ms=1400 + idx * 150,
                created_at=now - timedelta(minutes=idx * 20),
            )
        )
        created_latency_rows += 1

    await db.flush()

    insights = await generate_revenue_order_insights(db, organization_id)
    ai_incidents = await detect_ai_incidents(db, organization_id)
    recommendations = await generate_recommendations(db, organization_id)
    await answer_intelligence_query(db, org_id=organization_id, question="Почему сегодня просела выручка?")
    await answer_intelligence_query(db, org_id=organization_id, question="Покажи заказы за неделю")
    await build_state_snapshot(db, organization_id, persist=True)

    return {
        "payment_events_added": created_payments,
        "system_events_added": created_system_events,
        "escalations_added": created_escalations,
        "failed_tasks_added": created_failed_tasks,
        "ai_usage_rows_added": created_ai_usage_rows,
        "latency_rows_added": created_latency_rows,
        "insights_added": len(insights) + len(ai_incidents),
        "recommendations_added": len(recommendations),
    }


async def seed_demo_data(db: AsyncSession, *, organization_id: int) -> dict[str, int | bool]:
    """
    Добавить фиктивных пользователей, заказы, брони и сообщения.
    Не очищает существующие данные. Меню из встроенного каталога не подмешивается.
    """
    org_id = int(organization_id)
    if await demo_data_exists(db, organization_id=org_id):
        return {
            "skipped": True,
            "menu_items_added": 0,
        }

    today = date.today()
    # UTC: совпадает с границами суток в /api/admin/stats и /analytics
    now = datetime.now(timezone.utc)
    users_data = [
        ("demo77000001", "Демо Клиент А"),
        ("demo77000002", "Демо Клиент Б"),
        ("demo77000003", "Демо Клиент В"),
        ("demo77000004", "Демо Клиент Г"),
        ("demo77000005", "Демо Клиент Д"),
        ("demo77000006", "Демо Клиент Е"),
        ("demo77000007", "Демо Клиент Ж"),
        ("demo77000008", "Демо Клиент З"),
        ("demo77000009", "Демо Клиент И"),
        ("demo77000010", "Демо Клиент К"),
    ]
    created_users = 0

    for phone, name in users_data:
        existing = await db.execute(
            select(User).where(User.phone == phone, User.organization_id == org_id),
        )
        if existing.scalar_one_or_none():
            continue
        db.add(User(organization_id=org_id, phone=phone, name=name))
        created_users += 1

    await db.flush()

    all_demo = await db.scalars(
        select(User)
        .where(
            User.phone.startswith(DEMO_PHONE_PREFIX),
            User.organization_id == org_id,
        )
        .order_by(User.phone),
    )
    demo_users = list(all_demo.all())
    if not demo_users:
        await db.commit()
        return {"users": 0, "orders": 0, "bookings": 0, "chat_logs": 0, "menu_items_added": 0}

    def u(i: int) -> User:
        return demo_users[min(i, len(demo_users) - 1)]

    for idx, demo_user in enumerate(demo_users):
        _set_demo_user_profile(demo_user, idx, now)

    # Заказы: разные статусы и даты (аналитика / канбан); суммы и fee_lines — по тарифам v2
    orders_spec: list[tuple[int, str, dict, int]] = [
        (u(0).id, OrderStatus.COMPLETED.value, _demo_order(
            _line("Плов праздничный баранина", 2, 2790),
            _line("Шорпа с бараниной", 2, 2190),
            _line("Ташкентский чай 0,8 л", 1, 1890),
            order_type="delivery",
            payment_method="cash",
            delivery_address="г. Алматы, мкр. Самал-2, ул. Баумана 58, кв. 12",
        ), 0),
        (u(1).id, OrderStatus.COMPLETED.value, _demo_order(
            _line("Люля-кебаб из баранины", 3, 1490),
            _line("Капучино 0,3 л", 2, 1190),
            _line("Лепёшка", 2, 350),
            order_type="pickup",
            payment_method="card",
            pickup_time_note="с 18:30 до 19:15",
        ), 0),
        (u(2).id, OrderStatus.CONFIRMED.value, _demo_order(
            _line("Пепперони", 1, 3190),
            _line("Фетучини Альфредо", 1, 3190),
            _line("Лимонад киви-яблоко 1 л", 1, 2390),
            order_type="hall",
            payment_method="remote",
            booking_time="2026-06-20 19:30",
            is_preorder=True,
        ), 0),
        (u(3).id, OrderStatus.DRAFT.value, _demo_order(
            _line("Манты уйгурские", 2, 2390),
            _line("Ачучук", 1, 990),
            _line("Облепиховый чай 0,8 л", 1, 1890),
            order_type="delivery",
            payment_method="card",
            delivery_address="ул. Достык 89, подъезд 3, домофон 45",
        ), 1),
        (u(4).id, OrderStatus.COMPLETED.value, _demo_order(
            _line("Том ям с морепродуктами", 1, 3790),
            _line("Цезарь с курицей", 1, 2790),
            _line("Раф 0,3 л", 2, 1590),
            order_type="pickup",
            payment_method="cash",
            pickup_time_note="завтра к 13:00",
        ), 1),
        (u(0).id, OrderStatus.SENT_TO_IIKO.value, _demo_order(
            _line("ПловXана сет 5-6 персон", 1, 29900),
            _line("Смородина-мята 1,6 л", 2, 2590),
            order_type="delivery",
            payment_method="remote",
            delivery_address="Банкетный зал, доставка к отдельному входу",
        ), 3),
        (u(1).id, OrderStatus.COMPLETED.value, _demo_order(
            _line("Казан-кебаб из баранины", 1, 3690),
            _line("Картофель по-деревенски", 1, 1190),
            _line("Американо 0,3 л", 2, 890),
            order_type="hall",
            payment_method="cash",
            booking_time="2026-06-18 20:00",
        ), 4),
        (u(2).id, OrderStatus.COMPLETED.value, _demo_order(
            _line("Жаз бараний", 2, 1590),
            _line("Греческий", 1, 2690),
            _line("Латте 0,3 л", 2, 1190),
            order_type="delivery",
            payment_method="card",
            delivery_address="пр. Абая 109, офис 304",
        ), 5),
        (u(3).id, OrderStatus.CANCELLED.value, _demo_order(
            _line("Маргарита", 1, 2690),
            order_type="pickup",
            payment_method="cash",
            pickup_time_note="отменён клиентом",
        ), 6),
        (u(4).id, OrderStatus.COMPLETED.value, _demo_order(
            _line("Вагури бухарский", 1, 4100),
            _line("Машхурда", 2, 1990),
            _line("Тандыр-самса с говядиной", 4, 790),
            _line("Марокканский чай 0,8 л", 2, 1890),
            order_type="delivery",
            payment_method="cash",
            delivery_address="ЖК «Демо», ул. Толе би 42",
        ), 0),
        (u(0).id, OrderStatus.DRAFT.value, _demo_order(
            _line("Лагман от шеф-повара", 2, 2790),
            _line("Фреш апельсиновый 0,3 л", 2, 2590),
            order_type="pickup",
            payment_method="remote",
            pickup_time_note="сегодня вечером",
        ), 1),
        (u(1).id, OrderStatus.COMPLETED.value, _demo_order(
            _line("Спагетти болоньезе", 2, 2990),
            _line("Молочный коктейль шоколадный 0,3 л", 2, 1190),
            order_type="delivery",
            payment_method="card",
            delivery_address="мкр. Аксай-1, ул. Гагарина 7",
        ), 2),
    ]
    orders_spec.extend(_extra_volume_orders(demo_users))
    orders_spec.extend(_extra_month_history_orders(demo_users))
    created_orders = len(orders_spec)

    # Полуночь UTC «сегодня» на момент сида: дни 0..6 — окно дашборда (7 дней).
    utc_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    # Секунд с начала «сегодня» UTC до now — заказы за «сегодня» кладём только сюда,
    # иначе при раннем UTC (до 10:00) аналитика «Сегодня» была бы пустой (раньше были часы 10–17).
    elapsed_today = max(120, int((now - utc_midnight).total_seconds()))
    created_order_rows: list[Order] = []

    for idx, (uid, status, items_json, days_ago) in enumerate(orders_spec):
        total = float(items_json["total_price"])
        o = Order(
            organization_id=org_id,
            user_id=uid,
            status=status,
            total_price=total,
            items_json=items_json,
        )
        day_start = utc_midnight - timedelta(days=days_ago)
        if days_ago == 0:
            sec = (idx * 9649) % elapsed_today
            o.created_at = utc_midnight + timedelta(seconds=sec)
        else:
            sec = (idx * 1567) % 86400
            o.created_at = day_start + timedelta(seconds=sec)
        db.add(o)
        created_order_rows.append(o)

    await db.flush()

    bookings_spec = [
        (u(0).id, today + timedelta(days=1), time(19, 0), 4, "confirmed", "У окна, пожалуйста", "hall_1"),
        (u(1).id, today + timedelta(days=3), time(20, 30), 2, "pending", "", "hall_2"),
        (u(2).id, today + timedelta(days=7), time(13, 0), 6, "cancelled", "Клиент отменил", "vip"),
        (u(3).id, today, time(13, 0), 8, "confirmed", "Бизнес-ланч", "vip"),
        (u(4).id, today + timedelta(days=2), time(18, 0), 3, "pending", "Детский стул", "hall_1"),
        (u(0).id, today + timedelta(days=5), time(21, 0), 2, "pending", "Юбилей", "hall_2"),
        (u(5).id, today + timedelta(days=1), time(12, 30), 5, "confirmed", "Вегетарианское меню", "hall_1"),
        (u(6).id, today + timedelta(days=4), time(19, 30), 4, "pending", "Тихий зал", "hall_2"),
        (u(7).id, today + timedelta(days=6), time(14, 0), 2, "confirmed", "День рождения", "hall_1"),
        (u(8).id, today + timedelta(days=2), time(12, 0), 10, "pending", "Корпоратив", "hall_2"),
        (u(9).id, today + timedelta(days=8), time(20, 0), 3, "confirmed", "", "hall_1"),
        (u(5).id, today + timedelta(days=10), time(18, 30), 2, "pending", "Пробный ужин", "vip"),
        (u(1).id, today + timedelta(days=14), time(15, 0), 7, "cancelled", "Перенесли на другой день", "vip"),
        (u(2).id, today + timedelta(days=1), time(17, 0), 4, "confirmed", "Аллергия на орехи", "hall_2"),
    ]
    created_bookings = 0
    for uid, d, t_, guests, st, comment, hall in bookings_spec:
        db.add(
            Booking(
                organization_id=org_id,
                user_id=uid,
                booking_date=d,
                booking_time=t_,
                guests=guests,
                hall=hall,
                comment=comment,
                status=st,
            ),
        )
        created_bookings += 1

    logs_spec: list[tuple[int, str, str]] = [
        (u(0).id, "user", "Здравствуйте, хочу заказать плов"),
        (u(0).id, "assistant", "Конечно! Сколько порций плова и нужен ли самовывоз?"),
        (u(0).id, "user", "Два плова и шорпу, самовывоз"),
        (u(0).id, "assistant", "Записала. Итого 11 850 ₸. Подтверждаете заказ?"),
        (u(1).id, "user", "Есть ли столик на завтра?"),
        (u(1).id, "assistant", "На какое время и сколько гостей?"),
        (u(1).id, "user", "В 20:30, двое"),
        (u(2).id, "user", "Во сколько вы работаете?"),
        (u(2).id, "assistant", "Ежедневно с 10:00 до 23:00. Ждём вас!"),
        (u(3).id, "user", "Можно три люля-кебаб и две лепёшки?"),
        (u(3).id, "assistant", "Да, готовлю корзину. Нужна доставка или заберёте сами?"),
        (u(4).id, "user", "Позовите оператора, пожалуйста"),
        (u(4).id, "assistant", "Передаю запрос менеджеру, ожидайте пару минут."),
        (u(0).id, "operator", "Здравствуйте! Уточнили ваш заказ — готовим к выдаче через 25 минут."),
        (u(5).id, "user", "Доставка на Абая 150, подъезд 2"),
        (u(5).id, "assistant", "Принято. Ориентировочно 45–55 минут. Оплата курьеру?"),
        (u(5).id, "user", "Да, картой"),
        (u(6).id, "user", "Есть ли безлактозное молоко в капучино?"),
        (u(6).id, "assistant", "Да, можем заменить на овсяное или миндальное без доплаты."),
        (u(7).id, "user", "Забронируйте стол на шестерых на субботу"),
        (u(7).id, "assistant", "На какое время в субботу и как к вам обращаться?"),
        (u(7).id, "user", "В 14:00, имя Айдар"),
        (u(8).id, "user", "Меню на детский праздник есть?"),
        (u(8).id, "assistant", "Пришлю варианты сетов и напитков без кофеина."),
        (u(9).id, "user", "Скидка на самовывоз действует?"),
        (u(9).id, "assistant", "Сейчас акция 10% на заказ от 15 000 ₸ при самовывозе."),
        (u(3).id, "user", "Добавьте ещё один Цезарь в черновик"),
        (u(3).id, "assistant", "Добавила. Обновлённая сумма — увидите в корзине."),
        (u(2).id, "system", "Клиент открыл ссылку на меню из рассылки"),
    ]
    created_logs = 0
    log_anchor = now - timedelta(hours=30)
    for i, (uid, role, content) in enumerate(logs_spec):
        ts = log_anchor + timedelta(minutes=i * 6)
        db.add(
            ChatLog(
                organization_id=org_id,
                user_id=uid,
                role=role,
                content=content,
                created_at=ts,
            ),
        )
        created_logs += 1

    extra_stats = await _seed_demo_operational_context(
        db,
        organization_id=org_id,
        demo_users=demo_users,
        demo_orders=created_order_rows,
        now=now,
    )

    await db.commit()
    logger.info(
        "Демо-данные: +users=%s orders=%s bookings=%s logs=%s",
        created_users, created_orders, created_bookings, created_logs,
    )
    return {
        "skipped": False,
        "users_created": created_users,
        "orders_added": created_orders,
        "bookings_added": created_bookings,
        "chat_logs_added": created_logs,
        "menu_items_added": 0,
        **extra_stats,
    }


async def clear_demo_data(db: AsyncSession, *, organization_id: int) -> dict[str, int]:
    """
    Удалить всех пользователей с префиксом DEMO_PHONE_PREFIX и связанные записи.

    Явные DELETE в порядке FK (чаты → брони → заказы → пользователи), чтобы работало
    и при SQLite без PRAGMA foreign_keys, и при любом поведении ORM-каскада.
    Плюс очистка Redis/InMemory-ключей сессии по каждому демо-номеру.
    """
    org_id = int(organization_id)
    res = await db.execute(
        select(User.id, User.phone).where(
            User.phone.startswith(DEMO_PHONE_PREFIX),
            User.organization_id == org_id,
        ),
    )
    rows = res.all()
    if not rows:
        await db.commit()
        return {"users_deleted": 0, "orders_deleted": 0, "bookings_deleted": 0, "chat_logs_deleted": 0}

    user_ids = [r[0] for r in rows]
    phones = [r[1] for r in rows]

    for phone in phones:
        try:
            await purge_all_session_keys_for_phone(redis_client, phone)
        except Exception:
            logger.exception("Не удалось очистить Redis для демо-номера %s", phone)

    order_ids = list(
        (
            await db.execute(
                select(Order.id).where(Order.user_id.in_(user_ids)),
            )
        ).scalars().all()
    )
    conversation_ids = list(
        (
            await db.execute(
                select(IntelligenceConversation.id).where(IntelligenceConversation.organization_id == org_id),
            )
        ).scalars().all()
    )

    r0 = await db.execute(delete(IntelligenceMessage).where(IntelligenceMessage.organization_id == org_id))
    if conversation_ids:
        await db.execute(delete(IntelligenceConversation).where(IntelligenceConversation.id.in_(conversation_ids)))
    await db.execute(delete(RestaurantStateSnapshot).where(RestaurantStateSnapshot.organization_id == org_id))
    await db.execute(delete(OperationalInsight).where(OperationalInsight.organization_id == org_id))
    await db.execute(delete(BusinessRecommendation).where(BusinessRecommendation.organization_id == org_id))
    await db.execute(delete(PipelineLatencyLog).where(PipelineLatencyLog.organization_id == org_id))
    await db.execute(delete(AiUsageLog).where(AiUsageLog.organization_id == org_id))
    await db.execute(delete(SystemEvent).where(SystemEvent.organization_id == org_id))
    await db.execute(delete(FailedTask).where(FailedTask.organization_id == org_id))
    await db.execute(delete(EscalationEvent).where(EscalationEvent.organization_id == org_id))
    if order_ids:
        await db.execute(delete(PaymentEvent).where(PaymentEvent.order_id.in_(order_ids)))

    r1 = await db.execute(delete(ChatLog).where(ChatLog.user_id.in_(user_ids)))
    r2 = await db.execute(delete(Booking).where(Booking.user_id.in_(user_ids)))
    r3 = await db.execute(delete(Order).where(Order.user_id.in_(user_ids)))
    await db.execute(delete(User).where(User.id.in_(user_ids)))

    await db.commit()

    def _rc(res) -> int:
        n = res.rowcount
        return int(n) if n is not None and n >= 0 else 0

    logger.info(
        "Демо-данные удалены: users=%s orders=%s bookings=%s chat_logs=%s",
        len(user_ids),
        _rc(r3),
        _rc(r2),
        _rc(r1),
    )
    return {
        "users_deleted": len(user_ids),
        "orders_deleted": _rc(r3),
        "bookings_deleted": _rc(r2),
        "chat_logs_deleted": _rc(r1),
        "intelligence_messages_deleted": _rc(r0),
    }
