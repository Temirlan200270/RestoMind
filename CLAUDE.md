# RestoMind — ориентация для Claude Code

## Что это
**RestoMind OS** — AI-операционная система для ресторанного бизнеса: WhatsApp → LLM → заказы/брони → iiko. Монорепо: FastAPI backend + Jinja2/Alpine.js/Tailwind фронт + Alembic миграции. Архитектура строится по 5-фазовому OS Transition Plan ([`docs/OS_TRANSITION_PLAN.md`](docs/OS_TRANSITION_PLAN.md)).

## Быстрый вход (если времени мало)
- `CODEX.md` — короткая точка входа: что читать, где трекать задачи, красные линии.

## Ключевые документы (читать перед работой)
- **README.md** — продукт, быстрый старт, переменные окружения, вход в админку
- **codebase.md** — карта репозитория, где какая логика живёт
- **docs/CONVENTIONS.md** — инварианты разработки (контракт): async-first, idempotency, versioning, “Redis ≠ source of truth”; **Rules 9–11** — Tenant Isolation, Event-First, AI Context через ContextBuilder
- **docs/OS_TRANSITION_PLAN.md** — стратегический план перехода SaaS → OS (5 фаз); текущее состояние каждой фазы; читать перед любой архитектурной правкой
- **docs/ROADMAP.md** — **единственный** трекер задач/статусов (P0–P4); по завершению: `[x]` в ROADMAP + запись в `CHANGELOG.md`
- **CHANGELOG.md** — история изменений (дописывать в `## [Unreleased]`)
- **docs/sprints/** — временные мини‑родмапы/чеклисты на 1–2 недели (оперативка; после спринта удалять/вплавлять; статусы задач всё равно только в `docs/ROADMAP.md`)
- **docs/UI_DESIGN_SYSTEM.md** — при правке UI: компоненты `ds-*`, токены, a11y, Lighthouse
- **docs/UI_MAP.md** — карта `admin.html` / `screens/*` / макросов и `admin-app.js`
- **docs/AI_TOOLS_SETUP.md** — настройка Cursor / Claude Code / MCP под этот репозиторий
- **docs/AI_OPERATIONS.md** / **docs/EVENT_ARCHITECTURE.md** — Intelligence, события, инсайты

## Жёсткие запреты
1. **Миграции** — никогда не редактировать уже применённые файлы в `alembic/versions/`; новая миграция только по явному запросу.
2. **Межтенантная изоляция** — все запросы к БД фильтровать по `organization_id`; никогда не возвращать данные чужого филиала.
3. **LLM не внутри DB-сессии** — паттерн: `async with db` (чтение) → закрыть → LLM → новый `async with db` (запись). См. `app/api/admin/test_bot.py`.
4. **Платёжная логика** — не трогать `app/api/payment_webhook.py` и верификацию подписей без явного ТЗ.
5. **Мультитенантность** — не менять схему `Organization`/`Tenant`/роли без полного анализа последствий для всех org.

## Архитектурные ориентиры
- Точка входа WhatsApp: `app/api/webhooks.py` → `process_message` → `intent_router`
- Весь admin API пока в `app/api/admin/_monolith.py` (E0.1 — дробим постепенно)
- Tailwind собирается: `src/css/admin-input.css` → `app/static/css/admin.css` (не редактировать `admin.css` напрямую)
- CSS: предпочитать `ds-*` классы и CSS-токены над утилитами `brand-*`
