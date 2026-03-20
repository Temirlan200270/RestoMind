# 🤖 Project: Restaurant AI Agent (WhatsApp & iiko Integration)

## 📌 Контекст проекта
Разработка интеллектуального виртуального оператора для ресторана. 
Бот общается с клиентами в WhatsApp, понимает естественную речь, отвечает на вопросы по меню, формирует заказы (с учетом модификаторов и исключений) и бронирует столы. 
**MVP фокус:** Текстовый бот (WhatsApp), интеграция с OpenAI, базовая бизнес-логика заказов, простая админ-панель. Телефония (Voice) исключена из MVP.

## 🛠 Технологический стек
- **Backend:** Python 3.11+, FastAPI
- **Database:** PostgreSQL (ORM: SQLAlchemy 2.0 + Alembic для миграций)
- **Cache/Session:** Redis (для хранения контекста диалогов)
- **AI / LLM:** OpenAI API (GPT-4o) с использованием `client.beta.chat.completions.parse` для гарантии Pydantic-схем.
- **Интеграции:** Meta WhatsApp API (или Twilio), iiko API (в MVP эмулируем).
- **Валидация:** Pydantic V2

## 🏗 Архитектура проекта (Директории)
Cursor, строго придерживайся этой структуры при создании файлов:

```text
rest_ai_agent/
├── app/
│   ├── api/                # Роутеры (FastAPI)
│   │   ├── webhooks.py     # Входящие от WhatsApp
│   │   └── admin.py        # API для фронтенда админки
│   ├── core/               # Конфиги (Pydantic BaseSettings)
│   ├── db/                 # PostgreSQL (сессии, модели)
│   ├── schemas/            # Pydantic модели (схемы AI, API запросы/ответы)
│   ├── services/           # Бизнес-логика (AI, заказы, диалоги)
│   ├── integrations/       # Клиенты внешних API (WhatsApp, iiko)
│   └── main.py             # Точка входа (FastAPI app)
├── .env                    # Секреты
├── requirements.txt        
└── plan.md         # Этот файл
  
🚨 Строгие правила для написания кода (Rules for Cursor AI)
Async First: Все I/O операции (база данных, Redis, API вызовы к OpenAI и WhatsApp) должны быть строго асинхронными (async/await).
Non-blocking Webhooks: Эндпоинт вебхука WhatsApp обязан возвращать 200 OK моментально. Вся логика общения с LLM должна передаваться в BackgroundTasks (FastAPI).
Structured Outputs: Для OpenAI API ВСЕГДА использовать метод .parse() и передавать Pydantic модели. Никакого парсинга JSON вручную через регулярки.
Thin Routers: Роутеры в app/api/ должны содержать только прием запроса и вызов сервиса. Вся логика ветвления находится в app/services/.
No Hallucinations with Prices: ИИ не имеет права сам придумывать цены. Использовать RAG или контекст меню.
🚀 Пошаговый план разработки (Roadmap)
Фаза 1: Базовый сетап и Webhook (Foundation)

Инициализировать FastAPI приложение в app/main.py.

Настроить app/core/config.py для чтения .env (токены БД, OpenAI, Redis).

Создать базовые SQLAlchemy модели в app/db/models.py:
User (phone, name)
Order (user_id, status, items_json, total_price)
ChatLog (user_id, role, content)

Настроить подключение к PostgreSQL и Redis.

Создать app/api/webhooks.py с эндпоинтом /whatsapp, который принимает запрос, логирует его и через BackgroundTasks передает в заглушку функции process_message.
Фаза 2: Интеллектуальное ядро (AI Brain & Structured Outputs)

Создать app/schemas/ai_schemas.py с моделями Pydantic для структурированного ответа ИИ:
OrderItem (name, quantity, exclude_ingredients)
BookingDetails (date, time, guests)
AIBrainResponse (intent: Literal['order', 'book', 'faq', 'escalate'], reply_text, items, booking_details)

Написать System Prompt в формате Markdown (сохранить как константу в app/services/prompts.py).

Создать app/services/ai_brain.py с функцией call_openai(history, user_text), которая использует client.beta.chat.completions.parse и возвращает объект AIBrainResponse.
Фаза 3: Менеджер диалогов и Память (State Management)

Реализовать функции в app/services/dialog_mgr.py для работы с Redis:
get_chat_history(phone) -> list (получить последние 10 сообщений).
append_to_history(phone, role, text) (добавить сообщение юзера/бота с TTL 24 часа).

Связать вебхук -> извлечение истории -> вызов ai_brain -> сохранение ответа в историю.

Создать заглушку клиента WhatsApp в app/integrations/whatsapp.py (send_message(phone, text)). В MVP можно просто принтовать в консоль.
Фаза 4: Бизнес-логика маршрутизации (The Gateway)

В app/services/dialog_mgr.py реализовать маршрутизатор на основе intent, полученного от AI:
if intent == 'order': передать items в app/services/order_logic.py для валидации по локальному словарю меню (заглушка для iiko).
if intent == 'book': сохранить бронь в БД.
if intent == 'escalate': отправить уведомление админу.
Во всех случаях: отправить reply_text пользователю через whatsapp.py.
Фаза 5: Интеграция iiko (Основы) & Админка

Создать app/integrations/iiko_client.py (пока реализовать только авторизацию /api/1/access_token и получение номенклатуры /api/1/nomenclature).

Сделать крон-джобу (или эндпоинт для ручного триггера), которая скачивает меню из iiko и сохраняет/обновляет его в PostgreSQL (таблица MenuItem).

Создать app/api/admin.py с простыми REST эндпоинтами для фронтенда:
GET /admin/orders (список заказов)
GET /admin/chats/{phone} (просмотр диалога)
POST /admin/menu/sync (принудительно забрать меню из iiko)

# 🤖 Project: Restaurant AI Agent — Часть 2 (Production & iiko Deep Dive)

## 📌 Статус проекта
Фазы 1-5 (MVP фундамент) успешно завершены. 
**Текущая архитектура работает:** Webhook -> Redis State -> OpenAI (Structured Outputs) -> Router -> DB. 
**Цель Части 2:** Реализовать сложную логику корзины, точный мэтчинг блюд с ID iiko, работу с модификаторами, подтверждение заказа и перехват диалога живым оператором.

## 🚨 Новые строгие правила для Cursor (Rules v2)
1. **iiko UUIDs Only:** При формировании финального заказа для Айко НИКОГДА не использовать текстовые названия блюд. Всегда производить поиск и привязку к `id` (UUID) из локальной таблицы номенклатуры.
2. **State Machine Корзины:** Заказ не отправляется в Айко сразу после интента `order`. Он должен перейти в статус `DRAFT`, ИИ должен запросить подтверждение у клиента ("Ваш заказ на сумму Х руб. Подтверждаете?").
3. **Mute AI Action:** Если диалог переведен на оператора, ИИ должен "замолчать" (проверять флаг в Redis перед обращением к OpenAI).

---

## 🚀 Пошаговый план разработки (Часть 2)

### Фаза 6: RAG и Мэтчинг блюд (Семантический поиск)
*Проблема: Клиент пишет "дай колу и маргариту", а в базе iiko это "Coca-Cola 0.5 стекло" (UUID: xxxx) и "Пицца Маргарита 30см" (UUID: yyyy).*
- [ ] Добавить в `app/services/ai_brain.py` логику: перед тем как отдать запрос в OpenAI, делать быстрый поиск по локальной БД `MenuItem` и собирать мини-каталог доступных блюд (названия + UUID + цены), передавая его в системный промпт ИИ как контекст.
- [ ] Обновить Pydantic модель `OrderItem` в `app/schemas/ai_schemas.py`: добавить обязательное поле `iiko_item_id: str` и опциональный список `modifiers_ids: List[str]`.
- [ ] **Тест:** Заставить ИИ возвращать не просто "Маргарита", а правильный `iiko_item_id` из контекста.

### Фаза 7: Управление Корзиной и State Machine (Dialog Manager)
- [ ] В `app/services/dialog_mgr.py` добавить логику состояний пользователя (хранить в Redis): `CHATTING`, `ORDERING`, `CONFIRMING_ORDER`, `HUMAN_MODE`.
- [ ] Реализовать флоу создания заказа:
  1. ИИ выдал intent `order` с массивом `OrderItem`.
  2. Бэкенд создает запись в БД `Order` со статусом `DRAFT`.
  3. Бэкенд считает итоговую сумму (из локальной БД).
  4. Бот отправляет сообщение: *"Ваш заказ: ... Сумма: ... Всё верно? (Да/Нет)"*.
  5. Переводим юзера в состояние `CONFIRMING_ORDER`.
- [ ] Обработать ответы "Да" (переход к Фазе 8) и "Нет" (отмена/редактирование корзины).

### Фаза 8: Отправка заказа в iiko (Доставка/Самовывоз)
- [ ] В `app/integrations/iiko_client.py` написать метод `create_delivery_order(order_data: dict)`.
  - Эндпоинт: `POST /api/1/deliveries/create`.
  - Реализовать сборку JSON-тела заказа (передача `organizationId`, `customer`, `items` с `productId` и `modifiers`, `orderTypes`).
- [ ] Если клиент подтвердил корзину (состояние `CONFIRMING_ORDER` -> "Да"):
  - Вызываем `create_delivery_order`.
  - Меняем статус заказа в БД на `SENT_TO_IIKO`.
  - Пишем клиенту: *"Заказ успешно передан на кухню!"*.

### Фаза 9: Система перехвата (Human Override)
- [ ] В `app/services/dialog_mgr.py` при входе в `process_incoming_message` добавить проверку: `is_human_mode = redis.get(f"human_mode:{phone}")`.
- [ ] Если `is_human_mode == True`:
  - **НЕ вызывать** `ai_brain.py`.
  - Просто сохранять сообщение в `ChatLog` (чтобы админ видел его в веб-интерфейсе).
- [ ] В `app/api/admin.py` добавить эндпоинты:
  - `POST /admin/chats/{phone}/takeover` (Устанавливает флаг `human_mode` в Redis в True).
  - `POST /admin/chats/{phone}/release` (Возвращает бота, `human_mode` -> False).
  - `POST /admin/chats/{phone}/send_message` (Позволяет админу написать клиенту в WhatsApp вручную).

### Фаза 10: Интеграция стоп-листов (Out-of-Stock)
- [ ] В `app/integrations/iiko_client.py` добавить метод `get_stop_lists()`.
  - Эндпоинт: `POST /api/1/stop_lists`.
- [ ] Создать фоновую задачу (celery/cron или FastAPI `apscheduler`), которая раз в 15 минут запрашивает стоп-листы и ставит флаг `is_available = False` в БД `MenuItem`.
- [ ] Строго прописать в System Prompt ИИ: *"Никогда не предлагай и не добавляй в заказ блюда, у которых is_available=False"*.