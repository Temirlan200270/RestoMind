# RestoMind Workspace Briefing

## SNAPSHOT

**type:** single  
**langs:** Python, HTML, CSS, JavaScript  
**runtimes:** Python 3.8+, Node.js 18+ (Stagewise CLI)  
**pkgManager:** pip, npm  
**deliverables:** FastAPI backend, SQLite/PostgreSQL, admin SPA, WhatsApp/Telegram integration  
**rootConfigs:** `package.json`, `requirements.txt`, `alembic.ini`, `.env`, `docker-compose.yml`

---

## ARCHITECTURE OVERVIEW

Monolithic FastAPI application: WhatsApp chatbot operator for restaurant (orders, bookings, FAQ). Multi-tenant foundation, AI-driven (OpenAI), menu sync (iiko), payment prepayment, admin dashboard (Jinja2 + Alpine.js).

**Integrations:** WhatsApp (Meta API) → OpenAI (chat + Whisper) → iiko POS → Redis (optional)

---

## PACKAGES

This is a single-package project:

| name | path | type | deps | usedBy | role |
|------|------|------|------|--------|------|
| restomind | `w13fb/` | app | openai, fastapi, sqlalchemy, redis | standalone | AI-powered restaurant WhatsApp bot + admin |

---

## DEPENDENCY GRAPH

None (single package).

---

## ARCHITECTURE

### restomind (`w13fb/`)

**entry:** `app/main.py`  
**routing:** `app/api/admin/` (admin CRUD; временно часть роутов в `_monolith.py`), `app/api/webhooks.py` (WhatsApp webhook)
**state:** Redis cache (optional), SQLite/PostgreSQL session, in-memory fallback  
**api:** RESTful JSON, WebSocket (admin live), WhatsApp Meta API, iiko Cloud API  
**db:** SQLAlchemy async ORM → SQLite (dev) | PostgreSQL (prod); 8 tables: users, orders, bookings, menu_items, chat_logs, organizations, integration_events, escalation_events  
**auth:** Session-based (admin login) + WhatsApp phone number as user identifier  
**build:** Alembic migrations (PostgreSQL), Tailwind CSS compiler  
**services:**
- `ai_brain.py` → OpenAI Chat Completions (parse) with structured output (AIBrainResponse)
- `intent_router.py` → map AI intent (order, book, faq, escalate) → business logic
- `dialog_mgr.py` → user conversation state (Redis/in-memory, history, pending actions)
- `order_logic.py` → order validation, pricing (container, delivery fee), JSON serialization
- `booking_halls.py` → booking availability, VIP slots, hall management
- `menu_sync.py` → iiko menu/stop-list sync to DB
- `integration_health.py` → track sync status (last success, error messages)
- `prompts.py` → system prompt templates

**integrations:**
- `whatsapp.py` → send message, template (Meta Cloud API)
- `telegram.py` → escalation alerts to admin
- `iiko_client.py` → fetch menu, create orders, fetch stop-lists (Cloud API v3)
- `telephony.py` → telephony fallback (stub)

**dirs:**
- `app/api/` → router entry points (authentication, admin CRUD, WebSocket)
- `app/core/` → config (Pydantic Settings), rate limiter middleware
- `app/db/` → SQLAlchemy models (ORM), session factory, connection pooling
- `app/services/` → core logic (AI, dialog, routing, order, booking, sync)
- `app/integrations/` → external APIs (WhatsApp, iiko, Telegram, Telephony)
- `app/schemas/` → Pydantic models (AIBrainResponse, validation)
- `app/data/` → static menu data (plovxana_menu.py, seed data)
- `app/static/` → compiled CSS (Tailwind)
- `app/templates/` → Jinja2 HTML (admin.html, 297KB single-page)
- `alembic/` → schema migrations (PostgreSQL)
- `tests/` → pytest integration/unit tests (AI, orders, pricing, booking, rate limit)
- `logs/` → rotating file logs (restomind.log, errors.log)

---

## STACK

