# RestoMind OS — инструкции для Codex/ИИ-агента

Этот файл — **точка входа** для Codex/ИИ‑агента в репозитории.

**RestoMind OS** — AI-операционная система для ресторанного бизнеса (не просто чат-бот). Архитектура строится послойно: Tenant Isolation → Event Core → AI Context Snapshot → Decision Engine. Перед любой архитектурной правкой читать `docs/OS_TRANSITION_PLAN.md`.

## Что читать в первую очередь (в этом порядке)

1. `README.md` — что за продукт, как запустить, переменные окружения.
2. `codebase.md` — карта репозитория: где живёт логика и основные потоки.
3. `docs/CONVENTIONS.md` — **инварианты разработки (контракт)**; Rules 9–11 — OS-инварианты (Tenant Isolation, Event-First, AI Context).
4. `docs/OS_TRANSITION_PLAN.md` — стратегический план OS, текущее состояние фаз; читать перед архитектурными решениями.
5. `docs/ROADMAP.md` — **единственный** трекер задач/статусов (P0–P4).
6. `CHANGELOG.md` — что уже сделано (дописываем в `## [Unreleased]`).

## Правила работы (важно)

- **Source of Truth по задачам**: статусы/галочки обновляем **только** в `docs/ROADMAP.md`.
- **Source of Truth по “что сделано”**: при значимых изменениях дописываем в `CHANGELOG.md` (`## [Unreleased]`).
- **Временные планы/чеклисты спринта**: `docs/sprints/` (после спринта удаляем/вплавляем полезное в “живые” доки).

## Если правишь админку (UI)

- Шаблоны: `app/templates/` (разбито на `app/templates/screens/*` + `admin.html` как скелет).
- Карта UI-слоя: `docs/UI_MAP.md`.
- JS: `app/static/js/admin-app.js` (Alpine x-data).
- UI‑контракт: `docs/UI_DESIGN_SYSTEM.md` (a11y/Lighthouse/`ds-*`).
- FSM чатов и WebSocket: `docs/STATE_MACHINE.md`, `docs/EVENT_ARCHITECTURE.md` (раздел Realtime).

## Красные линии (не ломать без явного ТЗ)

- **Платежи**: `app/api/payment_webhook.py`, `app/services/payment_*`, `app/services/payment_adapters.py`.
- **Multi-tenant**: все выборки/действия должны быть scoped по `organization_id` (Rule 9).
- **Idempotency / versioning**: не убирать `Order.row_version`, дедуп входящих, уникальные ограничения.
- **Redis ≠ источник истины**: Redis — кэш/сессии/события, истина — БД.
- **Event-First**: новые бизнес-действия — через `emit_event(BusinessEvent)` в `app/services/system_events.py` (Rule 10).
- **AI Context**: данные для LLM только через `fetch_ai_read_context`, не сырой SQL внутри вызова (Rule 11).

