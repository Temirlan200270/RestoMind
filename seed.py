"""
Скрипт заполнения БД демо-данными (меню РойХана).
Запуск: python seed.py
"""

import asyncio
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import select

from app.db.models import Base, Booking, ChatLog, MenuItem, Order, OrderStatus, User
from app.db.session import async_engine, async_session_factory
from app.services.menu_bootstrap import ensure_default_menu_if_empty


async def seed() -> None:
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    print("[OK] Таблицы созданы")

    async with async_session_factory() as db:
        now = datetime.now(timezone.utc)
        today = date.today()

        # === Пользователи ===
        users = [
            User(phone="77011234567", name="Айсулу Касенова"),
            User(phone="77017654321", name="Арман Бектуров"),
            User(phone="77015551234", name="Динара Муратова"),
            User(phone="77019876543", name="Руслан Ахметов"),
            User(phone="77012223344", name="Камила Нуржанова"),
        ]
        db.add_all(users)
        await db.flush()
        print(f"[OK] Создано {len(users)} пользователей")

        # === Меню ПловXана (встроенный каталог, как при автозапуске приложения) ===
        n_menu = await ensure_default_menu_if_empty(db)
        await db.flush()
        print(f"[OK] Создано {n_menu} позиций меню (с фейковыми iiko_id)")

        menu_rows = (await db.execute(select(MenuItem))).scalars().all()
        menu_iiko: dict[str, str] = {
            mi.name: mi.iiko_id for mi in menu_rows if mi.iiko_id
        }

        def _items(raw: list[dict]) -> dict:
            """Добавляет iiko_id к каждой позиции заказа из справочника."""
            for item in raw:
                item["iiko_id"] = menu_iiko.get(item["name"], "")
            return {"items": raw}

        # === Заказы (разные даты для аналитики) ===
        orders = [
            # Сегодня
            Order(
                user_id=users[0].id, status=OrderStatus.COMPLETED,
                items_json=_items([
                    {"name": "Плов праздничный баранина", "quantity": 2, "price_per_unit": 2790, "item_total": 5580, "exclude_ingredients": []},
                    {"name": "Шорпа с бараниной", "quantity": 2, "price_per_unit": 2190, "item_total": 4380, "exclude_ingredients": []},
                    {"name": "Ташкентский чай 0,8 л", "quantity": 1, "price_per_unit": 1890, "item_total": 1890, "exclude_ingredients": []},
                ]),
                total_price=11850,
            ),
            Order(
                user_id=users[1].id, status=OrderStatus.COMPLETED,
                items_json=_items([
                    {"name": "Люля-кебаб из баранины", "quantity": 3, "price_per_unit": 1490, "item_total": 4470, "exclude_ingredients": []},
                    {"name": "Капучино 0,3 л", "quantity": 2, "price_per_unit": 1190, "item_total": 2380, "exclude_ingredients": []},
                    {"name": "Лепёшка", "quantity": 2, "price_per_unit": 350, "item_total": 700, "exclude_ingredients": []},
                ]),
                total_price=7550,
            ),
            Order(
                user_id=users[2].id, status=OrderStatus.CONFIRMED,
                items_json=_items([
                    {"name": "Пепперони", "quantity": 1, "price_per_unit": 3190, "item_total": 3190, "exclude_ingredients": []},
                    {"name": "Фетучини Альфредо", "quantity": 1, "price_per_unit": 3190, "item_total": 3190, "exclude_ingredients": []},
                    {"name": "Лимонад киви-яблоко 1 л", "quantity": 1, "price_per_unit": 2390, "item_total": 2390, "exclude_ingredients": []},
                ]),
                total_price=8770,
            ),
            # Вчера
            Order(
                user_id=users[3].id, status=OrderStatus.COMPLETED,
                items_json=_items([
                    {"name": "Манты уйгурские", "quantity": 2, "price_per_unit": 2390, "item_total": 4780, "exclude_ingredients": []},
                    {"name": "Ачучук", "quantity": 1, "price_per_unit": 990, "item_total": 990, "exclude_ingredients": []},
                    {"name": "Облепиховый чай 0,8 л", "quantity": 1, "price_per_unit": 1890, "item_total": 1890, "exclude_ingredients": []},
                ]),
                total_price=7660,
            ),
            Order(
                user_id=users[4].id, status=OrderStatus.COMPLETED,
                items_json=_items([
                    {"name": "Том ям с морепродуктами", "quantity": 1, "price_per_unit": 3790, "item_total": 3790, "exclude_ingredients": []},
                    {"name": "Цезарь с курицей", "quantity": 1, "price_per_unit": 2790, "item_total": 2790, "exclude_ingredients": []},
                    {"name": "Раф 0,3 л", "quantity": 2, "price_per_unit": 1590, "item_total": 3180, "exclude_ingredients": []},
                ]),
                total_price=9760,
            ),
            # 3 дня назад
            Order(
                user_id=users[0].id, status=OrderStatus.COMPLETED,
                items_json=_items([
                    {"name": "ПловXана сет 5-6 персон", "quantity": 1, "price_per_unit": 29900, "item_total": 29900, "exclude_ingredients": []},
                    {"name": "Смородина-мята 1,6 л", "quantity": 2, "price_per_unit": 2590, "item_total": 5180, "exclude_ingredients": []},
                ]),
                total_price=35080,
            ),
            # 5 дней назад
            Order(
                user_id=users[1].id, status=OrderStatus.COMPLETED,
                items_json=_items([
                    {"name": "Казан-кебаб из баранины", "quantity": 1, "price_per_unit": 3690, "item_total": 3690, "exclude_ingredients": []},
                    {"name": "Картофель по-деревенски", "quantity": 1, "price_per_unit": 1190, "item_total": 1190, "exclude_ingredients": []},
                    {"name": "Американо 0,3 л", "quantity": 2, "price_per_unit": 890, "item_total": 1780, "exclude_ingredients": []},
                ]),
                total_price=6660,
            ),
            # Неделю назад
            Order(
                user_id=users[2].id, status=OrderStatus.COMPLETED,
                items_json=_items([
                    {"name": "Жаз бараний", "quantity": 2, "price_per_unit": 1590, "item_total": 3180, "exclude_ingredients": []},
                    {"name": "Греческий", "quantity": 1, "price_per_unit": 2690, "item_total": 2690, "exclude_ingredients": []},
                    {"name": "Латте 0,3 л", "quantity": 2, "price_per_unit": 1190, "item_total": 2380, "exclude_ingredients": []},
                ]),
                total_price=8250,
            ),
            Order(
                user_id=users[3].id, status=OrderStatus.CANCELLED,
                items_json=_items([
                    {"name": "Маргарита", "quantity": 1, "price_per_unit": 2690, "item_total": 2690, "exclude_ingredients": []},
                ]),
                total_price=2690,
            ),
            # 2 недели назад
            Order(
                user_id=users[4].id, status=OrderStatus.COMPLETED,
                items_json=_items([
                    {"name": "Вагури бухарский", "quantity": 1, "price_per_unit": 4100, "item_total": 4100, "exclude_ingredients": []},
                    {"name": "Машхурда", "quantity": 2, "price_per_unit": 1990, "item_total": 3980, "exclude_ingredients": []},
                    {"name": "Тандыр-самса с говядиной", "quantity": 4, "price_per_unit": 790, "item_total": 3160, "exclude_ingredients": []},
                    {"name": "Марокканский чай 0,8 л", "quantity": 2, "price_per_unit": 1890, "item_total": 3780, "exclude_ingredients": []},
                ]),
                total_price=15020,
            ),
            # 3 недели назад
            Order(
                user_id=users[0].id, status=OrderStatus.COMPLETED,
                items_json=_items([
                    {"name": "Лагман от шеф-повара", "quantity": 2, "price_per_unit": 2790, "item_total": 5580, "exclude_ingredients": []},
                    {"name": "Фреш апельсиновый 0,3 л", "quantity": 2, "price_per_unit": 2590, "item_total": 5180, "exclude_ingredients": []},
                ]),
                total_price=10760,
            ),
            Order(
                user_id=users[1].id, status=OrderStatus.COMPLETED,
                items_json=_items([
                    {"name": "Спагетти болоньезе", "quantity": 2, "price_per_unit": 2990, "item_total": 5980, "exclude_ingredients": []},
                    {"name": "Молочный коктейль шоколадный 0,3 л", "quantity": 2, "price_per_unit": 1190, "item_total": 2380, "exclude_ingredients": []},
                ]),
                total_price=8360,
            ),
        ]

        # Даты: все в пределах последних 7 календарных дней UTC (дашборд / мини-график).
        utc_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        date_offsets = [0, 0, 0, 1, 1, 2, 3, 4, 5, 6, 0, 1]
        for i, order in enumerate(orders):
            offset = date_offsets[i] if i < len(date_offsets) else 0
            hour_off = 10 + (i % 8)
            order.created_at = utc_midnight - timedelta(days=offset) + timedelta(hours=hour_off)

        db.add_all(orders)
        await db.flush()
        print(f"[OK] Создано {len(orders)} заказов")

        # === Бронирования ===
        tomorrow = today + timedelta(days=1)
        day_after = today + timedelta(days=2)
        bookings = [
            Booking(user_id=users[0].id, booking_date=tomorrow, booking_time=time(19, 0), guests=4, comment="У окна, пожалуйста", status=OrderStatus.CONFIRMED),
            Booking(user_id=users[1].id, booking_date=tomorrow, booking_time=time(20, 30), guests=2, comment="", status="pending"),
            Booking(user_id=users[2].id, booking_date=day_after, booking_time=time(18, 0), guests=6, comment="День рождения, будет торт", status="pending"),
            Booking(user_id=users[3].id, booking_date=today, booking_time=time(13, 0), guests=8, comment="Бизнес-ланч на компанию", status=OrderStatus.CONFIRMED),
        ]
        db.add_all(bookings)
        await db.flush()
        print(f"[OK] Создано {len(bookings)} бронирований")

        # === Логи чатов ===
        chat_logs = [
            ChatLog(user_id=users[0].id, role="user", content="Салам! Хочу два плова и шорпу"),
            ChatLog(user_id=users[0].id, role="assistant", content="Здравствуйте! Ваш заказ:\n  - Плов праздничный x 2 — 5 580 ₸\n  - Шорпа с бараниной x 2 — 4 780 ₸\n\nИтого: 12 250 ₸\n\nПодтверждаете?"),
            ChatLog(user_id=users[0].id, role="user", content="Да, подтверждаю"),
            ChatLog(user_id=users[0].id, role="assistant", content="Заказ оформлен! Ожидайте 30-40 минут. Приятного аппетита!"),

            ChatLog(user_id=users[1].id, role="user", content="Забронируйте столик на завтра на 20:30 на двоих"),
            ChatLog(user_id=users[1].id, role="assistant", content="Готово! Столик забронирован:\nЗавтра в 20:30\n2 гостя\n\nЕсть пожелания к столику?"),
            ChatLog(user_id=users[1].id, role="user", content="Нет, всё отлично, спасибо!"),
            ChatLog(user_id=users[1].id, role="assistant", content="Ждём вас! До встречи"),

            ChatLog(user_id=users[2].id, role="user", content="Во сколько вы работаете?"),
            ChatLog(user_id=users[2].id, role="assistant", content="Мы работаем ежедневно с 10:00 до 23:00. Ждём вас!"),

            ChatLog(user_id=users[3].id, role="user", content="Можно 3 люля-кебаб из баранины и 2 лепёшки?"),
            ChatLog(user_id=users[3].id, role="assistant", content="Конечно! Ваш заказ:\n  - Люля-кебаб из баранины x 3 — 4 470 ₸\n  - Лепёшка x 2 — 700 ₸\n\nИтого: 5 170 ₸"),
        ]
        db.add_all(chat_logs)
        await db.flush()
        print(f"[OK] Создано {len(chat_logs)} сообщений в чатах")

        await db.commit()

    print("\n[OK] Все демо-данные загружены!")
    print("   Запуск: python -m uvicorn app.main:app --reload")
    print("   Браузер: http://localhost:8000")


if __name__ == "__main__":
    asyncio.run(seed())