**restomind** →
- framework: FastAPI 0.115+
- routing: FastAPI Router (prefix-based), WebSocket
- state: Redis async (optional) | InMemoryRedis fallback
- orm: SQLAlchemy 2.0+ async
- db: SQLite (aiosqlite) | PostgreSQL (asyncpg)
- auth: SessionMiddleware (cookie-based), phone number identity
- migrations: Alembic
- ai: OpenAI (`OPENAI_MODEL`, default gpt-4o-mini; structured output + Whisper for voice)
- http-client: httpx 0.28+
- validation: Pydantic 2.10+, Pydantic Settings
- templates: Jinja2 3.1+
- frontend: Alpine.js (in admin.html), TailwindCSS
- async-runtime: asyncio, uvicorn
- logging: stdlib logging (rotating file handler)
- monitoring: Sentry (optional, DSN-based)
- testing: pytest, pytest-asyncio

---

## STYLE

- **naming:** snake_case functions/variables, UPPER_CASE constants, CamelCase classes (ORM models, Pydantic)
- **imports:** organize by stdlib, third-party, local app imports
- **typing:** Full type hints on functions (async def, return → TYPE)
- **errors:** Pydantic ValidationError caught in services, logged to file/console + Sentry
- **async:** Async/await throughout (no blocking I/O); FastAPI dependency injection (Depends)
- **state:** Dialog/order state stored in Redis (chat history, pending items) with TTL; fallback to in-memory
- **testing:** pytest fixtures (conftest.py), mock OpenAI/iiko; unit tests on business logic
- **logging:** Rotation (10MB restomind.log, 5MB errors.log); DEBUG if APP_DEBUG=true
- **patterns:** Service layer (intent_router, order_logic) decoupled from routes; factory pattern (redis_client)

---

## STRUCTURE

High-signal directories:

- `app/` → core application (services, API, ORM)
- `app/api/` → FastAPI routers (routes to handlers)
- `app/services/` → business logic (no Flask context; pure functions + DB)
- `app/db/` → ORM models + session factory
- `app/integrations/` → external API clients
- `app/templates/` → admin.html (single monolithic page)
- `alembic/` → PostgreSQL schema versioning
- `tests/` → pytest suite (unit + integration)
- `.github/workflows/` → CI/CD (ci.yml, deploy.yml)

---

## BUILD

**workspaceScripts:**
- `stagewise` → Stagewise CLI proxy
- `stagewise:bridge` → bridge mode
- `dev:stagewise` → run dev server via Stagewise (reload on change)
- `build:admin-css` → compile Tailwind CSS → `app/static/css/admin.css`

**envFiles:**
- `.env` (current, with secrets; not in git)
- `.env.example` (template, all keys documented)

**envPrefixes:**
- `APP_` (app_name, app_debug)
- `DB_` (db_mode, database_url_dsn)
- `POSTGRES_` (user, password, host, port, db)
- `REDIS_` (enabled, host, port, db)
- `OPENAI_` (api_key, model, transcription_model, base_url)
- `WHATSAPP_` (api_token, verify_token, phone_number_id, public_base_url)
- `TELEGRAM_` (bot_token, admin_chat_id)
- `IIKO_` (api_login, organization_id, terminal_group_id, product_ids, menu_sync_only_dish_good)
- `PRICING_` (container_hall, container_delivery_pickup, delivery_fee, delivery_free_threshold, containers_per_main_unit)
- `HALL_` (prepayment_min)
- `ADMIN_` (username, password)
- `SESSION_` (secret)
- `RATE_LIMIT_` (per_minute)
- `SENTRY_` (dsn)

**ci:**
- `.github/workflows/ci.yml` → run tests (pytest), lint (if configured)
- `.github/workflows/deploy.yml` → deploy to Render

**docker:**
- `Dockerfile` → Python 3.14 slim, uvicorn entry
- `docker-compose.yml` → dev (app + SQLite)
- `docker-compose.prod.yml` → prod (app + PostgreSQL + Redis + Nginx SSL)
- `.dockerignore` → exclude node_modules, .git, logs, __pycache__

---

## LOOKUP

Map tasks to authoritative files:

