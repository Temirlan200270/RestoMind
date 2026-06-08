# Changelog

Краткий журнал **релизов RestoMind OS**. Детали (файлы, миграции, тесты) — в [`docs/releases/`](docs/releases/README.md).

| Документ | Назначение |
|----------|------------|
| [`docs/ROADMAP.md`](docs/ROADMAP.md) | Задачи и статусы |
| **CHANGELOG.md** (этот файл) | Что **отгрузили** |
| [`docs/releases/`](docs/releases/README.md) | Длинная история по эпикам |

Формат секций: `[YYYY.MM — Epic]` + дата закрытия этапа. Правила — [`docs/releases/README.md`](docs/releases/README.md).

---

## [Unreleased]

### Docs
- Синхронизация доков с Postgres-only и текущим Alembic: убраны хардкоды revision id, SQLite-патчи из CONVENTIONS, таблица приоритета Redis, Rule 12 (UTC/tz), X1 staged vs freeze, дедуп AI_OPERATIONS → INTELLIGENCE_OS_PLAN; stubs в `docs/archive/`.
- Второй проход аудита: SUPPLYMIND_STAFFMIND gaps закрыты; SECURITY_AUDIT ↔ RLS `20260609_tenant_rls`; GuestCare Google WONTFIX в AI_OPERATIONS; разделение Daily OS vs Owner digest; уточнение E5 BackgroundTasks в ROADMAP.
- Executive OS docs: `AI_OPERATIONS.md` § Executive Hub v2 (default landing, preview drawer, Telegram confirm, command registry, action chain); исправлена кодировка `app.organization_id` в ROADMAP § аудит OS vs SaaS-UI.

### Added
- Executive OS evolution (6 блоков): Hub default landing (`executive_hub_default_enabled` в `/auth/me` + org meta), proactive apply из `insight_delivery` с `agent_action_proposal_id` и signed Telegram link (`GET /api/public/agent-actions/confirm`), lifecycle `proposed→previewed→confirmed→applied` (`POST .../preview`), registry v2 `app/services/agent_commands/`, lineage `GET .../agent-actions/{id}/chain`, guarded `IikoWriteAdapter` (`IIKO_LIVE_WRITE_ENABLED`).
- Executive OS command foundation: `agent_action_proposals` оформлены как валидируемые команды (`ForceCloseRestaurantCommand`, `CreateUpsellRuleCommand`, `StageIikoWriteCommand`), добавлен `GET /api/admin/intelligence/agent-actions/commands`, 422 для невалидных payload и audit events `agent_action.proposed|confirmed|applied|rejected`.
- Snapshot learning loop MVP: `POST /api/admin/intelligence/snapshots/{id}/feedback` + кнопка «ИИ ошибся» в списке snapshots записывают коррекцию в `organization_memory_events`.
- Executive Hub v2: NLG-виджеты Health/Money/Quality/Ops, action cards (navigate/chat/agent_action), API `dimensions` в `GET /api/admin/intelligence/executive-hub`.
- Human-in-the-loop agent actions: `POST /api/admin/intelligence/agent-actions/propose|confirm|reject`, force-close, upsell rule create, staged iiko write; детект из `/intelligence/query`.
- Postgres RLS last-line defense: `app/db/tenant_rls.py`, middleware, миграция `20260609_tenant_rls`.
- Parallel `fetch_ai_read_context`: три независимых DB-сессии вместо одного sequential fetch.
- Executive Hub v1: overlay для manager/admin с narrative cards, drill-down drawer и чатом агента; API `GET /api/admin/intelligence/executive-hub`.

### Fixed
- Deploy: миграция `20260608_pg_parity_index_bool` больше не пересоздаёт `ix_orders_org_kind` (индекс уже есть с `20260514_night_preorders`).
- DB: полный переход тестов/CI/runtime на PostgreSQL — SQLite startup DDL и `aiosqlite` удалены, тестовая схема и GitHub Actions работают через Postgres.
- CI: `test_fulfillment_only_order` — мок `check_operational_status` (открытая кухня), тест больше не падает ночью из‑за `night_preorder`.

- Code/test cleanup: removed tracked generated `scripts/_nomenclature_summary.txt`, fixed the broken Windows `post_nomenclature.cmd` path by adding `scripts/post_nomenclature.ps1`, and updated `_summarize_nomenclature.py` so reports are stdout/`--out` only.
- Docs cleanup: obsolete root debug dumps (`nomenclature.json`, `payload.json`) and old sprint scratch docs were removed; tracked admin baseline PNGs were kept because `docs/UI_DESIGN_SYSTEM.md` embeds them. Deployment/status docs now point to the current runbook, roadmap, and Alembic head `20260603_menu_item_lifecycle`.

### Intelligence OS — единый слой данных и AI-Аналитик

