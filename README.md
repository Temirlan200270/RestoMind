# RestoMind OS

**AI-операционная система для ресторанного бизнеса.** Единое ядро управления продажами, маркетингом и операционкой: гость пишет в **WhatsApp**, ответы формирует LLM по структурированной схеме (`AIBrainResponse`); голос — **Whisper**; заказы синхронизируются с **iiko**; аналитика и рекомендации — в Admin-панели владельца.

Подробный список изменений и возможностей — в [CHANGELOG.md](CHANGELOG.md). Правила разработки (инварианты) — в [docs/CONVENTIONS.md](docs/CONVENTIONS.md). **Дерево проекта и суть кодовой базы** — в [codebase.md](codebase.md). Стратегический план перехода → OS — в [docs/OS_TRANSITION_PLAN.md](docs/OS_TRANSITION_PLAN.md).

## Архитектура ядра (OS Layers)

| Слой | Суть | Статус |
|------|------|--------|
| **Tenant Isolation** | Каждый запрос фильтруется по `organization_id`; legacy NULL только default org; backfill API | **~95%** |
| **Event-Driven Core** | Бизнес-действия порождают события (`SystemEvent`, `emit_event`); аналитика из `DailyOrgStats` + backfill | **~98%** |
| **AI Context Snapshot** | `fetch_ai_read_context` → `AIReadContext`; данные для LLM готовит слой `ContextBuilder`, не raw SQL | ~100% |

Детальная карта переходов — [`docs/OS_TRANSITION_PLAN.md`](docs/OS_TRANSITION_PLAN.md).

## Модули (Add-ons)

Система расширяется независимыми модулями поверх единого ядра:

- **Ordering** — приём заказов, стоп-листы, iiko-интеграция (реализован)
- **GuestCare** — сбор отзывов, авто-ответы, агрегация из 2GIS/Google (частично)
- **Marketing** — сегментированные рассылки, лояльность, бонусы (реализован)
- **Intelligence** — AI-аналитика, инсайты, рекомендации владельцу (реализован)
- **SupplyMind** — AI-закупки и Foodcost (дорожная карта)

## Возможности

- **Приём заказов** — распознавание блюд из меню, подтверждение «Да»/«Нет»; тип получения и оплата (v2); контейнеры и доставка считаются автоматически (`PRICING_*` в `.env`); после согласия клиента в WhatsApp заказ подтверждается и ждёт оператора — **в iiko** отправляется только из админки («Подтвердить и печать» / канбан)
- **Бронирование** — дата, время, гости; подтверждение «Да»/«Нет» (`CONFIRMING_BOOKING`)
- **FAQ** — часы, адрес, состав, аллергены
- **Human Override** — перехват диалога оператором; в режиме оператора AI не отвечает
- **Стоп-листы** — фоновая синхронизация из iiko (~15 мин)
- **Админ-панель** — вход по логину/паролю (cookie-сессия), дашборд, канбан, live-чаты, WebSocket, аналитика, редактирование меню, демо-данные
- **Demo Pitch (G10.8)** — **«Посмотреть демо»** или **`GET /demo`** (zero-friction): 30-сек pitch → read-only осмотр; см. [`docs/DEMO_PITCH.md`](docs/DEMO_PITCH.md)
- **Надёжность** — rate limiting по телефону, логи в файлы, опционально Sentry
- **Мультитенантность (фундамент)** — модель `Organization`, поле `organization_id` у сущностей (см. CHANGELOG)

## Стек

