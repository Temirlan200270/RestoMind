# Changelog

Заметные изменения проекта **RestoMind**. Формат близок к [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/).

---

## [Unreleased] — 2026-03-20

### Добавлено

- **Деплой на Render:** `render.yaml` (Blueprint: Web Service + PostgreSQL `free`), `DEPLOY_RENDER.md`, `docs/VERCEL.md`; в конфиге поддержка `DATABASE_URL` (managed Postgres) и `CMD` Docker с `PORT`; миграции через `preDeployCommand: alembic upgrade head`.
- **Форма входа в админку** — полноэкранный экран до авторизации; сессия в cookie (`SessionMiddleware`), API `POST /api/admin/auth/login`, `GET /api/admin/auth/me`, `POST /api/admin/auth/logout`.
- **WebSocket-токен** — подпись `itsdangerous` (`/api/admin/auth/login` и `/auth/me` отдают `ws_token`), query `?token=` на `/api/admin/ws`.
- **Демо-данные** — кнопки «Демо-данные» / «Удалить демо» в шапке; `GET/POST/DELETE /api/admin/demo*`, префикс телефонов `demo7700…`.
- **Сессии:** `SESSION_SECRET` (опционально), cookie `restomind_admin`, зависимость `itsdangerous`.
- **Rate limiting** по номеру телефона: скользящее окно 60 с, Redis или in-memory; `RATE_LIMIT_PER_MINUTE`.
- **Логи в файлы:** `logs/restomind.log`, `logs/errors.log` с ротацией (`RotatingFileHandler`).
- **Sentry:** опционально `SENTRY_DSN` (если установлен `sentry-sdk`).
- **WhatsApp:** `send_template()` для одобренных шаблонов Meta (проактивные уведомления).
- **Бронирование:** состояние `CONFIRMING_BOOKING`, черновик → подтверждение «Да»/«Нет» → `confirmed` / `cancelled`.
- **Мультитенантность (фундамент):** модель `Organization`; `organization_id` у **User, Order, Booking, MenuItem** (nullable). У **ChatLog** отдельного поля нет — связь с организацией через пользователя.
- **Telephony (v2.0 заготовка):** `app/integrations/telephony.py` — интерфейсы STT/TTS, `TelephonyRouter`, `CallSession`.
- **Тесты:** pytest + pytest-asyncio, **20** unit-тестов в `tests/`.
- **CI/CD:** `.github/workflows/ci.yml` (pytest + проверка импорта приложения), `deploy.yml` (SSH + docker compose, секреты `DEPLOY_*`).
- **Меню без ручного seed:** при старте приложения, если в БД **нет ни одной позиции**, автоматически загружается встроенный каталог (`app/services/menu_bootstrap.py`, тот же состав, что в `seed.py`). Повторять при каждом запуске **не нужно** — только при пустой таблице.
- **Редактирование меню в админке:** `POST/PATCH/DELETE /api/admin/menu` — добавление позиции, правка цены/названия/раздела/описания/URL фото/наличия, удаление; во вкладке «Меню» — кнопки «Добавить», «Изменить», переключатель «В наличии / Стоп» на карточке.
- **Демо из админки (`POST /api/admin/demo/seed`):** больше нагрузочных данных — 5 демо-клиентов, 12 заказов с разными датами/статусами, 6 броней, расширенные диалоги; если **меню пустое**, подгружается полный список из `PLOVXANA_MENU_ITEMS` (~137 позиций). Повторный seed при уже существующих демо-клиентах: **409** как раньше, но если меню было пустым — **200** с `partial` и добавлением меню.
- **Кабина оператора (Диалоги):** правая колонка — сводка по клиенту (`GET /api/admin/customers/{phone}/summary`), быстрые ответы, заметка `operator_note` (`POST /api/admin/customers/{phone}/note`); поле `User.operator_note` в БД.
- **Звук в админке:** короткие сигналы Web Audio при входящем сообщении клиента (другой чат или вкладка в фоне) и при появлении нового заказа по WebSocket.

### Изменено

