# RestoMind — обзор кодовой базы

Документ для быстрой ориентации (люди и ИИ): **что за проект**, **как устроен репозиторий**, **куда смотреть за логикой**. Детали API — OpenAPI `/docs`; инварианты — [`docs/CONVENTIONS.md`](docs/CONVENTIONS.md); релизы — [CHANGELOG.md](CHANGELOG.md), архив эпиков — [docs/releases/README.md](docs/releases/README.md).

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

**Intelligence OS / Restory-class слой:** продуктовая модель клиента — [`docs/CUSTOMER.md`](docs/CUSTOMER.md); архитектура данных/Copilot/ROI — [`docs/INTELLIGENCE_OS_PLAN.md`](docs/INTELLIGENCE_OS_PLAN.md). Ключевые файлы: `app/services/iiko_olap_sales_sync.py`, `app/services/data_quality.py`, `app/services/copilot/`, `app/services/restaurant_graph.py`, `app/services/forecasting.py`, `app/services/recommendation_outcomes.py`, `app/services/insight_delivery.py`.

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
│   │   │   ├── demo.py            # demo seed/clear + G10.8 shift-scene pitch API
│   │   │   ├── ws.py              # /api/admin/ws?token=... (live-события)
│   │   │   ├── test_bot.py        # /api/admin/test-bot (ручное тестирование диалога)
│   │   │   ├── intelligence.py    # OS dashboard, snapshots/replay, apply-pricing, reviews, digest, supply/staff/voice
│   │   │   ├── network.py         # сеть филиалов (is_network tenant)
│   │   │   ├── schemas.py         # общие Pydantic-схемы для admin API
│   │   │   ├── deps.py            # сессия, tenant-scope, location guards
│   │   │   └── _monolith.py       # временно: остальной admin API до завершения раскола E0.1
│   │   ├── webhooks.py            # Meta WhatsApp: verify + входящие → обработка сообщений
│   │   ├── payment_webhook.py     # внешние провайдеры оплаты (Bearer/HMAC)
│   │   └── superadmin.py          # организации, заявки, SuperadminAuditLog, GET /audit, message accounting, AI tokens
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
│   │   ├── intent_router.py       # маршрутизация намерений, get_or_create_user (E.164), черновик заказа
│   │   ├── phone_normalize.py     # E.164: normalize_phone_e164, canonical_user_phone, lookup variants
│   │   ├── user_phone_resolve.py  # find_user_by_phone (7705… vs +7705…)
│   │   ├── user_phone_merge.py    # merge duplicate User rows (one-off / scripts)
│   │   ├── wa_queue_metrics.py    # queue_wait_ms: enqueue → process_with_retry start
│   │   ├── chat_serializer.py     # per-chat FIFO + owner-token Redis lock для входящих сообщений
│   │   ├── async_tasks.py         # tracked fire-and-forget asyncio tasks + логирование исключений
│   │   ├── quick_replies.py       # детерминированные ответы без LLM (greeting, menu, status, …)
│   │   ├── faq_cache.py           # Redis-кеш intent=faq + get_faq_cache_metrics
│   │   ├── redis_locks.py         # owner-token Redis locks для фоновых singleton loops
│   │   ├── task_queue.py          # ARQ enqueue / dispatch_arq_or_background
│   │   ├── event_consumer_runner.py # post-commit analytics/audit consumers (async по умолчанию)
│   │   ├── dialog_mgr.py          # состояния чата, Redis/БД, in-transaction durable state transitions
│   │   ├── stoplist_session.py    # Redis rm:stoplist_seen — diff «только что на стопе» в диалоге
│   │   ├── trace_context.py       # publish_chat_event / publish_state_event / publish_human_event (WS payload)
│   │   ├── order_logic.py         # меню (include_unavailable), ValidatedOrder+stoplist_items, цены, LRU menu context cache
│   │   ├── context_engine.py      # параллельный preflight (asyncio.gather), загружает стоп-позиции для ИИ
│   │   ├── customer_reply.py      # доставка текста/голоса клиенту; finalize_outbound fire-and-forget
│   │   ├── message_accounting.py  # telemetry учёт сообщений WhatsApp (inbound/outbound, upsert, fire-and-forget)
│   │   ├── ai_usage.py            # учёт токенов LLM (upsert по org+day, schedule_log_ai_usage)
│   │   ├── pipeline_latency.py    # latency baselines, SLA monitor, fire-and-forget
│   │   ├── personalization.py     # get_user_preferences: never_categories/drinks_freq из истории заказов
│   │   ├── menu_sync.py           # tenant-scoped синхронизация меню из iiko
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
│   │   ├── demo_data.py             # seed/clear demo org; pitch risks + recovered_kzt для explore
│   │   ├── demo_shift_scene.py      # G10.8: 30s counterfactual pitch (GET-only phases)
│   │   ├── demo_shift_presentation.py # смягчение shift/state в demo explore (cap risk, S2)
│   │   ├── supplymind.py          # inventory snapshots, purchase draft builder
│   │   ├── staffmind.py           # staff onboarding sessions, KB Q&A
│   │   ├── voice_ai.py            # per-org voice flag, list_voice_call_logs, record_voice_call
│   │   ├── trace_context.py       # trace_id contextvars, WS publish helpers
│   │   ├── trace_timeline.py      # GET /trace-timeline: merge SystemEvent + ChatLog by trace_id
│   │   ├── superadmin_audit.py    # SuperadminAuditLog write/list
│   │   ├── daily_os_digest.py     # legacy daily OS digest (Final Mile)
│   │   ├── owner_intelligence.py  # OI summary KPI, menu/network preview
│   │   ├── owner_intelligence_digest.py  # weekly digest text builder
│   │   ├── owner_digest_delivery.py      # preview/send pipeline, SystemEvent audit
│   │   ├── owner_weekly_digest.py        # ARQ cron Mon 10:00 org TZ
│   │   ├── order_ai_audit.py      # QA auto-audit risk scoring + calibration
│   │   ├── upsell_scoring_engine.py / upsell_pair_mining.py / upsell_attribution.py
│   │   ├── upsell_experiments.py  # A/B phrase variants
│   │   ├── menu_profit_lab.py     # cost_price analytics, price recommendations
│   │   ├── network_benchmark.py / network_weekly_report.py
│   │   ├── operational_mode.py    # Kitchen Gate v2
│   │   ├── telegram_customer.py   # Telegram guest channel
│   │   ├── pos/adapters/          # POSAdapter registry (iiko, rkeeper)
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
├── scripts/                       # sync_menu_from_iiko, grant_superadmin, diag_duplicate_phones,
│                                  # diag_whatsapp_latency, merge_duplicate_users,
│                                  # verify_owner_intel_schema.py (post-migration smoke), …
│
├── .github/workflows/
│   ├── ci.yml                     # push: pytest + импорт приложения
│   └── deploy.yml                 # опционально SSH на VPS (ENABLE_SSH_DEPLOY)
│
├── Dockerfile
├── docker-compose.yml
├── docker-compose.prod.yml
├── render.yaml                    # Blueprint Render: web `restomind` + worker `restomind-worker`
│
├── README.md                      # быстрый старт и возможности
├── codebase.md                    # этот файл
├── docs/CONVENTIONS.md            # инварианты разработки (контракт)
├── CHANGELOG.md
├── DEPLOY_RENDER.md
├── docs/                          # ROADMAP, CONVENTIONS, releases/ (архив эпиков), UI_MAP, …
├── requirements.txt
├── pytest.ini
├── seed.py                        # локальный полный сброс/демо-данные (осторожно)
└── .env.example
```

---

## Потоки данных (в двух словах)

| Направление | Где вход | Куда логика |
|-------------|----------|-------------|
| WhatsApp → бот | `api/webhooks.py` | E.164; `process_inbound_message(channel=whatsapp)`; ARQ → intent_router, Revenue Copilot hooks |
| Telegram guest | `api/telegram_webhook.py` | `process_inbound_message(channel=telegram)`; org resolve by webhook secret |
| Owner Intelligence | `api/admin/owner_intelligence*.py` | summary, QA audits, upsell, menu-profit, network, kitchen-gate, digest |
| Weekly digest | ARQ `owner_digest_scheduled_tick` | `owner_digest_delivery.py` → Telegram ops; dedupe Redis + `owner_digest.sent` event |
| Daily digest (Final Mile) | ARQ `daily_os_digest_scheduled_tick` | `daily_os_digest.py` → Telegram ops chat |
| Админ UI | `templates/` + `static/js/admin-app.js` | `api/admin/` → сервисы, БД; чаты: channel badge WA/TG, E.164 dedupe, FSM-бейдж |
| Live-обновления | WS `/api/admin/ws` | `services/events` + `trace_context`; `os.audit`, business events |
| Voice (Twilio) | `api/webhooks.py` voice routes | `voice_ai.py` → STT/Realtime → `process_message` |
| Control Plane trace | webhook, ARQ, admin chats | `trace_timeline.py`, `GET /intelligence/trace-timeline` |
| POS sync | `api/admin/menu.py`, `iiko_sync_tasks.py` | `get_pos_adapter(org)` → iiko or rkeeper |
| Оплата провайдера | `api/payment_webhook.py` | заказ, `PaymentEvent`, фон: автопечать iiko при флаге org |
| Superadmin | `api/superadmin.py` | организации, заявки, credentials, `GET /audit`, message accounting, AI-токены |

---

## Тесты и CI

```bash
python -m pytest tests/ -v
```

Test suite: **1000+** tests in `tests/` (`pytest -q`). GitHub Actions: [`.github/workflows/ci.yml`](.github/workflows/ci.yml).

**Alembic:** не хардкодить revision id в доках — проверять `alembic heads` (один head) и `alembic current` после `alembic upgrade head`. Smoke: [`docs/DEPLOY_RUNBOOK.md`](docs/DEPLOY_RUNBOOK.md) §8.

---

## Связанные документы

| Файл | Назначение |
|------|------------|
| [README.md](README.md) | Установка, `.env`, запуск, краткая структура |
| [`docs/CONVENTIONS.md`](docs/CONVENTIONS.md) | Правила разработки (инварианты/контракт) |
| [CHANGELOG.md](CHANGELOG.md) | Краткие релизы |
| [docs/releases/README.md](docs/releases/README.md) | Архив эпиков (детали) |
| [DEPLOY_RENDER.md](DEPLOY_RENDER.md) / [docs/DEPLOY_RUNBOOK.md](docs/DEPLOY_RUNBOOK.md) | Production (Render / self-hosted checklist) |
| [docs/DEPLOY_RUNBOOK.md](docs/DEPLOY_RUNBOOK.md) | Staging/prod чеклист; §8 Owner Intelligence smoke |
| [docs/SUPERADMIN_GUIDE.md](docs/SUPERADMIN_GUIDE.md) | Super Admin (владелец платформы): заявки/регистрация, управление ресторанами, аудит webhook, идеи улучшений |
| [docs/UI_MAP.md](docs/UI_MAP.md) | Карта админ UI: `admin.html`, `screens/*`, компоненты, `admin-app.js` |
| [docs/AI_TOOLS_SETUP.md](docs/AI_TOOLS_SETUP.md) | Настройка Cursor / Claude Code / MCP для работы над репо |
| [docs/AI_OPERATIONS.md](docs/AI_OPERATIONS.md) | Restaurant Intelligence, **Owner Intelligence**, инсайты, Final Mile API |
| [docs/FINAL_MILE_IMPLEMENTED.md](docs/FINAL_MILE_IMPLEMENTED.md) | SupplyMind, StaffMind, Voice, Digest — backend MVP |
| [docs/ROADMAP.md](docs/ROADMAP.md) | Single tracker for tasks and statuses |
| [docs/EVENT_ARCHITECTURE.md](docs/EVENT_ARCHITECTURE.md) | Durable `SystemEvent`, пайплайн аналитики |
