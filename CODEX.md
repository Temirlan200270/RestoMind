# RestoMind — инструкции для Codex

Этот файл — **точка входа** для Codex/ИИ‑агента в репозитории.

## Что читать в первую очередь (в этом порядке)

1. `README.md` — что за продукт, как запустить, переменные окружения.
2. `codebase.md` — карта репозитория: где живёт логика и основные потоки.
3. `docs/CONVENTIONS.md` — **инварианты разработки (контракт)**.
4. `docs/ROADMAP.md` — **единственный** трекер задач/статусов (P0–P3).
5. `CHANGELOG.md` — что уже сделано (дописываем в `## [Unreleased]`).

## Правила работы (важно)

- **Source of Truth по задачам**: статусы/галочки обновляем **только** в `docs/ROADMAP.md`.
- **Source of Truth по “что сделано”**: при значимых изменениях дописываем в `CHANGELOG.md` (`## [Unreleased]`).
- **Временные планы/чеклисты спринта**: `docs/sprints/` (после спринта удаляем/вплавляем полезное в “живые” доки).

## Если правишь админку (UI)

- Шаблоны: `app/templates/` (разбито на `app/templates/screens/*` + `admin.html` как скелет).
- Карта UI-слоя: `docs/UI_MAP.md`.
- JS: `app/static/js/admin-app.js` (Alpine x-data).
- UI‑контракт: `docs/UI_DESIGN_SYSTEM.md` (a11y/Lighthouse/`ds-*`).

## Красные линии (не ломать без явного ТЗ)

- **Платежи**: `app/api/payment_webhook.py`, `app/services/payment_*`, `app/services/payment_adapters.py`.
- **Multi-tenant**: все выборки/действия должны быть scoped по `organization_id`.
- **Idempotency / versioning**: не убирать `Order.row_version`, дедуп входящих, уникальные ограничения.
- **Redis ≠ источник истины**: Redis — кэш/сессии/события, истина — БД.