- iiko OLAP sales layer: Cloud + Server, факт-таблицы продаж, daily/hourly агрегаты, backfill CLI и ARQ cron.
- AI Center получил таб «Продажи»; добавлены `/analytics/sales/*` API.
- `/intelligence/query` переведён на safe tool-based Copilot; добавлены sales anomalies, demand-driven SupplyMind, ROI outcomes и guarded autonomous drafts.
- Архитектурная корректировка: добавлены `CUSTOMER.md`, D0 Data Quality, C1.5 Explainability + Confidence, M1 Organization Memory, relational C2 Knowledge Graph; X1 Autonomy перенесён в future.
- Закрыт основной gap до Restaurant OS trust-layer: canonical-first pipeline, lineage/reconciliation, deep drilldown, proactive delivery, memory autogeneration, graph ETL, forecasting v2, ROI chain/digest и preliminary live sales.
- Добит trust-layer gap: fact build переведён на canonical-first, добавлены lineage fields, schema hash, reconciliation report, Copilot `get_data_lineage`, deep drilldown category/dish/hour, delivery action endpoints, memory autogeneration и preliminary live sales preview.
- Доведены C2/O1/F1/D1.2/P1: graph profile rebuild для Menu Profit Lab, seasonality + dirty-data weighting в forecast, ROI chain API и ROI-блок в owner digest, per-org delivery settings и timezone normalization для naive iiko OLAP timestamps.
- Закрыт D3.1 Trust UI: AI Center показывает confidence badge, evidence list и drilldown path на карточках инсайтов.
- Добит продуктовый слой Intelligence OS: role-based quick questions в Copilot, AI Center inbox для insight deliveries с read/dismiss/action_taken, UI настроек delivery rules, ROI-блок "совет -> выполнено -> результат", supplier/campaign memory autogeneration и baseline causal drilldown по category/dish/hour.
- Исправлен деплойный Alembic-fail на Postgres: revision id `20260603_intelos_sales_copilot_layer` укорочен до `20260603_intelos_sales_copilot`, чтобы помещаться в `alembic_version.version_num varchar(32)`; добавлен регрессионный тест на длину revision id.
- Исправлена автоподгрузка себестоимости: ручная iiko sync теперь запускает food-cost enrichment, а `iiko_food_cost_sync` использует общий Cloud/Server sales client; iiko Server получает себестоимость через OLAP STOCK.
- Уточнён food-cost sync: `IIKO_DATA_SOURCE=server` из `.env` больше не перебивается дефолтным `Organization.iiko_data_source=cloud` для дефолтной организации; парсер распознаёт вложенные/альтернативные поля iiko и пишет диагностику, если строки себестоимости не пришли, не распознаны или не совпали с меню.
- Исправлен ложный перевод на оператора при AI timeout: enriched technical fallback больше не смешивается с plov-kazan подсказкой и не переводит гостя в sticky `HUMAN_MODE`.
- Исправлен сценарий короткого уточнения блюда: если гость отвечает «Праздничный», а в меню есть несколько подходящих вариантов плова, система больше не выбирает один случайно и не отвечает «не разобрал», а просит выбрать конкретный вариант; админский чат дедуплицирует повторные WS/REST события по `chat_log_id`.
- Усилена FSM без тяжёлой блокировки: добавлен `User.session_version` для stale LLM writes, поздний LLM-ответ не перезаписывает состояние/ожидающий заказ после takeover; `HUMAN_MODE` получил TTL и авто-возврат по явным новым сценариям, если нет ручной паузы AI.
- Усилены runtime-guards: `chat_serializer` получил owner-token lock и корректный decode Redis bytes при drain очереди, `_menu_ctx_cache` ограничен LRU/max-size, фоновые циклы используют owner-token Redis locks; `orders.iiko_last_error` расширен до `TEXT`.
- Закрыт P0 reliability-pass: fire-and-forget задачи переведены на tracked helper с логированием ошибок, ключевые silent `except` получили диагностику, FSM state в admin/payment-choice ветках пишется в транзакции вызывающего, chat log retention/menu sync больше не выполняют unscoped tenant-операции.
- Улучшена диагностика GuestCare 2GIS sync: если 2GIS возвращает защитную страницу `/museum` вместо HTML с отзывами, админка показывает явную ошибку `2gis_antibot`, а не успешное `найдено 0`.
- Доработан GuestCare 2GIS parser: авто-sync нормализует firm URL на `/tab/reviews`, извлекает server-rendered карточки отзывов из HTML 2GIS, показывает 24h-блок по подтянутым отзывам, а ручной URL без текста больше не отображается как отзыв «Импортировано из 2gis».

### Документация — реструктуризация CHANGELOG

- Трёхуровневая модель: ROADMAP (задачи) → CHANGELOG (релизы, ~100 строк) → `docs/releases/` (детали эпиков).
- Политика: [`docs/releases/README.md`](docs/releases/README.md); архив старого журнала сохранён без потерь.

### Operator reliability guards (90% path)