- **add WhatsApp webhook route** → `app/api/webhooks.py`, `app/main.py` (include_router)
- **add admin endpoint** → `app/api/admin/` (router; временно `_monolith.py`), `app/main.py` (include_router)
- **add AI prompt template** → `app/services/prompts.py` (RESTAURANT_SYSTEM_PROMPT)
- **add intent handler** → `app/services/intent_router.py` (route_intent function, new branch)
- **add ORM model** → `app/db/models.py` (add Base subclass, migration in alembic/versions/)
- **add service logic** → `app/services/` (new module or extend existing)
- **modify order validation** → `app/services/order_logic.py` (validate_order, build_order_items_json)
- **modify booking logic** → `app/services/booking_halls.py` (BOOKING_SLOTS, vip_slot_occupied)
- **add iiko integration** → `app/integrations/iiko_client.py` (IikoClient class)
- **add WhatsApp message** → `app/integrations/whatsapp.py` (send_message, send_template)
- **add config option** → `app/core/config.py` (Settings dataclass + .env.example)
- **admin frontend** → `app/templates/admin.html` (Alpine.js, API calls to admin.py)
- **add test** → `tests/test_*.py` (pytest + conftest fixtures)
- **run migrations** → `alembic revision --autogenerate -m "message"` then `alembic upgrade head`

---

## KEY FILES

| file | scope | purpose | readFor |
|------|-------|---------|---------|
| `app/main.py` | fastapi | app factory, lifespan (startup/shutdown, table creation, stop-list loop), routing setup | entry point, middleware, lifecycle hooks |
| `app/core/config.py` | config | all environment variables, Pydantic Settings, database_url property, redis_url property | how to add new config option, environment defaults |
| `app/db/models.py` | orm | 8 ORM models (User, Order, Booking, MenuItem, ChatLog, Organization, IntegrationEvent, IntegrationHealth, EscalationEvent) | schema, relationships, field types, defaults |
| `app/db/session.py` | db-init | async engine, session factory, Redis client (or in-memory fallback), dependency injection | DB connection pooling, how to add new dependency |
| `app/api/webhooks.py` | routes | WhatsApp webhook endpoint (`POST /api/whatsapp/webhook`), message parsing, rate limiting, background task dispatch | WhatsApp message flow, how to route intents |
| `app/api/admin/` | routes | 30+ admin endpoints (orders, bookings, menu, integrations, sync, demo, websocket) | admin CRUD operations, WebSocket broadcasting |
| `app/services/ai_brain.py` | ai | call OpenAI API with structured output (AIBrainResponse), retry logic, fallback on error | how to modify AI prompt, how to add fields to response |
| `app/services/intent_router.py` | logic | dispatch AI intent → order/book/faq/escalate logic, validation, DB writes, event publish | how to add new intent, how to modify order/booking flow |
| `app/services/dialog_mgr.py` | state | user conversation state (Redis/in-memory), chat history, pending order/booking, HUMAN_MODE flag | how conversation context is stored, state machine |
| `app/services/order_logic.py` | logic | order validation against menu, pricing (container, delivery, threshold), JSON serialization | pricing calculation, order items format, validation rules |
| `app/services/menu_sync.py` | sync | sync menu from iiko (products, categories, prices), sync stop-lists (availability) | how menu is fetched, how to customize product filtering |
| `app/integrations/iiko_client.py` | external | iiko Cloud API client (menu fetch, order creation, stop-list fetch) | iiko API flow, terminal group filtering, order JSON structure |
| `app/integrations/whatsapp.py` | external | Meta WhatsApp API client (send text, template messages) | WhatsApp message sending, template IDs |
| `app/schemas/ai_schemas.py` | validation | Pydantic schema for AI response (intent, reply_text, order_items, booking_data, meta) | AI response validation, what fields AI can output |
| `alembic/` | migrations | version-controlled DB schema (PostgreSQL) | how to add columns or new tables |
| `tests/conftest.py` | tests | pytest fixtures (async_db, mocks) | how to set up test fixtures, mocking strategy |

---

## STATE MANAGEMENT

