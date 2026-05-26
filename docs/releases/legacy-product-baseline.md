# RestoMind — базовый продуктовый срез (legacy)

Снимок возможностей ранней версии платформы (до OS Transition). Актуальная карта кода — [codebase.md](../codebase.md).

---

## Ядро (AI + диалог)

- AI на OpenAI (structured output по Pydantic-схеме; Whisper для голоса)
- System Prompt, RAG — меню из БД в контексте
- История в Redis (до 20 сообщений, TTL 24 ч)
- Intents: `order`, `book`, `faq`, `escalate`
- Retry до 2 попыток + fallback на `escalate`
- **Rate limiting** по телефону (Redis / in-memory)

## Заказы

- Цикл: клиент → AI → валидация → DRAFT → подтверждение → confirmed → iiko → `sent_to_iiko`
- Валидация по `MenuItem`, fuzzy matching (difflib, cutoff 0.6)
- `iiko_item_id`, модификаторы, исключения ингредиентов
- Защита от пустого заказа (все позиции неизвестны → DRAFT не создаётся)
- `OrderStatus`: DRAFT → CONFIRMED → SENT_TO_IIKO → COMPLETED / CANCELLED

## State machine

- Состояния: `CHATTING`, `CONFIRMING_ORDER`, `CONFIRMING_BOOKING`, `HUMAN_MODE`
- Pending order и pending booking в Redis (TTL 24 ч)
- Подтверждение заказа и брони словами «Да» / «Нет» (ru/en)

## Бронирование

- Парсинг даты/времени/гостей через AI
- Flow: черновик → резюме → подтверждение клиентом → confirmed / cancelled

## Human override

- Takeover / release / `send_message` от оператора
- Логи чата для user / assistant / operator

## Интеграция iiko

- Токен, номенклатура, создание доставки, стоп-листы
- Синхронизация меню bulk, фоновая синхронизация стоп-листов (~15 мин)

## Интеграция WhatsApp

- Верификация webhook, POST → BackgroundTasks
- Текстовые сообщения + **template messages** (`send_template`)
- Retry, режим разработки без токена (лог в консоль)

## Real-time (WebSocket)

- Redis Pub/Sub, in-memory fallback
- Эндпоинт `/api/admin/ws`, события: `new_message`, `order_updated`, `human_needed`, `state_changed`
- Публикация из вебхука, intent router, админских действий

## Админ-панель

- Alpine.js + Tailwind + Chart.js
- Live-обновления, канбан заказов, live-чаты, алерты `human_needed` (звук)
- Дашборд, аналитика, меню, синхронизация, test-bot
- **Вход:** форма + cookie-сессия; REST и демо-API с `credentials`; WebSocket с подписанным `ws_token`

## База данных

- SQLAlchemy 2.0 async, PostgreSQL / SQLite (`DB_MODE`)
- Модели: **Organization**, User, Order, ChatLog, Booking, MenuItem
- Мультитенантность: `organization_id` на User, Order, Booking, MenuItem
- Redis + in-memory fallback; `seed.py` с демо-данными

## Обработка ошибок

- AI / iiko / WhatsApp: retry и логирование
- Webhook: защита от каскадных 500
- `process_message`: глобальный try/except

## Логирование и мониторинг

- Консоль + ротируемые файлы + отдельный error-log
- Sentry по `SENTRY_DSN` (опционально)

## Тесты

- `tests/test_order_logic.py` — `validate_order`, меню, контекст
- `tests/test_ai_brain.py` — мок OpenAI, fallback, retry
- `tests/test_rate_limiter.py` — лимиты
- Фикстуры: SQLite in-memory, `db_with_menu`

## Инфраструктура

- Dockerfile, `docker-compose.yml`, **`docker-compose.prod.yml`** (Traefik, Let's Encrypt)
- `DEPLOY_GUIDE.md`, `.env.example`, `requirements.txt` (+ pytest)
- Маршруты `/` и `/admin`

## Голосовой AI (v2.0 — заготовка)

- Модуль `telephony.py`: контракты под Twilio / STT / TTS, без боевого эндпоинта

## Оптимизации

- Один запрос меню на цикл сообщения
- Аналитика на уровне SQL
- Bulk load при синхронизации из iiko
- Pub/Sub вместо polling в админке
