# RestoMind — обзор кодовой базы

Документ для быстрой ориентации (люди и ИИ): **что за проект**, **как устроен репозиторий**, **куда смотреть за логикой**. Детали API — OpenAPI `/docs`; инварианты разработки — [`docs/CONVENTIONS.md`](docs/CONVENTIONS.md); история изменений — [CHANGELOG.md](CHANGELOG.md).

---

## Суть проекта

**RestoMind** — backend-сервис «AI-оператора» для ресторана: гость пишет в **WhatsApp**, ответы формирует LLM по структурированной схеме (`AIBrainResponse`), голосовые сообщения транскрибируются. Заказы и брони проходят через состояния диалога (Redis + резерв в БД), цены и номенклатура берутся из данных филиала (меню из **iiko Cloud**, правила ценообразования в коде/`PRICING_*`). Отправка заказа в кухню в iiko — по правилам продукта (часто после действия оператора в админке).

**Мультитенантность:** сущность `Organization`, у заказов/пользователей/меню — привязка к филиалу; часть сценариев — superadmin (платформа) vs админ организации.

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
│   │   ├── admin/                 # админка API (пакет): auth, WS, test-bot + временный монолит роутов
│   │   │   ├── auth.py            # /api/admin/auth/* (cookie-сессия, demo-login, request-access, select-org)
│   │   │   ├── ws.py              # /api/admin/ws?token=... (live-события)
│   │   │   ├── test_bot.py        # /api/admin/test-bot (ручное тестирование диалога)
│   │   │   ├── deps.py            # зависимости: сессия, tenant-scope, guards
│   │   │   └── _monolith.py       # временно: остальной admin API до завершения раскола E0.1
│   │   ├── webhooks.py            # Meta WhatsApp: verify + входящие → обработка сообщений
│   │   ├── payment_webhook.py     # внешние провайдеры оплаты (Bearer/HMAC)
│   │   └── superadmin.py          # организации, заявки, аудит payment-webhook-events, модерация
│   │   └── admin/intelligence.py  # Restaurant Intelligence + Digital Twin API
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
│   │   ├── ai_engine/             # openai_p, gemini_p, базовые абстракции
│   │   ├── intent_router.py       # маршрутизация намерений, работа с черновиком заказа
│   │   ├── dialog_mgr.py          # состояния чата, Redis, синхронизация с БД
│   │   ├── order_logic.py         # меню, позиции, цены, черновик
│   │   ├── menu_sync.py           # синхронизация меню из iiko
│   │   ├── events.py              # Pub/Sub для WS админки
│   │   ├── notification_router.py # Telegram «SOS», уведомления
│   │   ├── payment_*.py           # уведомление гостя, webhook-адаптеры, автопечать в iiko после оплаты
│   │   ├── intelligence.py        # revenue/orders analytics, insights, digital twin snapshots/simulation
│   │   ├── system_events.py       # durable domain events for analytics/audit/AI ops
│   │   ├── integration_health.py / readiness.py   # диагностика интеграций
│   │   ├── tenant_scope.py        # ограничения запросов по organization_id
│   │   └── …                      # booking, analytics, стоп-листы, sales strategy, retention и др.
│   │
│   ├── templates/                 # Jinja2: admin.html, superadmin, onboarding, компоненты
│   └── static/
│       ├── js/admin-app.js        # основная логика админ UI (Alpine)
│       └── css/admin.css
│
├── alembic/                       # миграции PostgreSQL (Alembic)
├── tests/                         # pytest-asyncio; интеграционные и модульные тесты
├── scripts/                       # утилиты: sync_menu_from_iiko, grant_superadmin, диагностика iiko
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
├── docs/                          # SUPABASE_MIGRATION, VERCEL, UPGRADE_TRACKER, …
├── requirements.txt
├── pytest.ini
├── seed.py                        # локальный полный сброс/демо-данные (осторожно)
└── .env.example
```

---

## Потоки данных (в двух словах)

| Направление | Где вход | Куда логика |
|-------------|----------|-------------|
| WhatsApp → бот | `api/webhooks.py` | `services/dialog_mgr`, `intent_router`, `ai_brain`, Redis |
| Админ UI | `templates/` + `static/js/admin-app.js` | `api/admin/` → сервисы, БД |
| Live-обновления | WS `/api/admin/ws` | `services/events` + Redis Pub/Sub (или in-memory) |
| Оплата провайдера | `api/payment_webhook.py` | заказ, `PaymentEvent`, фон: автопечать iiko при флаге org |
| Superadmin | `api/superadmin.py` | организации, заявки, синхронизация меню |

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
