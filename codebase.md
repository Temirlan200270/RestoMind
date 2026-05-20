# RestoMind — обзор кодовой базы

Документ для быстрой ориентации (люди и ИИ): **что за проект**, **как устроен репозиторий**, **куда смотреть за логикой**. Детали API — OpenAPI `/docs`; инварианты разработки — [`docs/CONVENTIONS.md`](docs/CONVENTIONS.md); история изменений — [CHANGELOG.md](CHANGELOG.md).

---

## Суть проекта

**RestoMind OS** — AI-операционная система с модульной архитектурой для ресторанного бизнеса. Не просто чат-бот: единое ядро управления продажами, маркетингом и операционкой.

**Канал:** гость пишет в **WhatsApp**, ответы формирует LLM по структурированной схеме (`AIBrainResponse`), голосовые сообщения транскрибируются. Заказы и брони проходят через FSM-состояния диалога (Redis + durable в БД), цены и номенклатура берутся из данных филиала (меню из **iiko Cloud**, правила ценообразования в `PRICING_*`). Отправка заказа в кухню — по правилам продукта (часто после действия оператора в админке).

**Архитектурные слои (OS Core):**
- _Tenant Isolation_ — `organization_id` на всех сущностях; межтенантный доступ запрещён конвенцией и тестами
- _Event-Driven Core_ — бизнес-события через `emit_system_event` (`app/services/system_events.py`); основа аналитики
- _AI Context Layer_ — `fetch_ai_read_context` → `AIReadContext` готовит данные для LLM; сырой SQL в LLM-вызовах запрещён

**Иерархия тенантности:** `Tenant` (владелец аккаунта) → `Organization` (филиал/заведение); флаг `Tenant.is_network` включает режим франшизы с Branch Switcher и агрегированной аналитикой «Вся сеть».

**Модули поверх ядра:** Ordering, GuestCare, Marketing, Intelligence, SupplyMind (дорожная карта).

**Админ-панель:** серверный HTML (Jinja2) + Alpine.js, REST под `/api/admin/*`, live-события по WebSocket; отдельно UI и API **superadmin** (`/api/superadmin/*`, страница `/superadmin`).

**ИИ:** по умолчанию OpenAI; через настройки возможен **Gemini** (`AI_PROVIDER`). См. `app/services/ai_engine/`, `app/services/ai_brain.py`.

---

## Дерево репозитория

Упрощённо: показаны только значимые узлы; в `services/` перечислены ключевые файлы, остальное — по аналогии или через поиск по `app/services/`.

