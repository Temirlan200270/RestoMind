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
