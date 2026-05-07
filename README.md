# RestoMind

AI-оператор для ресторана: принимает заказы и бронирует столики через WhatsApp, используя LLM (**OpenAI** по умолчанию; опционально **Gemini** через `AI_PROVIDER`) — structured output по схеме `AIBrainResponse`; голос — **Whisper**. Интегрируется с **iiko** для синхронизации меню и отправки заказов на кухню.

Подробный список изменений и возможностей — в [CHANGELOG.md](CHANGELOG.md). Правила разработки (инварианты) — в [docs/CONVENTIONS.md](docs/CONVENTIONS.md). **Дерево проекта и суть кодовой базы** — в [codebase.md](codebase.md).

## Возможности

- **Приём заказов** — распознавание блюд из меню, подтверждение «Да»/«Нет»; тип получения и оплата (v2); контейнеры и доставка считаются автоматически (`PRICING_*` в `.env`); после согласия клиента в WhatsApp заказ подтверждается и ждёт оператора — **в iiko** отправляется только из админки («Подтвердить и печать» / канбан)
- **Бронирование** — дата, время, гости; подтверждение «Да»/«Нет» (`CONFIRMING_BOOKING`)
- **FAQ** — часы, адрес, состав, аллергены
- **Human Override** — перехват диалога оператором; в режиме оператора AI не отвечает
- **Стоп-листы** — фоновая синхронизация из iiko (~15 мин)
- **Админ-панель** — вход по логину/паролю (cookie-сессия), дашборд, канбан, live-чаты, WebSocket, аналитика, редактирование меню, демо-данные
- **Надёжность** — rate limiting по телефону, логи в файлы, опционально Sentry
- **Мультитенантность (фундамент)** — модель `Organization`, поле `organization_id` у сущностей (см. CHANGELOG)

## Стек

| Компонент | Технология |
|-----------|------------|
| Backend | Python 3.11+, FastAPI |
| Database | PostgreSQL / SQLite (dev), SQLAlchemy 2.0, **Alembic** |
| Cache | Redis (опционально, есть in-memory fallback) |
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

В админке кнопка **«Демо-данные»** добавляет пользователей с префиксом `demo7700…` и **не стирает** остальное; меню из демо не создаётся.

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

Кратко для продакшена: `APP_DEBUG=false`, PostgreSQL, секреты `OPENAI_API_KEY`, админка, `SESSION_SECRET` / `generateValue` на Render, токены WhatsApp; Redis на Render опционально (см. DEPLOY_RENDER).

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

Полезные UI-доки:

- `docs/UI_DESIGN_SYSTEM.md` — дизайн-система админки (компоненты, токены, a11y, Lighthouse).
- `docs/ui/mobile-review/README.md` — mobile review (Playwright-скриншоты и план улучшений).
- `docs/ROADMAP.md` — единый список техдолга и задач (single source of truth).

## API (кратко)

Полный перечень см. в OpenAPI: [http://localhost:8000/docs](http://localhost:8000/docs) после запуска.

### Авторизация админки (без cookie-сессии)

| Метод | Путь | Описание |
|-------|------|----------|
| POST | `/api/admin/auth/login` | вход, сессия + `ws_token` |
| POST | `/api/admin/auth/demo-login` | гостевой вход в демо-организацию (read-only) |
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

### WhatsApp

- `GET /api/whatsapp/webhook` — верификация Meta  
- `POST /api/whatsapp/webhook` — входящие сообщения  

### System

- `GET /health` — healthcheck  
- `GET /`, `GET /admin` — HTML админ-панели  
- `GET /request-access` — публичная форма заявки  
- `GET /superadmin` — UI панели владельца (доступ проверяется API)  

## Режимы работы

| Параметр | Значение | Описание |
|----------|----------|----------|
| `DB_MODE` | `sqlite` | SQLite (удобно для разработки) |
| `DB_MODE` | `postgres` | PostgreSQL (продакшен) |
| `REDIS_ENABLED` | `false` | In-memory заглушка для сессий/событий |
| `REDIS_ENABLED` | `true` | Redis сервер |
| `REDIS_MEMORY_ONLY` | `true` | Принудительно in-memory: **не** подключаться к Redis (приоритет над `REDIS_ENABLED`; для тестов и при исчерпании квоты Upstash) |
| `REDIS_URL` | *(пусто)* | Если задан — полный URL подключения (приоритет над `REDIS_HOST`/`PORT`). Для Upstash: строка **Redis Connect** `rediss://…` (нужен TCP для Pub/Sub), не REST API |

## Разработка

- **Тесты:** `pytest` из корня проекта.  
- **Админка (CSS/инструменты):** если правите `app/templates/*` или стили, установите dev-зависимости Node.js и используйте скрипты из `package.json`:
  - `npm ci`
  - `npm run build:admin-css` (Tailwind → `app/static/css/admin.css`)
  - `npm run check:admin-js` / `npm run lint:js`
  - `npm run lh:admin` (Lighthouse, см. `docs/ui/lighthouse/README.md`)
- **CI:** `.github/workflows/ci.yml` — pytest и проверка импорта приложения.  
- **Deploy по SSH (VPS):** `.github/workflows/deploy.yml` — только если в репозитории задана переменная **`ENABLE_SSH_DEPLOY=true`** и секреты `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_SSH_KEY`. Для деплоя на **Render** этот workflow не нужен.
