# RestoMind OS — инструкции для Codex/ИИ-агента

Этот файл — **точка входа** для Codex/ИИ‑агента в репозитории.

**RestoMind OS** — AI-операционная система для ресторанного бизнеса (не просто чат-бот). Архитектура строится послойно: Tenant Isolation → Event Core → AI Context Snapshot → Decision Engine. Перед любой архитектурной правкой читать `docs/OS_TRANSITION_PLAN.md`.

## Что читать в первую очередь (в этом порядке)

1. `README.md` — что за продукт, как запустить, переменные окружения.
2. `codebase.md` — карта репозитория: где живёт логика и основные потоки.
3. `docs/CONVENTIONS.md` — **инварианты разработки (контракт)**; Rules 9–11 — OS-инварианты (Tenant Isolation, Event-First, AI Context).
4. `docs/OS_TRANSITION_PLAN.md` — стратегический план OS, текущее состояние фаз; читать перед архитектурными решениями.
5. `docs/ROADMAP.md` — **единственный** трекер задач/статусов (P0–P4).
6. `CHANGELOG.md` — **краткие релизы** (дописываем в `## [Unreleased]`); длинная история — `docs/releases/README.md`.
7. **Sales demo (G10.8):** `docs/DEMO_PITCH.md` — 30-сек pitch, explore, smoke; кнопка на login — «Посмотреть демо».
8. **WhatsApp ops:** дубли телефона / latency — `scripts/diag_duplicate_phones.py`, `scripts/diag_whatsapp_latency.py`, `scripts/merge_duplicate_users.py`; E.164 — `app/services/phone_normalize.py`.
9. **Owner Intelligence OS:** сервисы `owner_intelligence.py`, `owner_digest_delivery.py`, `menu_profit_lab.py`, `network_benchmark.py`, `order_ai_audit.py`, `upsell_*`; API `/api/admin/owner-intelligence/*`; smoke — `docs/DEPLOY_RUNBOOK.md` §8, `scripts/verify_owner_intel_schema.py`.

## Правила работы (важно)

- **Source of Truth по задачам**: статусы/галочки обновляем **только** в `docs/ROADMAP.md`.
- **Source of Truth по “что сделано”**: кратко в `CHANGELOG.md` (`## [Unreleased]`); детали эпика — `docs/releases/`. Политика: [`docs/releases/README.md`](docs/releases/README.md).
- **Временные планы/чеклисты спринта**: `docs/sprints/` (после спринта удаляем/вплавляем полезное в “живые” доки).

## Если правишь админку (UI)

- Шаблоны: `app/templates/` (разбито на `app/templates/screens/*` + `admin.html` как скелет).
- Карта UI-слоя: `docs/UI_MAP.md` (вкладки `ai_center`: value / insights / load / **owner_intel** / **network_benchmark** / os / guestcare / final_mile).
- JS: `app/static/js/admin-app.js` (Alpine x-data; WS: `os.audit`, business events).
- UI‑контракт: `docs/UI_DESIGN_SYSTEM.md` (a11y/Lighthouse/`ds-*`; тексты — язык оператора, см. CONVENTIONS §8).
- FSM чатов и WebSocket: `docs/STATE_MACHINE.md`, `docs/EVENT_ARCHITECTURE.md`.
- OS / Intelligence API: `docs/AI_OPERATIONS.md`; Final Mile backend: `docs/FINAL_MILE_IMPLEMENTED.md`; UI gaps: `docs/REMAINING_UPDATES.md`.

## Красные линии (не ломать без явного ТЗ)

- **Платежи**: `app/api/payment_webhook.py`, `app/services/payment_*`, `app/services/payment_adapters.py`.
- **Multi-tenant**: все выборки/действия должны быть scoped по `organization_id` (Rule 9).
- **Idempotency / versioning**: не убирать `Order.row_version`, дедуп входящих, уникальные ограничения.
- **Redis ≠ источник истины**: Redis — кэш/сессии/события, истина — БД.
- **Event-First**: новые бизнес-действия — через `emit_event(BusinessEvent)` в `app/services/system_events.py` (Rule 10).
- **AI Context**: данные для LLM только через `fetch_ai_read_context`, не сырой SQL внутри вызова (Rule 11).