```text
RestoMind/
├── app/
│   ├── main.py                    # FastAPI, роутеры, lifespan, статика
│   ├── worker.py                  # фоновый воркер ARQ (очереди; если включено)
│   │
│   ├── api/                       # HTTP-слой: тонкие хендлеры → services
│   │   ├── admin/                 # админка API (пакет)
│   │   │   ├── __init__.py        # агрегирует роутеры (совместимый импорт `from app.api.admin import …`)
│   │   │   ├── auth.py            # /api/admin/auth/* (cookie-сессия, demo-login, request-access, select-org)
│   │   │   ├── ws.py              # /api/admin/ws?token=... (live-события)
│   │   │   ├── test_bot.py        # /api/admin/test-bot (ручное тестирование диалога)
│   │   │   ├── intelligence.py    # OS dashboard, snapshots/replay, apply-pricing, reviews, digest, supply/staff/voice
│   │   │   ├── network.py         # сеть филиалов (is_network tenant)
│   │   │   ├── schemas.py         # общие Pydantic-схемы для admin API
│   │   │   ├── deps.py            # сессия, tenant-scope, location guards
│   │   │   └── _monolith.py       # временно: остальной admin API до завершения раскола E0.1
│   │   ├── webhooks.py            # Meta WhatsApp: verify + входящие → обработка сообщений
│   │   ├── payment_webhook.py     # внешние провайдеры оплаты (Bearer/HMAC)
│   │   └── superadmin.py          # организации, заявки, аудит платёжных уведомлений, message accounting, AI tokens
│   │
│   ├── core/                      # config, rate_limiter, пароли, константы ИИ
│   ├── db/
│   │   ├── models.py              # SQLAlchemy-модели (Organization, User, Order, …)
│   │   ├── session.py             # async engine, async_session_factory, get_db, Redis
│   │   └── ssl_context.py         # TLS для Postgres
│   │
│   ├── integrations/              # внешние API
│   │   ├── whatsapp.py
│   │   ├── iiko_client.py
│   │   ├── telegram.py
│   │   ├── twilio_client.py / twilio_media.py   # голос/телефония при необходимости
│   │   └── telephony.py           # заготовки
│   │
│   ├── schemas/
│   │   └── ai_schemas.py          # Pydantic-схемы ответа ИИ и заказа
│   │
│   ├── services/                  # бизнес-логика и оркестрация
│   │   ├── ai_brain.py            # вызов LLM, парсинг в схему
│   │   ├── ai_engine/             # openai_p (prompt caching order), gemini_p, prompting.py, базовые абстракции
│   │   ├── intent_router.py       # маршрутизация намерений, черновик заказа, stoplist handling
│   │   ├── dialog_mgr.py          # состояния чата, Redis, синхронизация с БД; clear_pending_order не сбрасывает HUMAN_MODE
│   │   ├── stoplist_session.py    # Redis rm:stoplist_seen — diff «только что на стопе» в диалоге
│   │   ├── trace_context.py       # publish_chat_event / publish_state_event / publish_human_event (WS payload)
│   │   ├── order_logic.py         # меню (include_unavailable), ValidatedOrder+stoplist_items, цены, черновик, fingerprint стоп-листа в кэше
│   │   ├── context_engine.py      # параллельный preflight (asyncio.gather), загружает стоп-позиции для ИИ
│   │   ├── customer_reply.py      # доставка текста/голоса клиенту; finalize_outbound fire-and-forget
│   │   ├── message_accounting.py  # telemetry учёт сообщений WhatsApp (inbound/outbound, upsert, fire-and-forget)
│   │   ├── ai_usage.py            # учёт токенов LLM (upsert по org+day, schedule_log_ai_usage)
│   │   ├── pipeline_latency.py    # latency baselines, SLA monitor, fire-and-forget
│   │   ├── personalization.py     # get_user_preferences: never_categories/drinks_freq из истории заказов
│   │   ├── menu_sync.py           # синхронизация меню из iiko
│   │   ├── events.py              # Pub/Sub для WS админки
│   │   ├── notification_router.py # Telegram «SOS», уведомления
│   │   ├── payment_*.py           # уведомление гостя, webhook-адаптеры, автопечать в iiko после оплаты
│   │   ├── intelligence.py        # revenue/orders analytics, insights, digital twin snapshots/simulation
│   │   ├── sales_strategy_engine.py # E11: жёсткие правила до LLM (trace cap, session rejection cap)
│   │   ├── sales_strategy.py      # build_sales_strategy: tag-pairing, персонализация, heuristics
│   │   ├── system_events.py       # emit_event(BusinessEvent) → analytics + audit
│   │   ├── audit_consumer.py      # audit_log + WS os.audit
│   │   ├── dialog_events.py       # ai.dialog.started
│   │   ├── integration_events.py  # integration.iiko.failed
│   │   ├── analytics_backfill.py  # DailyOrgStats + dialogs_count
│   │   ├── owner_dashboard.py     # os-dashboard, stock_alerts, revenue history (location-aware)
│   │   ├── revenue_leak.py        # Money MVP: abandoned drafts, slow response, cancellations
│   │   ├── supplymind.py          # inventory snapshots, purchase draft builder
│   │   ├── staffmind.py           # staff onboarding sessions, KB Q&A
│   │   ├── voice_ai.py            # per-org voice flag, call logs, Twilio guard
│   │   ├── daily_os_digest.py     # owner digest payload + Telegram send + ARQ tick
│   │   ├── healing_actions.py     # self-healing + WA payment nudges 2.0
│   │   ├── integration_health.py / readiness.py
│   │   ├── tenant_scope.py        # organization_id + Location RBAC
│   │   └── …                      # booking, analytics, стоп-листы, sales strategy, loyalty, marketing и др.
│   │
│   ├── templates/                 # Jinja2: admin.html (скелет + include), screens/*, components/*, superadmin, onboarding
│   └── static/
│       ├── js/admin-app.js        # Alpine + WS (os.audit, business events)
│       ├── manifest.webmanifest   # PWA
│       ├── sw.js                  # service worker (offline shell)
│       └── css/admin.css
│
├── alembic/                       # миграции PostgreSQL (Alembic)
├── tests/                         # pytest-asyncio; интеграционные и модульные тесты
├── scripts/                       # sync_menu_from_iiko, grant_superadmin, capture_admin_mobile_review.py, run_admin_lighthouse.mjs, …
│
├── .github/workflows/
│   ├── ci.yml                     # push: pytest + импорт приложения
│   └── deploy.yml                 # опционально SSH на VPS (ENABLE_SSH_DEPLOY)
│
├── Dockerfile
├── docker-compose.yml
├── docker-compose.prod.yml
├── render.yaml                    # Blueprint Render (Docker)
│
├── README.md                      # быстрый старт и возможности
├── codebase.md                    # этот файл
├── docs/CONVENTIONS.md            # инварианты разработки (контракт)
├── CHANGELOG.md
├── DEPLOY_RENDER.md
├── DEPLOY_GUIDE.md
├── docs/                          # ROADMAP, CONVENTIONS, UI_DESIGN_SYSTEM, UI_MAP, AI_OPERATIONS, EVENT_ARCHITECTURE, …
├── requirements.txt
├── pytest.ini
├── seed.py                        # локальный полный сброс/демо-данные (осторожно)
└── .env.example
```