**User Conversation State (Redis | In-Memory):**
- `chat_history:{phone}` → list of (role, content) tuples (max 20 recent messages)
- `user_state:{phone}` → UserState enum (IDLE, AWAITING_ORDER_CONFIRM, AWAITING_BOOKING_CONFIRM, HUMAN_MODE)
- `pending_order:{phone}` → JSON (items, total) — waiting for user "да" (confirm)
- `pending_booking:{phone}` → JSON (date, time, guests, hall, comment) — waiting for confirm
- TTL: 1 hour (unless extended)

**Database Persistence:**
- ChatLog → all messages (user, assistant, system) + meta_json (intent, confidence)
- User → phone, name, operator_note (for admin), ai_paused flag
- Order → status (draft → confirmed → sent_to_iiko → completed | cancelled), items_json, total, prepayment_status
- Booking → date, time, guests, hall, status, linked order

---

## DEPLOY FLOW

1. **Local Dev:** `npm run dev:stagewise` (Python 3.14, SQLite, in-memory Redis, no iiko/WhatsApp)
2. **Docker:** `docker-compose up` (same as local; for reproducibility)
3. **Production:** `docker-compose.prod.yml` (PostgreSQL, Redis, Nginx + SSL, env from .env or managed platform)
4. **CI/CD:** GitHub Actions → test (pytest) → deploy to Render (render.yaml config)

---

## PORTS & ENDPOINTS

- **Local:** `http://localhost:8000`
  - `GET /` → admin panel
  - `POST /api/whatsapp/webhook` → WhatsApp incoming messages
  - `GET /health` → lightweight ping
  - `GET /health/deep` → DB + Redis check
  - **Admin API:** `/api/admin/orders`, `/api/admin/bookings`, `/api/admin/menu`, etc.
  - **WebSocket:** `/api/admin/ws?token=...` → live order/chat updates

- **Production:** `https://restomind.example.com` (via Render, Railway, or self-hosted)

---

## QUICKSTART MODIFICATIONS

**Add new order field:**
1. Update `User.Order` model in `app/db/models.py`
2. Run `alembic revision --autogenerate -m "add_field"`
3. Update `AIBrainResponse` in `app/schemas/ai_schemas.py` (if AI needs to extract it)
4. Update `validate_order`, `build_order_items_json` in `app/services/order_logic.py`
5. Add test in `tests/test_order_logic.py`

**Add new intent:**
1. Add case in `route_intent()` in `app/services/intent_router.py`
2. Update AI prompt in `app/services/prompts.py` (RESTAURANT_SYSTEM_PROMPT)
3. Update `AIBrainResponse.intent` Enum (if using Enum)
4. Implement handler (e.g., `handle_new_intent()`)

**Add new admin endpoint:**
1. Define handler in `app/api/admin/` (временное место — `_monolith.py`, далее — целевой подмодуль) (with `@router.get()`, `@router.post()`, etc.)
2. Add to `app/main.py` `include_router()` call
3. Update `admin.html` Alpine.js component to call endpoint

**Integrate new WhatsApp message type:**
1. Add case in `_process_whatsapp_webhook()` in `app/api/webhooks.py`
2. Update `_save_chat_log()` if meta structure changes

---

## TESTS

Run: `pytest tests/ -v`

Test files:
- `test_ai_brain.py` → OpenAI API mock, response validation
- `test_order_logic.py` → order validation, pricing calculation
- `test_pricing.py` → delivery fee logic, container pricing
- `test_booking_preorder.py` → booking + order linking
- `test_rate_limiter.py` → rate limit middleware
- `test_menu_sync_price.py` → iiko menu sync
- `test_stop_list_terminal_group.py` → terminal group filtering

---

## NOTES

- **Language:** Russian UI (prompts, messages), English code/comments
- **Multi-tenant:** Organization model exists but not fully active; single restaurant per deployment for MVP
- **Payment:** prepayment_status field ready, but payment provider integration (Kaspi, Halyk, etc.) pending
- **Scaling:** Redis for session/state; Alembic for DB versioning; async/await throughout
- **Errors:** Fallback to escalate on AI error; rate limit 20/min per phone; Sentry optional for prod