| Компонент | Технология |
|-----------|------------|
| Backend | Python 3.11+, FastAPI |
| Database | PostgreSQL / SQLite (dev), SQLAlchemy 2.0, **Alembic** |
| Cache / очередь | Redis; **ARQ worker** обрабатывает входящие WhatsApp и фоновые задачи (см. ниже) |
| AI | OpenAI (`gpt-4o-mini`, env `OPENAI_MODEL`) или Gemini (`AI_PROVIDER=gemini`, `GEMINI_API_KEY`); structured output + Whisper (`OPENAI_TRANSCRIPTION_MODEL`); опц. `OPENAI_BASE_URL` |
| Интеграции | Meta WhatsApp API, iiko Cloud API |
| Админка | Jinja2 + Alpine.js + Tailwind CSS + Chart.js |
| Тесты | pytest, pytest-asyncio (`tests/`, порядка 200 тестов — см. CI) |
| Продакшен | Docker; **Render** ([DEPLOY_RENDER.md](DEPLOY_RENDER.md)); либо VPS + [DEPLOY_GUIDE.md](DEPLOY_GUIDE.md) |

## Быстрый старт

### 1. Клонировать и установить зависимости

```bash
git clone <repo-url>
cd RestoMind
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # Linux/macOS
pip install -r requirements.txt
```

### 2. Настроить окружение

```bash
copy .env.example .env
```

**Обязательно для локальной работы:** `OPENAI_API_KEY`.  
**Админка:** `ADMIN_USERNAME`, `ADMIN_PASSWORD` (legacy-вход); для cookie и подписи WebSocket-токена задайте **`SESSION_SECRET`** (случайная строка, в проде — не пустая).  
**Super Admin (legacy по env, опционально):** `SUPERADMIN_USERNAME`, `SUPERADMIN_PASSWORD` — вход в админку с `is_superadmin=true` без StaffUser (удобно для быстрого доступа к `/superadmin`).
Остальное — по необходимости (WhatsApp, iiko, Redis, `SENTRY_DSN`, `RATE_LIMIT_PER_MINUTE` — см. [.env.example](.env.example)).

**Telegram — SOS персоналу (опционально):** при запросе оператора (`intent: escalate`) или временном сбое AI в Telegram уходит карточка с номером гостя и кнопкой «Открыть диалог в админке». В `.env` / Render: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ADMIN_CHAT_ID` (id группы или пользователя). Для отдельного чата на филиал — поле **Telegram: чат персонала** в админке (**Настройки → Мой ресторан**), иначе используется глобальный `TELEGRAM_ADMIN_CHAT_ID`. Чтобы ссылка в кнопке была кликабельной, задайте **`PUBLIC_BASE_URL`** (полный `https://…` без `/admin`).

### 3. База данных и демо

**Важно:** команды выполняйте **из корня проекта** (где лежит `seed.py`).

Для **PostgreSQL** после настройки `.env` примените миграции:

```bash
alembic upgrade head
```

Для **SQLite** (режим по умолчанию) таблицы создаются при старте приложения.

```bash
python seed.py
```

`seed.py` **пересоздаёт таблицы** и заливает полное меню + заказы/чаты (полный сброс локальной БД).

**Меню:** встроенный демо-каталог при старте **не** подставляется — номенклатура только из **iiko Cloud** (`python scripts/sync_menu_from_iiko.py` или кнопка в админке). Правки — во вкладке **«Меню»** админки.

В админке кнопка **«Демо-данные»** (Настройки → Техническое) добавляет пользователей с префиксом `demo7700…` и **не стирает** остальное; меню из демо не создаётся. Demo-org **seed'ится при старте приложения** (см. `app/main.py`); кнопка «Демо-данные» — ручной re-seed для staff-сессии.

**Sales demo:** на экране входа — **«Посмотреть демо»** (не путать с «Демо-данные» в настройках). Полный сценарий — [`docs/DEMO_PITCH.md`](docs/DEMO_PITCH.md).

Синхронизация номенклатуры из **iiko Cloud** в таблицу `menu_items` (нужны `IIKO_API_LOGIN` и `IIKO_ORGANIZATION_ID` в `.env`): из корня проекта выполните `python scripts/sync_menu_from_iiko.py` (то же действие, что кнопка синхронизации в админке).

### 4. Запустить сервер