- **Админка «Меню»:** чипы разделов — **flex-wrap** вместо горизонтального скролла; при **>20** категорий — подписи **«Кухня»** и **«Бар и напитки»**; у чипов — счётчик позиций; липкая панель поиска/фильтров (`z-10`, blur); индикатор **«N в стопе»** (клик → фильтр «Нет в наличии»); режим **«Выбрать»** с чекбоксами и панелью **«В стоп / В продажу»** для выделенных; подсказка на бейдже наличия (переключение без модалки).
- **Аналитика** (`GET /api/admin/analytics`): границы периода и «сегодня» считаются в **UTC**, как у дашборда (`/stats`), чтобы цифры не расходились между вкладками.
- **Админка (дашборд):** градиент под мини-графиком Chart.js через scriptable `backgroundColor` (корректно при первом рендере и resize); подпись «Нет данных за вчера» только после успешной загрузки `daily_series`.
- **Админка «Диалоги»:** список + чат + **инфо-панель** (lg+), двухпанельный скролл списка/ленты, премиум-баблы (клиент слева / ИИ и менеджер справа), textarea + Enter / Shift+Enter, `toggleTakeover`, автоскролл ленты, время превью в списке (`lastAt`), имя из `GET /chats/{phone}`.
- **Админка — вёрстка высоты:** колонка приложения `h-[100dvh]` + flex (`flex-1 min-h-0`); алерт «Бот просит помощи» **в потоке** под шапкой (не `fixed`), чтобы при его появлении область чата сжималась и инпут не уезжал за экран; убран `calc()` у чатов; навигация сайдбара с `overflow-y-auto`.
- `.env.example` — админка, лимиты, Sentry.
- `.dockerignore` — `logs/`, `tests/`.

### Исправлено

- **Админка — графики (Chart.js):** гонка между `requestAnimationFrame` и `resize` по таймеру — график иногда создавался с canvas 0×0 или `resize` вызывался до создания экземпляра. `scheduleDashboardChartRender` и загрузка аналитики теперь **await** отрисовки; повторный `resize` через **150 / 400 / 800 ms** после смены вкладки.
- **Аналитика / дашборд:** демо-заказы с `days_ago` 7–21 не попадали в окно «последние 7 дней» — мини-график «Динамика» и «Сегодня» выглядели пустыми; даты демо и `seed.py` переведены на **0–6 дней UTC** от полуночи дня сида. **SQLite:** фильтры по `orders.created_at` используют **naive UTC** (`_sql_dt_for_filter`), чтобы «Сегодня» и периоды в `/analytics` не отдавали 0 строк. Подписи мини-графика — `T12:00:00Z` (как на аналитике).
- **Критично:** в `admin.html` у функции `renderChart()` был **не закрыт `try`** (без `catch`) — синтаксическая ошибка JS, из‑за неё не поднимался Alpine, страница оставалась белой (`x-cloak`). Исправлено.
- **Графики выручки (дашборд и аналитика):** дневная разбивка в `GET /api/admin/analytics` строится по **календарным дням UTC** без ошибки `range(num_days+1)`; ключ дня для заказов — дата в UTC (naive datetime из SQLite считается UTC). **Chart.js:** при `x-show` вкладка до показа имеет `display:none` — контейнер 0×0; добавлены `ResizeObserver`, повторные `resize()`/`update('none')`, вызов после `loadTabData`, проверка `res.ok` для `/stats` и `/analytics`, обмен дат в custom-периоде если «с–по» перепутаны. Сброс пользовательского периода при каждом заходе на «Аналитику» убран; на графике аналитики — выбор **«Только выручка / только заказы / вместе»**.
- **`GET /api/admin/stats` — `daily_series`:** вместо семи SQL-фильтров по суткам (на SQLite с naive `created_at` и границами UTC график часто был из нулей) — одна выборка заказов и **корзины по дням в Python** по тому же ключу UTC, что и аналитика. **Демо и `seed.py`:** `created_at` заказов пишется в **UTC** (`datetime.now(timezone.utc)`), чтобы совпадать с KPI «сегодня/вчера».
- **Админка «Заказы»:** подпись «Канбан» заменена на **«По этапам»**, второй режим — **«Список»** (понятнее, чем дважды «таблица»).
- **Админка «Меню»:** позиции без категории попадали в группу «Прочее», но она не входила в `menuCategories` — карточки не строились (пустой экран при живых данных). Исправлена сборка `menuDisplayGroups`; в фильтрах учитывается «Прочее»; при ошибке API показывается сообщение.
- **Удаление демо-данных:** явные SQL `DELETE` (чаты → брони → заказы → пользователи), очистка Redis/InMemory-ключей сессии по демо-номерам; для SQLite при каждом подключении `PRAGMA foreign_keys=ON`.
- **UI удаления демо:** модальное окно с чекбоксом и тостом вместо `confirm()`.

---

## Ядро (AI + диалог)

- AI на Google Gemini 2.5 Flash с Structured Outputs (Pydantic-схема)
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
- `tests/test_ai_brain.py` — мок Gemini, fallback, retry
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