---

## Потоки данных (в двух словах)

| Направление | Где вход | Куда логика |
|-------------|----------|-------------|
| WhatsApp → бот | `api/webhooks.py` | preflight: канон. телефон, сброс DRAFT при пустой истории, stoplist_session; `dialog_mgr`, `intent_router`, `ai_brain`; operator_only / эскалация → `trace_context.publish_*` |
| Админ UI | `templates/` + `static/js/admin-app.js` | `api/admin/` → сервисы, БД; чаты: FSM-бейдж, `formatChatDisplayContent`, takeover/release |
| Live-обновления | WS `/api/admin/ws` | `services/events` + `trace_context`; `os.audit`, business events, `new_message`, `state_changed`, … |
| Voice (Twilio) | `api/webhooks.py` voice routes | `voice_ai.py` → STT → `process_message`; guard `voice_ai_enabled` |
| Daily digest | ARQ `daily_os_digest_scheduled_tick` | `daily_os_digest.py` → Telegram ops chat |
| Оплата провайдера | `api/payment_webhook.py` | заказ, `PaymentEvent`, фон: автопечать iiko при флаге org |
| Superadmin | `api/superadmin.py` | организации, заявки, синхронизация меню, message accounting, AI-токены |

---

## Тесты и CI

```bash
python -m pytest tests/ -v
```

В репозитории — **порядка двухсот** тестов и десятки файлов в `tests/`; точное число может расти. GitHub Actions: [`.github/workflows/ci.yml`](.github/workflows/ci.yml) (`push` на `main` / `develop`, PR в `main`).

---

## Связанные документы

| Файл | Назначение |
|------|------------|
| [README.md](README.md) | Установка, `.env`, запуск, краткая структура |
| [`docs/CONVENTIONS.md`](docs/CONVENTIONS.md) | Правила разработки (инварианты/контракт) |
| [CHANGELOG.md](CHANGELOG.md) | История версий |
| [DEPLOY_RENDER.md](DEPLOY_RENDER.md) / [DEPLOY_GUIDE.md](DEPLOY_GUIDE.md) | Продакшен |
| [docs/SUPERADMIN_GUIDE.md](docs/SUPERADMIN_GUIDE.md) | Super Admin (владелец платформы): заявки/регистрация, управление ресторанами, аудит webhook, идеи улучшений |
| [docs/UI_MAP.md](docs/UI_MAP.md) | Карта админ UI: `admin.html`, `screens/*`, компоненты, `admin-app.js` |
| [docs/AI_TOOLS_SETUP.md](docs/AI_TOOLS_SETUP.md) | Настройка Cursor / Claude Code / MCP для работы над репо |
| [docs/AI_OPERATIONS.md](docs/AI_OPERATIONS.md) | Restaurant Intelligence, события, инсайты, Final Mile API |
| [docs/FINAL_MILE_IMPLEMENTED.md](docs/FINAL_MILE_IMPLEMENTED.md) | SupplyMind, StaffMind, Voice, Digest — backend MVP |
| [docs/REMAINING_UPDATES.md](docs/REMAINING_UPDATES.md) | UI gaps и staging checks после Final Mile |
| [docs/EVENT_ARCHITECTURE.md](docs/EVENT_ARCHITECTURE.md) | Durable `SystemEvent`, пайплайн аналитики |