```bash
python -m uvicorn app.main:app --reload
```

Откройте в браузере: [http://localhost:8000](http://localhost:8000) или [http://localhost:8000/admin](http://localhost:8000/admin) — форма входа, затем панель.

### 5. Тестирование бота (без WhatsApp)

Эндпоинт защищён сессией админки. Удобнее всего: зайти в [http://localhost:8000/docs](http://localhost:8000/docs) → **Authorize** не нужен для cookie, но проще вызвать `POST /api/admin/auth/login`, затем `POST /api/admin/test-bot` из той же сессии браузера.

Через **curl** (подставьте логин/пароль из `.env`):

```bash
curl -c cookies.txt -X POST http://localhost:8000/api/admin/auth/login \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"admin\",\"password\":\"ВАШ_ПАРОЛЬ\"}"

curl -b cookies.txt -X POST http://localhost:8000/api/admin/test-bot \
  -H "Content-Type: application/json" \
  -d "{\"text\": \"Хочу два плова и капучино\"}"
```

## Продакшен

| Платформа | Документ |
|-----------|----------|
| **Render** (Web Service; PostgreSQL — Supabase или другой хост, см. `DATABASE_URL`) | **[DEPLOY_RENDER.md](DEPLOY_RENDER.md)**, миграция БД — **[docs/SUPABASE_MIGRATION.md](docs/SUPABASE_MIGRATION.md)** |
| **Свой сервер** (Docker + Traefik + HTTPS) | **[DEPLOY_GUIDE.md](DEPLOY_GUIDE.md)** |
| **Vercel** | Этот бэкенд на Vercel не рассчитан; см. [docs/VERCEL.md](docs/VERCEL.md) |

Автоматически задеплоить в ваш аккаунт нельзя — нужен ваш git-репозиторий и вход в Render. Плагины Vercel/Render в IDE только помогают связать проект; шаги — в таблице выше.

Кратко для продакшена: `APP_DEBUG=false`, PostgreSQL, секреты `OPENAI_API_KEY`, админка, `SESSION_SECRET` / `generateValue` на Render, токены WhatsApp; **Redis обязателен** для очереди; **`REDIS_ENABLED=true`**, **`ARQ_ENABLED=true`**, задайте **`REDIS_URL`** (или host/port). Отдельным процессом поднимите worker:

```bash
python -m arq app.worker.WorkerSettings
```

Имя очереди по умолчанию — `restomind` (`ARQ_QUEUE_NAME`). В `APP_ENV=production|staging` приложение при старте проверяет, что к Redis можно подключиться и ARQ включён; без worker задачи из вебхуков не выполнятся.
Worker слушает ту же очередь, что и web-процесс использует для `enqueue_job`; статус можно проверить в админке через `GET /api/admin/system/task-queue-health`.

## Структура проекта

Актуальное **дерево каталогов**, потоки данных и точки входа — в **[codebase.md](codebase.md)**. Ниже — краткая схема.

```
RestoMind/
├── app/
│   ├── api/           # admin/ (пакет), webhooks.py, payment_webhook.py, superadmin.py
│   ├── core/          # config, rate_limiter
│   ├── db/            # models, session (async + Redis)
│   ├── integrations/  # whatsapp, iiko, telegram, twilio…
│   ├── schemas/       # ai_schemas и др.
│   ├── services/      # бизнес-логика (ai_brain, dialog_mgr, intent_router, …)
│   ├── templates/     # Jinja2 + components
│   ├── static/        # admin-app.js, CSS
│   └── main.py
├── alembic/
├── tests/
├── scripts/
├── .github/workflows/
├── codebase.md        # обзор репозитория для онбординга
├── docs/CONVENTIONS.md
├── CHANGELOG.md
├── DEPLOY_*.md
└── docs/
```

Полезные документы:

| Документ | Назначение |
|----------|------------|
| [docs/ROADMAP.md](docs/ROADMAP.md) | Единый трекер задач и техдолга (P0–P4), индекс wishlist |
| [CHANGELOG.md](CHANGELOG.md) | Журнал изменений |
| [docs/CONVENTIONS.md](docs/CONVENTIONS.md) | Инварианты разработки + §8 шаблоны Jinja (`_tab_*`) и миграции/SQLite |
| [docs/UI_DESIGN_SYSTEM.md](docs/UI_DESIGN_SYSTEM.md) | Дизайн-система админки (`ds-*`, a11y, Lighthouse) |
| [docs/UI_MAP.md](docs/UI_MAP.md) | Карта экранов и компонентов админки |
| [docs/AI_TOOLS_SETUP.md](docs/AI_TOOLS_SETUP.md) | Cursor / Claude Code / MCP |
| [docs/AI_OPERATIONS.md](docs/AI_OPERATIONS.md) | Intelligence, инсайты, операционные сценарии |
| [docs/EVENT_ARCHITECTURE.md](docs/EVENT_ARCHITECTURE.md) | Durable-события и аналитический пайплайн |
| [docs/ui/mobile-review/README.md](docs/ui/mobile-review/README.md) | Mobile review (Playwright-скриншоты) |
| [docs/ui/lighthouse/README.md](docs/ui/lighthouse/README.md) | Lighthouse для админки (`npm run lh:admin`) |
| [docs/DEMO_PITCH.md](docs/DEMO_PITCH.md) | 30-сек sales demo: pitch / explore, API, smoke |
| [docs/SUPERADMIN_GUIDE.md](docs/SUPERADMIN_GUIDE.md) | Панель superadmin |
| [docs/SUPABASE_MIGRATION.md](docs/SUPABASE_MIGRATION.md) | Миграция БД на Supabase |
| [docs/WHATSAPP_PHASE13_TEMPLATES.md](docs/WHATSAPP_PHASE13_TEMPLATES.md) | Шаблоны WhatsApp (Meta) |

## API (кратко)

Полный перечень см. в OpenAPI: [http://localhost:8000/docs](http://localhost:8000/docs) после запуска.

### Авторизация админки (без cookie-сессии)

| Метод | Путь | Описание |
|-------|------|----------|
| POST | `/api/admin/auth/login` | вход, сессия + `ws_token` |
| POST | `/api/admin/auth/demo-login` | гостевой вход в демо-организацию (read-only) → autoplay pitch |
| GET | `/api/admin/demo/shift-scenes` | каталог 30-сек demo-сцен |
| GET | `/api/admin/demo/shift-scene/{id}/state?phase=` | canned shift/state для pitch (без мутаций БД) |
| POST | `/api/admin/auth/request-access` | заявка на подключение ресторана (pending moderation) |
| POST | `/api/admin/auth/signup` | отключён (`410`, регистрация только по заявке) |
| POST | `/api/admin/auth/logout` | выход |
| GET | `/api/admin/auth/me` | проверка сессии, перевыпуск `ws_token` |

### Super Admin API (только `is_superadmin=true`)

| Метод | Путь | Описание |
|-------|------|----------|
| GET | `/api/superadmin/organizations` | список заведений + KPI за 30 дней |
| POST | `/api/superadmin/organizations` | ручное создание ресторана и первого admin |
| PATCH | `/api/superadmin/organizations/{id}/status` | блокировка/разблокировка за неуплату |
| PATCH | `/api/superadmin/organizations/{id}/credentials` | тех. настройки (iiko/WhatsApp/Telegram) |
| POST | `/api/superadmin/organizations/{id}/sync-menu` | форс-синхронизация меню из iiko |
| GET | `/api/superadmin/registration-requests` | список заявок |
| POST | `/api/superadmin/registration-requests/{id}/approve` | одобрение заявки с созданием org+staff |
| POST | `/api/superadmin/registration-requests/{id}/reject` | отклонение заявки |

### Защищённый Admin API (после входа)

Включая: заказы, брони, чаты, `GET /api/admin/customers/{phone}/summary`, заметка оператора, меню (CRUD + sync + стоп-листы), `GET /api/admin/stats`, `GET /api/admin/analytics`, `GET /api/admin/ai-value`, демо-данные, takeover/release/send_message, `test-bot`, и т.д.

### WebSocket

- `GET /api/admin/ws?token=...` — live-события (токен из `/auth/login` или `/auth/me`).

Типы событий (см. `app/services/trace_context.py`, подписка в `admin-app.js`):

| Событие | Назначение |
|---------|------------|
| `new_message` | Новая строка в чате (`meta`: `operator_only`, `technical_fallback`, `intent`, …) |
| `state_changed` | FSM диалога (`chatting`, `human_mode`, …) — бейдж и разблокировка ввода оператора |
| `human_needed` | Эскалация на оператора (алерт + звук) |
| `order_updated` | Канбан/список заказов |

При эскалации из WhatsApp публикуются `human_needed` и `state_changed` (`human_mode`).

### WhatsApp

- `GET /api/whatsapp/webhook` — верификация Meta  
- `POST /api/whatsapp/webhook` — входящие сообщения  

### System

- `GET /health` — healthcheck  
- `GET /`, `GET /admin` — HTML админ-панели  
- `GET /request-access` — публичная форма заявки  
- `GET /superadmin` — UI панели владельца (доступ проверяется API)  
- `GET /api/admin/system/task-queue-health` — диагностика Redis / ARQ / worker для админки

## Режимы работы

| Параметр | Значение | Описание |
|----------|----------|----------|
| `DB_MODE` | `sqlite` | SQLite (удобно для разработки) |
| `DB_MODE` | `postgres` | PostgreSQL (продакшен) |
| `REDIS_ENABLED` | `false` | In-memory заглушка для сессий/событий |
| `REDIS_ENABLED` | `true` | Redis сервер |
| `REDIS_MEMORY_ONLY` | `true` | Принудительно in-memory: **не** подключаться к Redis (приоритет над `REDIS_ENABLED`; для тестов и при исчерпании квоты Upstash) |
| `REDIS_URL` | *(пусто)* | Если задан — полный URL подключения (приоритет над `REDIS_HOST`/`PORT`). Для Upstash: строка **Redis Connect** `rediss://…` (нужен TCP для Pub/Sub), не REST API |
| `ARQ_ENABLED` | `false` | Включает постановку фоновых задач в ARQ; в `production/staging` должен быть `true` |
| `ARQ_QUEUE_NAME` | `restomind` | Имя очереди ARQ; web и worker используют одно значение |
| `WHATSAPP_FAST_ACK_ENABLED` | `true` | Safe fast-path: короткое «спасибо» отвечает без LLM |
| `PIPELINE_TIMING_ENABLED` | `true` | Логирует `rm_stage_ms` по этапам inbound-пайплайна |
| `RESTAURANT_MENU_CTX_REDIS_TTL_SEC` | `90` | TTL Redis-кэша строки меню для AI-контекста |

## Разработка

- **Тесты:** `pytest` из корня проекта.  
- **Админка (CSS/инструменты):** если правите `app/templates/*` или стили, установите dev-зависимости Node.js и используйте скрипты из `package.json`:
  - `npm ci`
  - `npm run build:admin-css` (Tailwind → `app/static/css/admin.css`)
  - `npm run check:admin-js` / `npm run lint:js`
  - `npm run lh:admin` (Lighthouse, см. `docs/ui/lighthouse/README.md`)
- **CI:** `.github/workflows/ci.yml` — pytest и проверка импорта приложения.  
- **Deploy по SSH (VPS):** `.github/workflows/deploy.yml` — только если в репозитории задана переменная **`ENABLE_SSH_DEPLOY=true`** и секреты `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_SSH_KEY`. Для деплоя на **Render** этот workflow не нужен.
