"""
Демо-данные для админ-панели: создаются и удаляются пакетно по префиксу телефона.
"""

import logging
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Booking, ChatLog, Order, OrderStatus, User
from app.db.session import redis_client
from app.services.dialog_mgr import purge_all_session_keys_for_phone
from app.services.menu_bootstrap import ensure_default_menu_if_empty

logger = logging.getLogger(__name__)

# Все демо-пользователи имеют телефон с этим префиксом (уникально для UI-демо)
DEMO_PHONE_PREFIX = "demo7700"


async def demo_data_exists(db: AsyncSession) -> bool:
    """Есть ли в БД хотя бы один демо-пользователь."""
    q = await db.execute(
        select(func.count())
        .select_from(User)
        .where(User.phone.startswith(DEMO_PHONE_PREFIX)),
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


def _order_items(*lines: dict) -> dict:
    return {"items": list(lines)}


def _extra_volume_orders(users: list[User]) -> list[tuple[int, str, float, dict | None, int]]:
    """
    Дополнительные заказы по 7 календарным дням UTC — график дашборда и канбан нагляднее.
    """
    if not users:
        return []
    n = len(users)
    dish_sets = [
        _order_items(_line("Самса с тыквой", 2, 690)),
        _order_items(_line("Шашлык из баранины", 2, 4290), _line("Лепёшка", 2, 350)),
        _order_items(_line("Борщ украинский", 2, 2190), _line("Сало с чесноком", 1, 1490)),
        _order_items(_line("Холодец домашний", 1, 2790)),
        _order_items(_line("Чак-чак", 1, 1990), _line("Чай улун 0,6 л", 1, 1190)),
        _order_items(_line("Бешбармак классический", 2, 3490)),
        _order_items(_line("Куырдак", 1, 3990), _line("Кумыс 0,5 л", 1, 1590)),
    ]
    base_totals = [4200.0, 8900.0, 5600.0, 12400.0, 3300.0, 9800.0, 7200.0, 4500.0, 6700.0, 10200.0]
    statuses = [
        OrderStatus.COMPLETED.value,
        OrderStatus.COMPLETED.value,
        OrderStatus.CONFIRMED.value,
        OrderStatus.COMPLETED.value,
        OrderStatus.SENT_TO_IIKO.value,
    ]
    out: list[tuple[int, str, float, dict | None, int]] = []
    idx = 0
    for days_ago in range(7):
        for slot in range(5):
            uid = users[(days_ago + slot * 2) % n].id
            total = round(
                base_totals[idx % len(base_totals)] + slot * 410.0 + days_ago * 95.0,
                2,
            )
            st = statuses[(days_ago + slot) % len(statuses)]
            items = dish_sets[(days_ago + slot) % len(dish_sets)]
            out.append((uid, st, total, items, days_ago))
            idx += 1
    return out


def _extra_month_history_orders(users: list[User]) -> list[tuple[int, str, float, dict | None, int]]:
    """
    Заказы 8…29 суток назад (UTC) — в «Неделю» не попадают, в «Месяц» дают сумму больше, чем за неделю.
    """
    if not users:
        return []
    n = len(users)
    dish = _order_items(_line("Комбо-ланч демо", 1, 2590), _line("Чай чёрный 0,4 л", 1, 590))
    out: list[tuple[int, str, float, dict | None, int]] = []
    for days_ago in range(8, 30):
        uid = users[days_ago % n].id
        total = round(3200.0 + (days_ago % 11) * 450.0, 2)
        out.append((uid, OrderStatus.COMPLETED.value, total, dish, days_ago))
    return out


async def seed_demo_data(db: AsyncSession) -> dict[str, int | bool]:
    """
    Добавить фиктивных пользователей, заказы, брони и сообщения.
    Не очищает существующие данные (кроме добавления меню, если таблица пуста).

    Если демо-пользователи уже есть — возвращает skipped, но при пустом меню
    всё равно может добавить позиции меню (идемпотентно по меню).
    """
    menu_items_added = await ensure_default_menu_if_empty(db)

    if await demo_data_exists(db):
        if menu_items_added:
            await db.commit()
        return {
            "skipped": True,
            "menu_items_added": menu_items_added,
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
        existing = await db.execute(select(User).where(User.phone == phone))
        if existing.scalar_one_or_none():
            continue
        db.add(User(phone=phone, name=name))
        created_users += 1

    await db.flush()

    all_demo = await db.scalars(
        select(User).where(User.phone.startswith(DEMO_PHONE_PREFIX)).order_by(User.phone),
    )
    demo_users = list(all_demo.all())
    if not demo_users:
        await db.commit()
        return {"users": 0, "orders": 0, "bookings": 0, "chat_logs": 0, "menu_items_added": menu_items_added}

    def u(i: int) -> User:
        return demo_users[min(i, len(demo_users) - 1)]

    # Заказы: разные статусы и даты (аналитика / канбан)
    orders_spec: list[tuple[int, str, float, dict | None, int]] = [
        (u(0).id, OrderStatus.COMPLETED.value, 11850.0, _order_items(
            _line("Плов праздничный баранина", 2, 2790),
            _line("Шорпа с бараниной", 2, 2190),
            _line("Ташкентский чай 0,8 л", 1, 1890),
        ), 0),
        (u(1).id, OrderStatus.COMPLETED.value, 7550.0, _order_items(
            _line("Люля-кебаб из баранины", 3, 1490),
            _line("Капучино 0,3 л", 2, 1190),
            _line("Лепёшка", 2, 350),
        ), 0),
        (u(2).id, OrderStatus.CONFIRMED.value, 8770.0, _order_items(
            _line("Пепперони", 1, 3190),
            _line("Фетучини Альфредо", 1, 3190),
            _line("Лимонад киви-яблоко 1 л", 1, 2390),
        ), 0),
        (u(3).id, OrderStatus.DRAFT.value, 7660.0, _order_items(
            _line("Манты уйгурские", 2, 2390),
            _line("Ачучук", 1, 990),
            _line("Облепиховый чай 0,8 л", 1, 1890),
        ), 1),
        (u(4).id, OrderStatus.COMPLETED.value, 9760.0, _order_items(
            _line("Том ям с морепродуктами", 1, 3790),
            _line("Цезарь с курицей", 1, 2790),
            _line("Раф 0,3 л", 2, 1590),
        ), 1),
        (u(0).id, OrderStatus.SENT_TO_IIKO.value, 35080.0, _order_items(
            _line("ПловXана сет 5-6 персон", 1, 29900),
            _line("Смородина-мята 1,6 л", 2, 2590),
        ), 3),
        (u(1).id, OrderStatus.COMPLETED.value, 6660.0, _order_items(
            _line("Казан-кебаб из баранины", 1, 3690),
            _line("Картофель по-деревенски", 1, 1190),
            _line("Американо 0,3 л", 2, 890),
        ), 4),
        (u(2).id, OrderStatus.COMPLETED.value, 8250.0, _order_items(
            _line("Жаз бараний", 2, 1590),
            _line("Греческий", 1, 2690),
            _line("Латте 0,3 л", 2, 1190),
        ), 5),
        (u(3).id, OrderStatus.CANCELLED.value, 2690.0, _order_items(
            _line("Маргарита", 1, 2690),
        ), 6),
        (u(4).id, OrderStatus.COMPLETED.value, 15020.0, _order_items(
            _line("Вагури бухарский", 1, 4100),
            _line("Машхурда", 2, 1990),
            _line("Тандыр-самса с говядиной", 4, 790),
            _line("Марокканский чай 0,8 л", 2, 1890),
        ), 0),
        (u(0).id, OrderStatus.DRAFT.value, 10760.0, _order_items(
            _line("Лагман от шеф-повара", 2, 2790),
            _line("Фреш апельсиновый 0,3 л", 2, 2590),
        ), 1),
        (u(1).id, OrderStatus.COMPLETED.value, 8360.0, _order_items(
            _line("Спагетти болоньезе", 2, 2990),
            _line("Молочный коктейль шоколадный 0,3 л", 2, 1190),
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

    for idx, (uid, status, total, items_json, days_ago) in enumerate(orders_spec):
        o = Order(
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
        db.add(ChatLog(user_id=uid, role=role, content=content, created_at=ts))
        created_logs += 1

    await db.commit()
    logger.info(
        "Демо-данные: +users=%s orders=%s bookings=%s logs=%s menu_added=%s",
        created_users, created_orders, created_bookings, created_logs, menu_items_added,
    )
    return {
        "skipped": False,
        "users_created": created_users,
        "orders_added": created_orders,
        "bookings_added": created_bookings,
        "chat_logs_added": created_logs,
        "menu_items_added": menu_items_added,
    }


async def clear_demo_data(db: AsyncSession) -> dict[str, int]:
    """
    Удалить всех пользователей с префиксом DEMO_PHONE_PREFIX и связанные записи.

    Явные DELETE в порядке FK (чаты → брони → заказы → пользователи), чтобы работало
    и при SQLite без PRAGMA foreign_keys, и при любом поведении ORM-каскада.
    Плюс очистка Redis/InMemory-ключей сессии по каждому демо-номеру.
    """
    res = await db.execute(
        select(User.id, User.phone).where(User.phone.startswith(DEMO_PHONE_PREFIX)),
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
    }