- Guard-слои: `fulfillment_infer`, `upsell_safety_gate`, `order_confirm_gate`, stale draft reset, fulfillment gate на «Да», technical fallback без sticky `HUMAN_MODE`.
- Evals: golden dialogs (starter), prompt snapshot tests; метрики `llm_reliability` в Owner Intelligence API + UI.
- **Плов на стопе:** `plov_kazan_schedule.py` — для любой позиции с пловом на стопе: слоты казанов (12:00/16:00/19:00), ближайшее время в промпт + fallback в ответе.
- **LLM latency (OpenAI + Gemini):** GA-first cascade Gemini, таймаут `AI_LLM_TIMEOUT_SEC`, умный skip `fast→strong`, обрезка `menu_context` при oversize; soft budget промпта `PROMPT_MAX_TOKENS_SOFT` 10k (hard 14k).
- Подробнее: [`docs/releases/2026.06-owner-intelligence.md`](docs/releases/2026.06-owner-intelligence.md) (блок reliability).

### Menu Profit Lab — management value

- Рекомендации по цене, чеклист missing cost, promote today для copilot; UI в OI и каталоге меню.
- iiko меню получило lifecycle-поля (`source`, `last_seen_iiko_sync_at`, archive flags) и replace/prune sync: отсутствующие в новой выгрузке iiko позиции архивируются/удаляются только по явному подтверждению; food-cost вынесен в отдельный scheduled source (`product_expenses` → OLAP STOCK fallback).

---

## [2026.06 — Owner Intelligence OS] — 2026-06-04

Продуктовый слой для владельца: ROI, допродажи, QA-аудит, Kitchen Gate, сеть.

- **Owner Intelligence MVP → sales-ready (P0–P6):** summary API, QA auto-audit, upsell attribution, Kitchen Gate v2, Menu Profit Lab, Network Benchmark.
- **Revenue Copilot v3:** pair mining, scoring explainability, Smart Sales UI, anti-repeat / frequency penalties.
- **Channels:** Telegram customer (per-org webhook, WA/TG badges), POS adapter (`IikoPOSAdapter`, r_keeper foundation).
- **Weekly digest:** Telegram owner report, cron + manual send, Redis dedupe.
- **Performance Pack:** quick replies, FAQ cache, async event consumers, E.164 dedupe, queue wait metrics.
- **Deploy:** smoke `verify_owner_intel_schema`, runbook §8.

Детали: [`docs/releases/2026.06-owner-intelligence.md`](docs/releases/2026.06-owner-intelligence.md)

---

## [2026.05 — Execution OS (G10 + Focus-Driven Admin)] — 2026-05-24

Операционная оболочка: деньги смены, inbox, demo pitch, три режима админки.

- **Money Core (G6–G10):** Live Pulse, Draft Recovery, Inbox money queue, Revenue Leak → actions, Shift Control, Next Action Mode.
- **Focus-Driven OS (Sprint 1–4):** Mode Engine (shift/control/intelligence), Shift split, Action Queue, Command Bar, mobile staged nav.
- **Wow / Demo (G10.6–G10.8):** operational scene, predictive shift, 30s demo autoplay, zero-friction explore.
- **Final Mile (backend+UI):** SupplyMind, StaffMind, Voice call log, GuestCare 2GIS, Control Plane trace.
- **Superadmin + Control Plane:** audit log, trace_id propagation, BI MVP tails.

Детали: [`docs/releases/2026.05-execution-os.md`](docs/releases/2026.05-execution-os.md)

---

## [2026.05 — OS Foundation (Phases 1–5)] — 2026-05-19

Переход SaaS → OS: tenant, events, snapshot, decision engine, predictive layer.

- **Phase 1:** `Tenant.is_network`, Branch Switcher, location scope, manager RBAC.
- **Phase 2–3:** `emit_event`, `DailyOrgStats`, AI Context Snapshot + replay, `event_slice`.
- **Phase 4:** Decision Engine (8+ rules), billing/stop-list/hallucination guards.
- **Phase 5 (~98%):** predictive forecasts, autopilot pricing, audit consumer, self-healing, event-first `/stats` и `/analytics`.

Детали: [`docs/releases/2026.05-os-foundation.md`](docs/releases/2026.05-os-foundation.md)

---

## [2026.04 — Platform & Admin UX] — 2026-05-10

Мультитенантность, redesign админки, Intelligence MVP, платёжные провайдеры UI.

- **Admin split + design system:** `screens/*`, `ds-*`, Phase U1–U7, Lighthouse, IA P1.5 (inbox, ai_center).
- **E0/E2:** admin API package, `select-org`, tenant owner scope, superadmin tools.
- **AI Operations MVP:** `/intelligence/*`, Digital Twin, event architecture docs.
- **P0 stability:** transient AI retry, force-close, token usage log, test_bot 3-phase.

Детали: [`docs/releases/2026.04-platform-admin.md`](docs/releases/2026.04-platform-admin.md)

---

## Архив

- **Полный построчный дамп** (до реструктуризации 2026-05-26): [`docs/releases/archive-detailed.md`](docs/releases/archive-detailed.md)
- **Ранний продуктовый baseline** (ядро WhatsApp + iiko + admin): [`docs/releases/legacy-product-baseline.md`](docs/releases/legacy-product-baseline.md)
- **Неклассифицированные записи:** [`docs/releases/2026-misc-unclassified.md`](docs/releases/2026-misc-unclassified.md)
