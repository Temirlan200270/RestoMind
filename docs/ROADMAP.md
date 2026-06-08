# RestoMind — Roadmap & Single Source of Truth

Единственный файл для отслеживания статусов задач, багов и технического долга.

**Правило для ИИ:** при завершении задачи ставить галочку `[x]` здесь и делать запись в `CHANGELOG.md`. Другие “планы/трекеры” больше не обновляем.

Временные мини‑родмапы/чеклисты на 1–2 недели — в `docs/sprints/` (но статусы задач всё равно только здесь).

## 💸 Money MVP — Детектор утечек (Current Focus)

> Rule 0: любой код показывает владельцу потерю денег или помогает её вернуть.

- [x] **G1 — Revenue Leak Service:** [`app/services/revenue_leak.py`](app/services/revenue_leak.py) — три источника потерь: `abandoned_drafts_kzt` (DRAFT > 1ч × AOV), `slow_response_kzt` (ждал > 5мин × AOV×0.5), `cancelled_today_kzt` (реальная сумма). `GET /api/admin/intelligence/revenue-leak`.
- [x] **G2 — Hero Block «Упущено сегодня»:** Красный баннер вверху дашборда. Три карточки breakdown. `loadRevenueLeak()` в [`admin-app.js`](app/static/js/admin-app.js), загружается при открытии «Главная».
- [x] **G3 — Узнавание гостя:** [`personalization.py`](app/services/personalization.py) — добавлены `top_items` (топ-2 блюда) и `disliked` (отклонял 2+ раза). В [`webhooks.py`](app/api/webhooks.py) вставляется одна строка в промпт: `«Профиль гостя: обычно берёт X; не берёт Y»` — бот сам скажет «Как обычно?».
- [x] **G4 — Auto-Short Mode:** При > 3 чатах ждущих > 5 мин — бот получает `[КРАТКИЙ РЕЖИМ]` в промпт. Redis-счётчик `org:{id}:slow_chats` (TTL=10мин), инкремент при входящем если прошло > 5мин, декремент после ответа.
- [x] **G5 — Live Pulse в чатах:** 🟢 &lt;2м / 🟡 2–5м / 🔴 &gt;5м по времени ожидания ответа гостю (`last_role=user` + `wait_seconds`). Сортировка: красные сверху. API: `pulse`, `last_role`, `wait_seconds` в `GET /chats`. UI: `_tab_chats.html`, `chatPulseStatus()` в [`admin-app.js`](app/static/js/admin-app.js).
- [x] **G6 — Draft Recovery:** ARQ `draft_recovery_scheduled_tick` (каждые ~10 мин) — WA-nudge для DRAFT &gt;45 мин с кнопками «Оформить»/«Отменить», dedupe 1×/24ч на заказ, восстановление `CONFIRMING_ORDER`. [`draft_recovery.py`](app/services/draft_recovery.py).
- [x] **G7 — Inbox = money queue:** единая очередь «Деньги на кону» — брошенные DRAFT (&gt;30 мин), pending prepay, медленные чаты (pulse amber/red). `GET /api/admin/inbox/money-queue`, [`money_queue.py`](app/services/money_queue.py), UI в `_tab_operator_queue.html`.
- [x] **G8 — Revenue Leak → Action Layer:** дашборд с 1-кликовыми действиями по каждой утечке (вернуть черновики, открыть красные чаты, очередь оплат). `surfaces[]` в `GET /revenue-leak`, `POST /revenue-leak/recover-drafts`, UI `_tab_dashboard.html`.
- [x] **G9 — Shift Control Screen:** единый экран смены — focus queue, live chats, orders strip, leak summary. UI: [`_tab_shift_control.html`](app/templates/screens/_tab_shift_control.html) + G10 `GET /shift/state`; стартовая вкладка для `operator`. Legacy `GET /shift-control` удалён в G10.3.
- [x] **G10 v1 — Next Action Mode:** … См. [`shift_state_engine.py`](app/services/shift_state_engine.py), [`G10_SHIFT_CONTROL_PLANE.md`](docs/G10_SHIFT_CONTROL_PLANE.md), [`G10_SEMANTIC_CONTRACT.md`](docs/G10_SEMANTIC_CONTRACT.md).
- [x] **G10.1 — Shift Trust Layer (часть):** … v1.1.
- [x] **G10.2 — Semantic Hardening:** projection diff, ownership, SET prune, UI invariant, action hints — [`G10_SEMANTIC_CONTRACT.md`](docs/G10_SEMANTIC_CONTRACT.md) §10.
- [x] **G10.2 tail:** S1 hysteresis, API degraded UI, failure simulation — [`G10_FAILURE_SIMULATION.md`](docs/G10_FAILURE_SIMULATION.md).
- [x] **G10 Production Hardening v1:** chat serialization, focus heartbeat API, healing realtime, S1 hysteresis, degraded UI (промежуточные примитивы superseded simplification).
- [x] **G10 Simplification Map:** lock+queue, `active_focus` lease, `heal:mute` — [`docs/G10_SIMPLIFICATION.md`](docs/G10_SIMPLIFICATION.md), контракт §12 [`G10_SEMANTIC_CONTRACT.md`](docs/G10_SEMANTIC_CONTRACT.md). **Freeze новых consistency-слоёв.** Доки синхронизированы 2026-05-20.
- [x] **FM-3 metric + recovery:** `GET /shift/state` exposes `metrics.shift_empty_focus_while_risk_positive` and logs `shift_empty_focus_while_risk_positive` when risk is positive but focus is empty; UI offers `reset_skips` CTA to show skipped/next items again while completed items stay closed.
- [x] **G10.3 — Legacy `/shift-control` removal:** удалён `GET /api/admin/shift-control`; `shift_control.py` — только `_saved_today_kzt`; heartbeat без `owner_token` (JS + API).
- [x] **Money Layer v2 — Recovered $:** `recovered_kzt` / `focus_completed_count` в `daily_org_stats`; события `shift.focus_completed`, `order.draft_recovered`; метрики `recovered_today_kzt` + `confirmed_revenue_today_kzt` в shift/state и UI.
- [x] **Money Layer v2 — Queue gaps:** `menu_confusion`, `booking_at_risk` в `money_queue.py`; slow_chat с AOV×0.5; action surfaces на дашборде.
- [x] **Money Layer v2 — iiko hourly ETL (lite):** таблица `sales_hourly_daily`, cron `sales_hourly_iiko_scheduled_tick`, `GET /analytics/sales-heatmap`, heatmap в расширенной аналитике.

## 🧠 Intelligence OS — Restory-class слой данных и AI-Аналитик

- [x] **P0 — Customer Model:** [`docs/CUSTOMER.md`](docs/CUSTOMER.md) фиксирует покупателей, ежедневные вопросы и first paid use cases; Copilot должен отвечать на них, а не на произвольную аналитику.
- [x] **D0 — Data Quality Layer:** `source_data → validation → normalization → canonical schema → fact tables`; добавлены raw snapshots, canonical sales/product tables, `data_quality_reports`, confidence и статус в sales overview.
- [x] **D1 — Unified iiko OLAP sales layer:** Cloud OLAP SALES + Server OLAP v2, per-org `iiko_data_source`, fact tables `sales_fact_orders/items`, `sales_daily_agg`, hourly `source=iiko_olap`, ARQ cron, backfill CLI.
- [x] **D2 — Food cost:** `product_expenses`/STOCK fallback обновляет `MenuItem.cost_price` и `SalesFactItem.cost`, поэтому Menu Profit Lab получает реальную себестоимость.
- [x] **D3 — Sales data UX:** AI Center → «Продажи» + `/analytics/sales/*` endpoints для overview/top-dishes/categories/hour.
- [x] **A1 — Sales anomalies:** `sales_anomaly_engine.py` создаёт `OperationalInsight` по падению выручки к OLAP baseline.
- [x] **C1 — Tool-based AI Analyst:** `/intelligence/query` использует safe read-only Copilot tools вместо эвристики/сырого SQL.
- [x] **C1.5 — Explainability + Confidence:** `OperationalInsight` имеет `confidence_score`, `evidence_json`, `drilldown_json`; anomaly engine и Copilot возвращают вероятные причины с основанием.
- [x] **M1 — Organization Memory:** добавлены `organization_memory_events`, API ручных заметок, автозапись resolved insight/ROI, Copilot memory tools.
- [x] **C2 — Restaurant Knowledge Graph (relational):** добавлены таблицы dish/ingredient/supplier/margin/seasonality/substitution и Copilot tools для margin risk, price simulation, supplier exposure, seasonality.
- [x] **O1 — Demand-driven SupplyMind:** черновик закупки учитывает OLAP demand multiplier.
- [x] **F1 — ROI feedback loop:** `recommendation_outcomes` + scheduled measurement + `recommendation.measured` event.
- [x] **X1 — Autonomy (future):** autonomous draft явно помечен как `experimental/internal`, без внешних действий и без мутаций в iiko/закупках без подтверждения.

### Intelligence OS — gap to 100%

> Текущий статус: trust-layer MVP готов. Ниже не "переделать заново", а добить продуктовую полноту до Restaurant OS, где каждая цифра доказуема, объяснима и связана с ROI.

- [x] **D0.1 Canonical-first fact build:** `sales_fact_orders/items` строятся из `canonical_sales_orders/items`, а raw OLAP rows используются только для snapshot/debug.
- [x] **D0.2 Quarantine + lineage:** сохраняются duplicate quarantine samples, `snapshot_id`/canonical lineage для facts, checksum и source schema/fields hash.
- [x] **D0.3 Reconciliation:** сверяется `sum(items.revenue)` с `order.revenue`; расхождения пишутся в `data_quality_reports`.
- [x] **P0.1 Role-based Copilot UX:** `/api/admin/intelligence/business-questions` отдаёт вопросы по роли (`owner`, `manager`, `network`, `franchise`), `/intelligence/query` прокидывает роль в tool selection, AI Center показывает сценарии по роли пользователя.
- [x] **C1.6 Data lineage tool:** Copilot tool `get_data_lineage(metric, period)` возвращает snapshot, checksum, quality report, fact counts и reconciliation status.
- [x] **C1.7 Deep drilldown:** revenue delta раскладывается на orders/guests/avg_check и baseline contribution по category/dish/hour с quantity/revenue deltas.
- [x] **D3.1 Trust UI:** AI Center показывает confidence badge, evidence list и drilldown path на карточках инсайтов; sales tab показывает data quality status/confidence.
- [x] **P1 Proactive inbox:** `OperationalInsight` становится источником proactive delivery; добавлены read/dismiss/action_taken endpoints и AI Center блок «Требует внимания».
- [x] **P1 Delivery rules:** per-org настройки severity -> channel в `Organization.meta_json`, API settings/history/actions, dedupe по insight/channel; AI Center умеет менять правила уведомлений.
- [x] **M1.1 Memory autogeneration:** menu import, price change, cost update/import, supplier graph link, marketing campaign, resolved anomaly и measured ROI создают `organization_memory_events`.
- [x] **C2.1 Graph ETL:** `restaurant_graph.rebuild_restaurant_graph_profiles()` наполняет `dish_ingredients`, `ingredient_suppliers`, `dish_margin_profile`, `dish_seasonality_profile` из меню/cost/inventory/sales facts; будущие 1C/Sheets подключаются как источники тех же профилей.
- [x] **C2.2 Menu Profit Lab v2:** Menu Profit Lab пересобирает graph profiles и читает `DishMarginProfile` как основной источник margin/cost, с fallback на `MenuItem.cost_price`.
- [x] **O1.1 Forecasting v2:** forecast использует seasonality profile, dirty-data weighting, memory events и confidence по basis rows.
- [x] **F1.1 ROI chain API + UI:** `/api/admin/intelligence/roi-outcomes` показывает цепочку "совет -> выполнено -> результат" с baseline/measurement windows, confidence и causality label; AI Center показывает ROI-блок.
- [x] **F1.2 ROI digest:** owner digest включает realized money, confidence и пометку качества данных по измеренным рекомендациям.
- [x] **D1.1 Live/open orders layer:** отдельный preliminary слой для открытых заказов; не смешивается с закрытыми OLAP facts без метки.
- [x] **D1.2 Timezone normalization:** canonical OLAP close time нормализуется через timezone организации; naive iiko timestamps трактуются как local org time и сохраняются в UTC.
- [x] **X1.1 Autonomy freeze:** внешняя автономность оставлена future/experimental; без внешних iiko/закупочных мутаций без подтверждения человека.

## 🔴 P0: Критический техдолг и баги (делать сейчас)

> Перенесено из бывшего `problems.md` (2026‑05): потенциальные data leaks, потеря/дубли сообщений, рассинхроны state и гонки UI.

- [x] **PostgreSQL-only runtime/tests/CI:** тесты и GitHub Actions переведены на `postgres:16`; SQLite startup DDL, sqlite upsert branches и `aiosqlite` удалены; runtime schema управляется Alembic/Postgres.
- [x] **test_bot: LLM вне DB‑сессии:** чтение → LLM (без сессии) → запись; DB‑соединение не держим во время ответа модели (`app/api/admin/test_bot.py`).
- [x] **Telegram fire‑and‑forget:** realtime путь не ждёт Telegram-уведомления (`app/services/events.py`: `asyncio.create_task(...)` вместо `await`).
- [x] **Экстренное закрытие (полный стек):** `Organization.force_closed_until/force_closed_reason` + миграция + учёт в `time_context.py` + `POST /api/admin/organization/force-close` + UI (профиль, модалка, причина).
- [x] **Счётчик токенов (полный стек):** `AiUsageLog` + upsert индекс `(organization_id, day)`; `_usage` в `AIBrainResponse`; заполнение в `openai_p.py`; fire-and-forget upsert; UI «Токены сегодня».
- [x] **Dashboard mobile: статус работы на всех экранах:** 🟢/🟡/⚫️ + красный бейдж «⛔ Временно закрыто» при force-close (клик ведёт в настройки).

- [x] **Data leak меню между организациями:** [`load_available_menu`](app/services/order_logic.py) требует `organization_id: int`; без скоупа вызов невозможен; ветка legacy `MenuItem.organization_id IS NULL` в загрузке меню для бота убрана. [`validate_order`](app/services/order_logic.py) при загрузке из БД без `organization_id` бросает `ValueError`. Регресс‑тесты: `tests/test_order_logic.py`, `tests/test_intent_phase18.py`, `tests/regression/test_upsell_anti_repeat.py`.
- [x] **WhatsApp inbound dedupe durable handoff:** атомарный старт в БД (`try_start_whatsapp_inbound_in_db`) в [`process_with_retry`](app/api/webhooks.py) до обработки; `mark_whatsapp_inbound_done` / `failed` + commit; Redis [`redis_whatsapp_inbound_done_cache_hit`](app/services/whatsapp_idempotency.py) только после успешного commit `done` (раннего Redis‑preclaim до DB‑claim нет).
- [x] **OpenAI timeout masking → retry:** transient‑ошибки (`RateLimitError | APIConnectionError | APITimeoutError | APIError 429/5xx`) превращаются в `TransientAiError` в [`app/services/ai_engine/openai_p.py:267-271`](app/services/ai_engine/openai_p.py); диспетчер [`app/services/ai_brain.py:247`](app/services/ai_brain.py) пробрасывает их (`raise_on_transient=True` по умолчанию); внешний цикл `_enqueue_processing` ([`app/api/webhooks.py:790-813`](app/api/webhooks.py), `MAX_RETRIES=3`, exp back‑off) делает повтор. Аналогично в `gemini_p.py`.
- [x] **Source of Truth для dialog state:** переходы из оплаты/подтверждения зеркалятся в БД через [`sync_user_dialog_state_to_db_then_redis`](app/services/dialog_mgr.py) (вызовы в [`app/api/webhooks.py`](app/api/webhooks.py): prepay → CHATTING, CONFIRMING_ORDER, выход из `AWAITING_ORDER_PAYMENT` / `CONFIRMING_ORDER` в CHATTING). Главный путь `route_intent` остаётся DB‑first в транзакции → commit → Redis.
- [x] **Operator outbound: отправка наружу только после фиксации ChatLog:** в [`admin_send_message`](app/api/admin/chats.py) и [`resend_failed_chat_message`](app/api/admin/chats.py) сначала пишется `ChatLog(delivery_status='sending')` + commit, затем `await send_message(...)` в WhatsApp, потом [`finalize_outbound_delivery`](app/services/chat_delivery.py) обновляет `delivery_status` и `provider_message_id` + commit; при ошибке провайдера статус становится `failed`, запись остаётся в БД. Регресс: [`tests/test_admin_operator_outbound.py`](tests/test_admin_operator_outbound.py), [`tests/test_admin_multitenant_ws_resend.py`](tests/test_admin_multitenant_ws_resend.py).
- [x] **UI: race-condition в заказах (REST vs WS):** в [`app/static/js/admin-app.js:6159-6210`](app/static/js/admin-app.js) реализован seq‑guard (`_ordersLoadSeq` отбрасывает устаревшие ответы REST) и merge по `row_version` (REST не перетирает более свежие WS‑данные).
- [x] **Admin UI refactor (split + lazy DOM):**
  - [x] Первая фаза — статичный split: [`app/templates/admin.html`](app/templates/admin.html) сократился до ~75 строк и собирается из 27 экранов в [`app/templates/screens/`](app/templates/screens/) через `{% include %}` (login, sidebar, header, banners, 11 табов, 8 экранов настроек, modals, bottom_nav).
  - [x] Вторая фаза — «ленивый DOM»: тяжёлые табы (`_tab_chats.html`, `_tab_orders.html`, блок `_tab_settings_*`, `dashboard`, `menu`, `ai_center`, `marketing`) монтируются после первого визита (`lazyTabMount` + `template x-if` в [`admin.html`](app/templates/admin.html), [`admin-app.js`](app/static/js/admin-app.js)); маркетинг — lazy chunk [`admin-marketing.js`](app/static/js/admin-marketing.js). Long-cache `Cache-Control: immutable` на `/static/*` (JS/CSS/fonts/images) через [`LongCacheStaticFiles`](app/middleware/static_cache.py). Метрики Lighthouse: опционально `npm run lh:admin` до/после.

## 🟡 P1: Ближайший спринт (Core SaaS)

- [x] **E0.1.x: ликвидация `_monolith.py`.** `_monolith.py` — compatibility shim; protected REST разбит по доменам: [`demo.py`](app/api/admin/demo.py), [`settings_ops.py`](app/api/admin/settings_ops.py), [`export.py`](app/api/admin/export.py), сборка в [`core.py`](app/api/admin/core.py). `auth_router`/`ws_router` — [`auth.py`](app/api/admin/auth.py) / [`ws.py`](app/api/admin/ws.py); test-bot — [`test_bot.py`](app/api/admin/test_bot.py). Исправлен отсутствующий `@router.get("/settings/environment")` + поля `RedisPurgePhoneBody`.
- [x] **E2.2 Branding (backend):** [`Tenant.brand_name`/`brand_color_hex`/`brand_logo_url`](app/db/models.py) + миграция [`20260511_e22_tenant_branding`](alembic/versions/20260511_e22_tenant_branding.py); модуль [`app/api/admin/branding.py`](app/api/admin/branding.py) — `GET /api/admin/branding`, `PATCH /api/admin/branding` (HEX-валидация, тримминг имени), `POST /api/admin/branding/logo` (PNG/JPEG ≤ 1 МБ, сохранение в `app/static/uploads/branding/tenant-<id>.<ext>`, cache-buster в URL). `GET /api/admin/auth/me → branding` читает данные из `Tenant` (контракт совместим с UI). Регресс: [`tests/test_admin_branding.py`](tests/test_admin_branding.py).
- [x] **E2.3 Billing (минимум):** `Tenant.plan_status`, таблица `billing_usage_daily`, ежедневный rollup (ARQ cron в [`app/worker.py`](app/worker.py)); блокировка login/`auth`/select-org и ранний выход WhatsApp webhook при `plan_status=suspended`; опциональное поле `billing_blocked` в `GET /auth/me`. Миграция [`20260512_e23_billing_minimal`](alembic/versions/20260512_e23_billing_minimal.py). Полноценный Stripe/лимиты по тарифу — вне scope.
- [x] **Superadmin password UX hardening:** одноразовые `generated_password`/`new_password` больше не уходят в toast; показываются в modal с copy button и обязательным подтверждением сохранения.
- [x] **Superadmin tech fields UI:** в таблице ресторанов редактируются `iiko_api_login`, `iiko_terminal_group_id`, `telegram_ops_chat_id` (password-поле для api login) — [`superadmin.html`](app/templates/superadmin.html), `PATCH …/credentials`.
- [x] **Superadmin audit log:** `SuperadminAuditLog` + миграция [`20260521_superadmin_audit`](alembic/versions/20260521_superadmin_audit.py); `GET /api/superadmin/audit`; запись на approve/reject/create/status/credentials/schedule/sync/password_reset — [`superadmin_audit.py`](app/services/superadmin_audit.py). UI «Журнал действий Super Admin». Тесты: [`tests/test_superadmin_audit.py`](tests/test_superadmin_audit.py).
- [x] **Control Plane Phase 2 (trace_id + API tail):** `trace_context.py`, webhook → ARQ → `emit_event`; iiko/WA/operator logs; `parent_event_id`/`caused_by`; `GET /trace-timeline` — [`docs/CONTROL_PLANE.md`](docs/CONTROL_PLANE.md), [`tests/test_control_plane_trace.py`](tests/test_control_plane_trace.py).
- [x] **Control Plane — timeline UI panel:** admin UI по `trace_id` (API `GET /trace-timeline` ✅); Phase 3 replay harness — см. CONTROL_PLANE.
- [x] **E5 ARQ-only:** убран fallback на `BackgroundTasks` в [`app/services/task_queue.py`](app/services/task_queue.py); в `APP_ENV=production|staging` старт web-процесса проверяет Redis+ARQ; worker обязателен в проде. Web enqueue и [`WorkerSettings`](app/worker.py) используют один `ARQ_QUEUE_NAME` (`restomind` по умолчанию).
- [x] **E5 диагностика очереди (light):** `GET /api/admin/system/task-queue-health` ([`app/api/admin/system.py`](app/api/admin/system.py)) + хелпер [`app/services/task_queue_health.py`](app/services/task_queue_health.py) — структурированный статус Redis/ARQ/worker (heartbeat по `<queue>:health-check`). Структурный лог `event=task_queue_enqueue` на каждый enqueue в [`app/services/task_queue.py`](app/services/task_queue.py).

## 🟠 P1.5: UX Density & AI Trust

- [x] **Executive Hub v1:** overlay поверх вкладок для manager/admin — narrative cards (`GET /api/admin/intelligence/executive-hub`), drill-down drawer, чат агента через `/intelligence/query`; кнопка Hub в шапке и CTA на дашборде.

> Источник: внешний UX-аудит (2026‑05). Сюда попало только то, что прошло наш фильтр «реально не сделано и осмысленно для оператора в час пик». Архитектура (Jinja + Alpine + Tailwind + WS) не меняется, на React/HTMX не переходим. Дизайн-система — `docs/UI_DESIGN_SYSTEM.md` секции «Density modes» и «AI in UI».

- [x] **P1.5.0: IA collapse + Unified «Требует внимания»**: сайдбар сжимаем до 4+4 пунктов (**Операции** / **Управление**); новый экран [`_tab_inbox.html`](app/templates/screens/_tab_inbox.html) объединяет [`operator_queue`](app/templates/screens/_tab_operator_queue.html) (таб **От клиентов**) и [`incidents`](app/templates/screens/_tab_incidents.html) (таб **Системные**); новый [`_tab_ai_center.html`](app/templates/screens/_tab_ai_center.html) объединяет [`ai_value`](app/templates/screens/_tab_ai_value.html) / [`intelligence`](app/templates/screens/_tab_intelligence.html) / [`digital_twin`](app/templates/screens/_tab_digital_twin.html); [`analytics`](app/templates/screens/_tab_analytics.html) уезжает внутрь [`dashboard`](app/templates/screens/_tab_dashboard.html) под‑табом **Главная / Аналитика**. Все старые hash-URL редиректят на новые.

- [x] **Compact Kanban (high-density)**: переключатель **Normal / Compact** на канбане заказов; в Compact — карточки одной строкой (название, сумма, телефон‑last4, статус‑точка), теги типа способа доставки/оплаты — иконками, без фоновых плашек. Хранить выбор в `localStorage` пер‑пользователя. Цель: ≥ 8 заказов в колонке без скролла на 1440px против текущих 2–3. Файлы: [`_tab_orders.html`](app/templates/screens/_tab_orders.html), [`admin-app.js`](app/static/js/admin-app.js) (флаг `kanbanDensity`), `src/css/admin-input.css` (`ds-kanban-card--compact`).
- [x] **Tenant color stripe**: тонкая полоса (`2–3px`) сверху хедера и/или сайдбара, цвет — `Organization.brand_color_hex`. Визуальный якорь для владельцев сети. Переменная `--tenant-accent` в [`admin-brand-tokens.js`](app/static/js/admin-brand-tokens.js) (`restoMindApplyTenantAccent`), `box-shadow: inset 0 2px 0` на шапке и сайдбаре; подключение токенов в [`admin.html`](app/templates/admin.html). При свитче филиала (`POST /api/admin/auth/select-org`) хром гасится (`orgSwitchChromeDimmed` + `rm-chrome-org-switch`) до завершения перезагрузки профиля и данных вкладок.
- [x] **Right Context Panel в чатах**: третья колонка справа от переписки в [`_tab_chats.html`](app/templates/screens/_tab_chats.html) — профиль гостя (имя, телефон, кол‑во заказов, средний чек/LTV), активный черновик/pending‑заказ, активная бронь, последняя эскалация. Данные уже доступны через существующие эндпоинты `/api/admin/orders`, `/api/admin/bookings` + `User.meta_json`; на фронте — секция в `_app_shell` без отдельного API. На `<lg` — выезжает как drawer.
- [x] **AI Confidence на заказе**: если `validate_order` нашёл позиции через fuzzy (`SequenceMatcher` &lt; 0.8) или адрес доставки не помечен `delivery_address_verified` — карточка/строка заказа подсвечиваются (`ds-order-surface--ai-confidence`) + бейдж `ds-badge-warning-soft`. Данные в `items_json.order_meta.confidence`; в списке заказов дублируется `low_confidence` / `order_confidence`. Пересборка черновика сохраняет `delivery_address_verified` при `true`.
- [x] **AI Snooze with timer**: меню «ИИ: пауза» в шапке чата (30 мин / 2 ч / до завтра / навсегда / снять таймер). Backend — `User.ai_snoozed_until` (UTC), миграция [`20260512_p15_ai_snooze.py`](alembic/versions/20260512_p15_ai_snooze.py), `POST /api/admin/chats/{phone}/ai-snooze`, пауза LLM в [`process_message`](app/api/webhooks.py) (и тест-бот в монолите). Индикатор «🟣 ИИ выключен до HH:MM»; по истечении — авто‑сброс поля при следующем обращении к БД.
- [x] **Bulk‑actions в стоп‑листе**: чекбоксы + sticky‑панель (`В стоп / Снять со стопа / Сменить раздел`); long‑press на карточке каталога → multi‑select; батч [`POST /api/admin/menu/bulk-stoplist`](app/api/admin/menu_bulk.py) (скоуп по сессии филиала, `failed[]` per‑item). UI: [`_tab_menu.html`](app/templates/screens/_tab_menu.html), [`admin-app.js`](app/static/js/admin-app.js) (секции `// bulk-stoplist`).
- [x] **Skeletons + relative time**: skeleton‑строки на тяжёлых вкладках (заказы, чаты, аналитика, дашборд‑лента, inbox) через [`_skeleton.html`](app/templates/components/_skeleton.html); `fmt.timeAgo` / `fmt.dateTime` в [`admin-app.js`](app/static/js/admin-app.js) — относительное время в лентах и списках (заказы, чаты, инциденты, операторская очередь, события дашборда), абсолютное в `title`. Стили `.ds-skeleton-line` в `src/css/admin-input.css`.

- [x] **Failed‑бейдж сообщений в карточке/модалке заказа** (Wishlist Темира #3): сейчас `delivery_status === 'failed'` подсвечивается только в `_tab_chats.html:206`. Нужно в [`_tab_orders.html`](app/templates/screens/_tab_orders.html) (карточка/модалка заказа) показывать индикатор «N сообщений не доставлено в WhatsApp» с переходом в диалог гостя. Источник — `chat_logs.delivery_status` за пользователя, в окне ±1 час от заказа; рядом с уже существующим `iiko_last_error`.
- [x] **Кастомная модалка удаления заказа с превью** (Wishlist Темира #10): отдельная `ds-modal-panel` для удаления — № заказа, сумма, клиент, тип/оплата, причина (опц.), кнопка с задержкой 1 c. Заменить вызов общего `uiConfirm` в `app/static/js/admin-app.js` (handlerы удаления заказа) на новую модалку. Цель — снизить шанс случайного удаления у оператора в час пик.
- [x] **Onboarding / coach‑marks внутри админки** (Wishlist Темира #15): первый вход (или `?first_run=1`) — пошаговая подсветка ключевых зон; прогресс в `localStorage` + **`StaffUser.meta_json.tour_completed_at`** через `POST /api/admin/auth/tour-complete` и поле в `/auth/me`. Дополнительные `?`‑тултипы у тяжёлых полей — отдельно.

- [x] **Franchise / Branch (Phase 1 OS):** … **Phase 1.2 analytics:** per-location rollup из `SystemEvent.payload._location_id` в [`owner_dashboard.py`](app/services/owner_dashboard.py) (`rollup_location_event_stats`) — non-shift `/stats`, `/analytics`, `/funnel` (без `/shift/*`).

- [x] **Refresh `docs/ui/baseline/`:** baseline PNGs are tracked because `docs/UI_DESIGN_SYSTEM.md` embeds them; regenerate via [`scripts/capture_admin_u0_baseline.py`](scripts/capture_admin_u0_baseline.py) after major UI changes. `docs/ui/mobile-review/` remains a separate mobile refresh.

## 🟢 P2: Развитие (Growth)

- [x] **E1 хвост (платежи):** HMAC-SHA256/MD5 верификация для Freedom Pay (`freedom_pay.py` — MD5 pg_sig + FreedomPayInitiator) и Kaspi Pay (`kaspi.py` — HMAC-SHA256, `sha256=` prefix); per-org `payment_config_json` (миграции `20260509_payment_tx_config` + `20260510_org_pay_cfg_json`); UI CRUD в настройках (`_tab_settings_restaurant.html`, `_tab_settings_connections.html`). Остаток: уточнить заголовки подписи по актуальным докам провайдеров + `E14` генерация ссылок на оплату.
- [x] **E14 авто‑ссылка на оплату (генерация payment URL / deep link):** `CloudPaymentsInitiator.create_payment()` генерирует ссылку через `/payments/link/create`; `intent_router` задаёт `RouteResult.cta_url` при `requires_big_order_prepay`; WhatsApp отправляет CTA-кнопку (`send_cta_url_button`) отдельно от текста заказа.
- [x] **E8 WhatsApp интерактив:** `send_interactive_buttons()` отправляет `interactive/button` (до 3 кнопок) для подтверждения/отмены заказа; `receive_message()` в `webhooks.py` раскрывает `button_reply` в `"да"` / `"нет"`. `RouteResult.interactive_buttons` управляет выбором транспорта (кнопки vs CTA vs текст).
- [x] **Telegram оператор‑бот:** `app/api/telegram_webhook.py` (`POST /api/telegram/webhook`, `X-Telegram-Bot-Api-Secret-Token`); `app/services/telegram_operator.py` — relay оператора (`reply:{phone}:{org_id}` callback, Redis TTL 30 мин, запись `ChatLog`); кнопка «📩 Ответить клиенту» в алерте эскалации + `/dialogs` команда. _Wishlist Темира #12._
- [x] **Экстренное закрытие ресторана:** причина + длительность паузы + корректное поведение вне рабочего времени.

- [x] **Ночные предзаказы + Telegram «на смене»** (Wishlist Темира #20): когда гость пишет вне рабочих часов (`time_context.py` уже умеет считать) — бот принимает заявку как **предзаказ** (не отправляя в iiko), кладёт в новую таблицу `night_preorders` (или `Order.kind='preorder'` + `scheduled_for`). Telegram оператор‑бот (см. выше) утром шлёт **сводку ночных предзаказов** в чат смены и ждёт кнопку «🟢 Я на смене» от оператора → после нажатия бот переключает все ночные предзаказы в обычный поток подтверждения. Супер‑админ получает алерт, если за N минут после открытия никто не нажал «на смене».
- [x] **Performance Pack (WhatsApp hot path):** kitchen-gate (`is_kitchen_open=false` → `night_preorder`); quick replies bypass LLM; FAQ Redis cache (default on); prompt metrics + history trim; **post-commit event consumers** (`event_consumers_async`); parallel `menu_context` + `sales_strategy` в `build_llm_prompt_bundle`.
- [x] **Авто‑сбор отзывов после заказа** (Wishlist Темира R3): через N минут после `OrderStatus.COMPLETED` (или `SENT_TO_IIKO` + offset) — WhatsApp шаблон «Как вам всё прошло?» с кнопками 👍 / 👎. 👍 → ссылка на отзыв в **2GIS** (`Organization.review_url_2gis`), 👎 → запись `customer_feedback` + Telegram‑алерт владельцу/админу с цитатой и `phone_last4`. Никаких новых LLM‑вызовов, всё на template‑messages + `intent_router` post‑hook.
- [x] **Горячая рассылка по клиентам + бонусная система** (Wishlist Темира #19): целевая рассылка через WhatsApp template_messages по сегментам — «давно не заказывали (>30 дней)», «частые гости», «по событию» (день рождения, праздник). Отдельный экран в админке (черновик → preview → send), per‑org rate‑limit, opt‑out по `User.marketing_opt_out`, лог в `marketing_blasts` + per‑message статус доставки. Бонусная система — отдельная таблица `loyalty_balance` + начисление через webhook iiko или вручную; в WhatsApp бот умеет отвечать «у вас N баллов» через `intent: faq` enrichment. **Перед стартом** — юридическая проверка: WABA маркетинг‑правила Meta + Закон РК «О персональных данных».

## ⚪ P3: Бэклог и R&D

- [x] **E11 Strategy Engine (расширение):** новые trigger_mode в `UpsellRule` без миграции: `time_of_day` (диапазон часов по org timezone) и `item_present` (категория уже есть в корзине). Новое правило `rule_session_rejection_cap` в `sales_strategy_engine.py` — если клиент 2+ раза проигнорировал предложения в текущем заказе, upsell останавливается. Персонализация из истории заказов: [`app/services/personalization.py`](app/services/personalization.py) — `get_user_preferences` анализирует 20 последних заказов клиента (`never_categories`, `avg_total`, `drinks_frequency`); загружается параллельно в `fetch_ai_read_context`; `build_sales_strategy` фильтрует кандидатов по предпочтениям (не предлагает категории/напитки, которые клиент никогда не берёт).
- [x] **E12 Smart Category Filter (вместо RAG):** RAG заменён более простым и точным подходом для больших меню. `detect_category_hint(message, menu_items)` — string-match запроса гостя против категорий без LLM. `build_menu_context_filtered()` — полный контекст для найденной категории + drinks/upsell-позиций, компактный (только name+price) для остального. Включается при `MENU_SMART_FILTER_ENABLED=true` и `len(menu_items) >= MENU_SMART_FILTER_MIN_ITEMS` (default 60). Кэш расширен до `(org_id, category_hint)`. Без embeddings, без pgvector.
- [x] **Event System Stabilization (Phase 2 OS) — ✅ 100%:** **12+ типов** на шине через `emit_event` (в т.ч. `ai.response.generated`, `ai.dialog.started`, `integration.iiko.failed`, `integration.whatsapp.failed`). `DailyOrgStats` + 13 колонок. **Backfill**: `POST /intelligence/backfill-stats` (+ `dialogs_count` из ChatLog). **websocket_consumer**: `emit_event` → `publish_event`; **audit_consumer** → `audit_log` + WS `os.audit`. Event-first: `/analytics`, `/funnel`, `network/stats`, `/stats`. Детали: [`docs/OS_TRANSITION_PLAN.md`](docs/OS_TRANSITION_PLAN.md), [`docs/AI_OPERATIONS.md`](docs/AI_OPERATIONS.md).
- [x] **AI Context Snapshot (Phase 3 OS) — ✅ 100%:** frozen `menu_context_text` / `menu_prices_snapshot`; replay с `chat_history_slice`; edge-case — synthetic menu context из `menu_prices_snapshot`, если `menu_context_text` пуст ([`context_engine.py`](app/services/context_engine.py)).
- [x] **Decision Engine (Phase 4 OS) — ✅ 100%:** [`app/services/decision_engine.py`](app/services/decision_engine.py) — 8 правил: `billing_suspended` (block, 3 источника defense-in-depth), `force_closed` (block), `empty_order` (block), `all_items_hallucinated` (block), `stoplist_quick` (warn), `delivery_no_address` (warn), `order_items_anomaly` (warn), `pricing_policy` (block при `max_discount_pct`). Все block → intent→faq. Интегрирован в [`webhooks.py`](app/api/webhooks.py) с billing_suspended флагом. Порог Фазы 5 **достигнут**. Тесты: [`tests/test_os_sprints.py`](tests/test_os_sprints.py) + [`tests/test_sprint_g.py`](tests/test_sprint_g.py).
## 🟢 P3 Growth & BI Analytics

- [x] **KPI‑центр официантов из iiko** (Wishlist Темира R4):
  - [x] БД: `waiter_registry`, `waiter_kpi_daily`, `iiko_sync_runs` — миграция [`20260523_p3_waiter_kpi`](alembic/versions/20260523_p3_waiter_kpi.py).
  - [x] ETL: Cloud deliveries + Office waiter report; cron `waiter_kpi_sync_scheduled_tick` в [`worker.py`](app/worker.py).
  - [x] Admin API: sync / рейтинг / CSV — [`waiter_kpi.py`](app/api/admin/waiter_kpi.py).
  - [x] UI: блок **«Официанты»** в подробной аналитике; spike [`IIKO_WAITER_KPI_SPIKE.md`](docs/IIKO_WAITER_KPI_SPIKE.md).
- [x] **iiko‑маркетинг (MVP):** `POST /api/admin/marketing/sync-iiko-customers` — телефоны гостей из iiko Cloud deliveries → `User` для сегментов рассылок ([`iiko_customer_sync.py`](app/services/iiko_customer_sync.py), вкладка «Маркетинг»).
- [x] **BI по iiko (OLAP):** lite ETL почасовых продаж (`sales_hourly_daily`, cron 23:15 UTC, heatmap API/UI). Полный warehouse + автоподстройка upsell — отдельный epic.
- [ ] **VIP white‑label** (Wishlist Темира R2): отдельный Astro/Next фронт per‑tenant; ROI gate до кода.


## P4: AI Operations / Intelligence

- [x] **Restaurant Intelligence MVP:** admin `AI-аналитик` tab + `POST /api/admin/intelligence/query` for revenue/orders questions.
- [x] **Unified analytics/event pipeline foundation:** durable `SystemEvent` stream and `emit_system_event()`.
- [x] **AI auto-insights MVP:** `OperationalInsight` with admin-visible revenue/order/cancellation insights.
- [x] **Restaurant state snapshots:** `RestaurantStateSnapshot` and `GET /api/admin/intelligence/digital-twin`.
- [x] **Digital Twin MVP:** separate admin tab and operator-capacity simulation engine.
- [x] **Phase 5 OS — Full OS Behavior (~98%) ✅:** Все критерии Phase 5 по [`docs/OS_TRANSITION_PLAN.md`](docs/OS_TRANSITION_PLAN.md) реализованы. **Статус: Launch Window.**
  - **Аналитика из event stream (~100% KPI):** `/stats` (all-time из `DailyOrgStats`), `/analytics`, `/funnel`, `/ai-value`, `network/stats`, `/activity` — event-first; SQL только для `items_json`/upsell и operational lists (`money_queue`, orders/chats). Backfill 90 дней + ops-события из `system_events`. Admin `bulk-cancel` и confirm → `order.*` events.
  - **Predictive insights (~95%):** `build_demand_forecast`, `build_cancellation_forecast`, `build_overload_risk`, `build_autopilot_pricing` (5 тактик с `price_adj_pct`). Все поля в `/os-dashboard`.
  - **Auto-recommendations (~95%):** 6 типов + `autopilot_pricing`. `POST /apply-pricing/{rec_id}` и **`POST /apply-pricing/bulk`** (все `new` за org).
  - **Self-healing (~95%):** 4 детектора + **Self-Healing 2.0** — WA-напоминание гостям с `prepayment_status=pending` при spike failed payments ([`healing_actions.py`](app/services/healing_actions.py)). `AuditLog` + `GET /audit-log`. `stock_alerts[]` на `/os-dashboard` (inventory snapshots или прокси из DailyOrgStats).

### Owner Intelligence (продуктовый слой для владельца)

- [x] **Этап 1 — Summary:** `build_owner_intelligence_summary`, `GET /api/admin/owner-intelligence/summary`, вкладка **Owner Intelligence** в AI Center.
- [x] **Этап 2 — QA Auto-Audit:** таблица `ai_order_audits`, `order_ai_audit.py`, API review/dismiss, событие `ai_order.audit_risk_detected`.
- [x] **Этап 3 — Upsell attribution:** `upsell_offer_events`, `upsell_attribution.py`, `GET /upsell-impact`; Revenue Copilot scoring + anti-repeat + A/B experiments + Smart Sales UI.
- [x] **Revenue Copilot (Wave 1):** `upsell_scoring_engine.py`, `get_copilot_candidate_lists`, `upsell_experiments` + migration, coordinator в `intent_router` / `context_engine`.
- [x] **OI hardening (Wave 2):** cron audit backfill, `kitchen_gate.order_blocked`, QA audit v2 + badge, Kitchen Gate expires presets.
- [x] **Analytics v2 (Wave 3):** Menu Profit `cost_price` UI/CSV; Network Benchmark full screen + v2 DTO.
- [x] **Channels (Wave 4):** Telegram customer channel foundation; POS `IikoAdapter` + `pos_provider`.
- [x] **Этап 4 — Kitchen Gate v2:** `operational_mode_states`, GET/PATCH `/kitchen-gate`, интеграция в `decision_engine` + AI context; toggles на **Смена** и Owner Intelligence (`_kitchen_gate_panel.html`).
- [x] **Этап 5 — Menu Profit Lab:** `menu_profit_lab.py`, `GET /menu-profit`, preview в summary.
- [x] **Этап 6 — Network Benchmark:** `network_benchmark.py`, `GET /network-benchmark` (disabled для одиночной точки).
- [x] **Hot-path hooks:** upsell offers в `apply_db_upsell_rules` + LLM upsell; audit после `confirm_order`; backfill `POST /order-audits/backfill`.
- [x] **Stop-list replacements:** категорийные альтернативы в `compose_stoplist_notice`, событие `kitchen_gate.replacement_suggested`.
- [x] **Sales-ready (P0–P6):** deploy smoke §8 в [`DEPLOY_RUNBOOK.md`](docs/DEPLOY_RUNBOOK.md); Revenue Copilot v3 (`upsell_pair_mining`); Telegram production; r_keeper adapter; weekly digest; QA polish; Menu/Network sales UI.
- [x] **Digest habit (§5):** `owner_digest_delivery.py`, preview + manual send в OI, `SystemEvent` audit log, Monday cron по TZ org.
- [x] **Menu Profit sales (§6):** price recommendations (`recommended_price`, `expected_margin_lift`), missing cost checklist, promote today для Copilot.
- [x] **Network Benchmark sales (§7):** per-location metrics table, weekly narratives + practice transfer.
- [x] **QA workflow (§8):** chat badge, filters (high/unreviewed/stoplist/address), outcome calibration loop.

## 🔵 P5: OS Decision Feed (Visibility — выполнен)

> Цель: владелец «чувствует» работу ОС. **Статус: Launch Window** — код P0–P6 (Owner Intelligence sales-ready) закрыт; выкатка и натурные тесты — [`docs/DEPLOY_RUNBOOK.md`](docs/DEPLOY_RUNBOOK.md) §8, sign-off [`docs/FINAL_MILE_OPS_SIGNOFF.md`](docs/FINAL_MILE_OPS_SIGNOFF.md) §A–§B.

### Focus-Driven OS (Admin Shell) — целевая UI-модель G10.4+

> **Статус:** Sprint 1–5 ✅ (Mode Engine internal; **Role-first IA** в UI; Shift split + staged nav; Action Queue inbox; Command Bar Ctrl+K). Детали: [`docs/UI_DESIGN_SYSTEM.md`](UI_DESIGN_SYSTEM.md) § Role-First IA.

**Принятые решения (2026-05-21):**

| Вопрос | Решение | Где в коде / доках |
|--------|---------|-------------------|
| Mobile Shift Mode | **Staged Focus Navigation** (`focus` ↔ `context`, кнопка «Назад к задаче» на `<lg`) | UI_DESIGN_SYSTEM LAW 2; реализация — Sprint 2 |
| Пустой focus при риске | **Гибрид:** TTL skip 600s + CTA `reset_skips` при `shift_empty_focus_while_risk_positive` | ✅ [`_tab_shift_control.html`](app/templates/screens/_tab_shift_control.html), FM-3 в ROADMAP |
| Voice call log | **Strict `location_id`** при выбранной точке; org-wide — только summary в Intelligence | ✅ `GET /voice/calls?location_id=`; ✅ `location_id` в payload при `record_voice_call` |

**Спринты (Strangler — без остановки прода):**

- [x] **Sprint 1 — Mode Engine + Universal Semantics** (Strangler; backend без изменений, `GET /shift/state` ✅):
  - [x] Mode Engine — `currentMode`, `setMode()`, `_bootstrapAdminMode`, matrix mode↔tab в `admin-app.js` *(internal после Sprint 5)*
  - [x] Mode Bar — `components/_mode_bar.html` *(компонент остаётся; из header убран в Sprint 5)*
  - [x] Universal Semantics — `ds-status-ok|warn|danger|ai|inactive` + `--color-mode-*` в `src/css/admin-input.css` → `npm run build:admin-css`
  - [x] Условный сайдбар — фильтр `navItems` *(Sprint 1: `isTabInCurrentMode`; Sprint 5: `isTabVisibleForRole`)*
- [x] **Sprint 2 — Shift split + mobile staged nav:** `_shift_focus_chat.html`, `_shift_focus_order.html`; правая панель Context Dock по `shiftState.focus.kind`; mobile `mobileActiveScreen` focus/context.
- [x] **Sprint 3 — Action Queue inbox + voice tail:** эволюция `_tab_inbox.html` (карточки `money_queue.py`, не дублировать G7 backend); Final Mile call strip с `locationQueryParams()`; закрыть backlog `location_id` в `record_voice_call`.
- [x] **Sprint 4 — Command Bar (Ctrl+K):** префиксы `/leak`, `/red`, `/force-close` поверх существующего глобального поиска.
- [x] **Sprint 5 — Role-first IA pivot:** убран Mode Bar; сайдбар по роли (`operator` / `manager` / `admin`); smart landing оператора (shift при риске/focus, иначе inbox); дашборд «Обычный / Расширенный» (`analyticsDensity`); mobile bottom nav по роли; shift badge polling вне вкладки «Смена»; demo-login → `applyRoleDefaultLanding`.

**Не дублировать (уже сделано):** G7 money queue, G9/G10 shift engine, FM-3 `reset_skips`, Voice `GET /voice/calls` (пагинация + RBAC) — см. чекбоксы ниже в P5.

### G10.5 — Shell v2 Role-First (Execution Kernel)

> **Статус:** ✅ Focus Card spec + макрос + operator scene consolidation. Детали: [`docs/FOCUS_CARD_SPEC.md`](docs/FOCUS_CARD_SPEC.md).

- [x] **Focus Card Spec** — `docs/FOCUS_CARD_SPEC.md`; ссылка в UI_DESIGN_SYSTEM § Execution Kernel UI.
- [x] **Focus Card macro + mapper** — `_focus_card.html`, `focusCardFromShiftState()` / `adminFocusCardFromShiftState()` в `admin-app.js`.
- [x] **Shift tab refactor** — `_tab_shift_control.html` использует макрос (поведение 1:1, staged nav сохранён).
- [x] **Operator scene** — sidebar primary «Смена», inbox «Все риски»; `openMoneyQueueItemViaShift` для operator.
- [x] **Tests** — `test_focus_driven_os_sprint2.py`, `test_ui_operator_language.py`; smoke checklist в CHANGELOG.

**Не в scope G10.5:** возврат Mode Bar, `POST /shell/mode`, big-bang rewrite `admin.html` (Phase D — отдельно).

### G10.6 — Wow Layer (Live Impact & Operational Scene)

> **Статус:** ✅ Live feedback loop action→деньги, one-screen operator, compressed actions, state animations.

- [x] **Live Impact Strip** — `live_impact` в `GET/POST /shift/state` (Redis TTL 90s); UI strip в `_tab_shift_control.html`.
- [x] **AI reasoning hint (heuristics)** — `why_this_card`, `ai_hint`, `confidence` в `focus`; строка «Почему эта задача» в `_focus_card.html`.
- [x] **Action compression** — `compressed_actions` (primary/secondary/tertiary) + UI 1+1+link.
- [x] **State animation layer** — CSS `ds-animate-pulse-green`, `ds-focus-slide-in`, escalation shake; JS pulse hooks.
- [x] **Operator one-screen** — primary nav shift+inbox; orders/chats/bookings в «Разделы» + mobile «Ещё»; lazy mount bookings.
- [x] **Owner today impact** — `recovered_today_kzt` в `GET /revenue-leak` + hero strip на дашборде + WS pulse.

### G10.7 — Predictive Shift Layer (operational perception)

> **Статус:** ✅ anticipation → inevitability → compressed outcome; pre-attention до клика.

- [x] **Focus anticipation** — `anticipation` на focus (`tension_level`, `anticipation_text`, `inevitability_text`, `predictive_prefix`).
- [x] **Predictive scene** — `predictive_scene` в shift/state + tension banner до действия.
- [x] **Pre-attention UI** — idle pulse на focus card, «Риск растёт» tick, metric risk rising.
- [x] **Compressed live impact** — `outcome_prefix` → `outcome_emotion` → `impact_money` (staged reveal в golden flow).
- [x] **Tests** — `test_predictive_shift_layer.py`.

### G10.8 — Demo Scene (30s money rescue autoplay)

> **Статус:** ✅ scripted сценка «потеря → спасение → деньги» + **G10.8.1 counterfactual pitch**.

- [x] **Backend** — `demo_shift_scene.py`: фазы `hook|tension|action|impact|next|resolve`, fixed narrative; `GET /demo/shift-scenes`, `GET /demo/shift-scene/{id}/state?phase=`.
- [x] **G10.8.1 Counterfactual layer** — `loss_would_be_kzt`, `counterfactual_line`, `urgency_sec`, auto-action copy; live impact flash `−1200 → +1200 спасено`; closing frame 25–30s.
- [x] **Autoplay UI** — единая кнопка «Посмотреть демо» → pitch; immersive shift (`pitch_immersive`, без кнопок/баннеров S2); live wait timer + urgency countdown; success tick на impact.
- [x] **Tests** — `test_demo_shift_scene.py`, `test_demo_shift_scene_ui.py`, `test_demo_pitch_seed.py`, `test_demo_shift_presentation.py`.
- [x] **Docs** — [`docs/DEMO_PITCH.md`](DEMO_PITCH.md) (канон pitch/explore, smoke, gaps).

### G10.8.2 — Demo zero-friction

> Landing autoplay, публичная self-demo ссылка, booking pitch variant.

- [x] **`GET /demo` и `/demo/{slug}`** — session + redirect `/admin?demo=1&demo_scene=…#shift`; `DEMO_PUBLIC_ENABLED` (или `APP_DEBUG`)
- [x] **Rate limit** — `DEMO_RATE_LIMIT_PER_HOUR` per IP
- [x] **`?demo=1` autoplay** — без экрана логина после redirect
- [x] **Сценка `booking_rescue_30s`** — booking_at_risk pitch (~8500 ₸)
- [x] **Публичные URL:** `/demo`, `/demo/money`, `/demo/booking` — см. [`docs/DEMO_PITCH.md`](DEMO_PITCH.md)

- [x] **OS Decision Feed UI:** «Лента решений ОС» в `aiCenterTab=os` ([`_tab_ai_center.html`](app/templates/screens/_tab_ai_center.html)), `loadAuditLog()`, WS `os.audit`, блок «Живая ОС» (`dashLiveFeed`) на дашборде, refresh по `order.*` / `payment.*` / `booking.*` в `handleWsEvent`. UI-тексты — язык оператора («данные ОС», не dev-жаргон).
- [x] **Websocket audit push:** [`audit_consumer.py`](app/services/audit_consumer.py) публикует `os.audit` с `org_id` после записи в `audit_log`.
- [x] **Daily OS Digest (backend):** [`daily_os_digest.py`](app/services/daily_os_digest.py) — `GET /daily-os-digest/preview`, cron `daily_os_digest_scheduled_tick` (окно 09:00 по `Organization.timezone`). Staging-проверка Telegram — [`docs/TELEGRAM_DIGEST_STAGING.md`](docs/TELEGRAM_DIGEST_STAGING.md).
- [x] **Daily OS Digest (UI preview):** `aiCenterTab=final_mile` показывает preview panel через `GET /daily-os-digest/preview`.
- [x] **SupplyMind (backend MVP):** `inventory_stock_snapshots`, `POST/GET /supplymind/drafts`, bulk snapshots — [`supplymind.py`](app/services/supplymind.py), [`docs/FINAL_MILE_IMPLEMENTED.md`](docs/FINAL_MILE_IMPLEMENTED.md). Тесты: [`tests/test_ultimate_platform_sprint.py`](tests/test_ultimate_platform_sprint.py).
- [x] **SupplyMind (admin UI):** `aiCenterTab=final_mile` показывает stock alerts, список drafts и создание draft из API.
- [x] **SupplyMind — iiko Office inventory sync:** [`iiko_office_client.py`](app/integrations/iiko_office_client.py), [`iiko_inventory_sync.py`](app/services/iiko_inventory_sync.py), `POST/GET /api/admin/inventory/sync-iiko|sync-status`, `GET/PATCH /api/admin/organization/iiko-office`, ARQ cron 6 ч, UI Настройки → Подключения + Final Mile sync, RBAC manager/admin (save Office — admin-only UI). **Ops gate:** live smoke — [`docs/FINAL_MILE_OPS_SIGNOFF.md`](FINAL_MILE_OPS_SIGNOFF.md) §A.
- [x] **SupplyMind — чеклист закупки (lifecycle + CSV):** внутренний чеклист без экспорта в iiko; `GET/PATCH /supplymind/drafts/{id}`, CSV export; UI «Чеклисты закупки» в `aiCenterTab=final_mile` — [`supplymind.py`](app/services/supplymind.py), тесты в [`tests/test_ultimate_platform_sprint.py`](tests/test_ultimate_platform_sprint.py). См. [`docs/SUPPLYMIND_STAFFMIND.md`](docs/SUPPLYMIND_STAFFMIND.md).
- [x] **StaffMind (backend MVP):** `staff_onboarding_sessions`, `POST/GET /staffmind/onboarding`, Q&A из `KnowledgeItem` — [`staffmind.py`](app/services/staffmind.py); RBAC: manager/admin на POST-мутациях, `GET` — staff org ([`intelligence.py`](app/api/admin/intelligence.py)).
- [x] **StaffMind (admin UI):** `_tab_settings_team.html` подключает onboarding-сессии и Q&A через существующие JS-хелперы.
- [x] **StaffMind Step 1 (meta):** `StaffUser.meta_json` — `role_metadata` + `assigned_location_ids`; `GET/POST/PATCH /api/admin/staff`; UI редактирования в **Настройки → Команда** ([`docs/SUPPLYMIND_STAFFMIND.md`](docs/SUPPLYMIND_STAFFMIND.md)).
- [x] **Voice AI (backend MVP):** Twilio incoming/stream, `GET/POST /voice/status|config`, `voice_call_logs` — [`voice_ai.py`](app/services/voice_ai.py), [`docs/VOICE_AI_SPIKE.md`](docs/VOICE_AI_SPIKE.md).
- [x] **Voice AI (admin UI toggle):** `aiCenterTab=final_mile` подключает enable/mode toggle через `GET /voice/status` и `POST /voice/config`.
- [x] **Voice AI (call log strip UI):** блок «Журнал звонков Voice AI» в `aiCenterTab=final_mile` — [`_tab_ai_center.html`](app/templates/screens/_tab_ai_center.html), `refreshVoiceCallStrip()` → `GET /voice/calls` с `locationQueryParams()` + offset pagination.
- [x] **Voice AI — GET /voice/calls API:** `GET /api/admin/intelligence/voice/calls?limit=&offset=` — список `voice_call_logs`, `total`, опционально `location_id` (фильтр по `payload_json` + RBAC). Тест: [`tests/test_voice_staging.py`](tests/test_voice_staging.py).
- [x] **Voice AI — call log location_id in payload:** писать `location_id` в `voice_call_logs.payload_json` при `record_voice_call`, чтобы фильтр `?location_id=` работал end-to-end.
- [x] **StaffMind tracker UI:** прогресс шагов, счётчики Q&A/тем, progress bar в **Настройки → Команда** — [`_tab_settings_team.html`](app/templates/screens/_tab_settings_team.html), `staffMindTrackerMeta()` в JS (часть метрик — эвристики до расширения API).
- [x] **StaffMind tracker backend metrics:** `progress.test_passed`, `questions_asked`, `step_target` в ответах onboarding API.
- [x] **SupplyMind checklist UX:** раскрываемые черновики, session item checks (не persist), lifecycle-кнопки в `aiCenterTab=final_mile` — [`_tab_ai_center.html`](app/templates/screens/_tab_ai_center.html).
- [x] **SupplyMind checklist item PATCH:** persist checked state per draft item (`PATCH …/drafts/{id}` body `items[]`).
- [x] **GuestCare External (MVP):** таблица `external_reviews`, `GET/POST /reviews/external*`, вкладка «Отзывы» в AI-центре.
- [x] **GuestCare External — auto-sync (2GIS primary):** [`guestcare_parser.py`](app/services/guestcare_parser.py), [`external_reviews_sync.py`](app/services/external_reviews_sync.py) — **100% продукта = 2GIS** (`review_url_2gis`); Google — опционально best-effort / ручной import (Places API не в scope). `POST /reviews/external/sync`, ARQ cron 2×/сутки, кнопка «Синхронизировать» в `aiCenterTab=guestcare`. Тесты: [`tests/test_guestcare_parser.py`](tests/test_guestcare_parser.py).
- [x] **PWA foundation:** [`app/static/manifest.webmanifest`](app/static/manifest.webmanifest), [`app/static/sw.js`](app/static/sw.js), регистрация в `admin-app.js`.
- [ ] **Voice AI — ops/staging gate (не код):** Realtime connector ✅ — [`voice_realtime/`](app/services/voice_realtime/), tools, `require_staff_admin` на config. **Чекбокс =** sign-off [`docs/FINAL_MILE_OPS_SIGNOFF.md`](FINAL_MILE_OPS_SIGNOFF.md) §B + [`docs/VOICE_STAGING_CHECKLIST.md`](docs/VOICE_STAGING_CHECKLIST.md).
- [ ] **Admin i18n ru/kk:** словари UI для админки (отдельно от LLM-языка гостя); см. обсуждение в CONVENTIONS — пока только русский inline.
- [x] **Inbound latency baselines + SLA monitor:** `PipelineLatencyLog` (модель + миграция `20260513_pipeline_latency`); `app/services/pipeline_latency.py` — `schedule_log_pipeline_latency` (fire-and-forget), `get_latency_summary` (p50/p95/max per stage), `check_sla_thresholds` (emit `SystemEvent("sla_violation")`); `GET /api/admin/intelligence/latency`. _Wishlist Темира #18 (часть)._
- [x] **Operator efficiency analytics:** `app/services/operator_efficiency.py` — `escalation_count/rate_pct`, `avg_first_response_min`, `human_mode_sessions`, `operator_recovery_rate_pct`; `GET /api/admin/intelligence/operator-efficiency`. _Wishlist Темира #18 (часть)._
- [x] **AI incident detection:** `detect_ai_incidents(db, org_id)` в `app/services/intelligence.py` — token spike (>3× 7d avg), error spike (>15%), latency spike (>1.5× SLA); вызов в `list_insights()` (lazy). `AiUsageLog.error_count` + `p95_latency_ms` (миграция `20260513_ai_usage_errors`). _Wishlist Темира #18 (часть)._
- [x] **AI business recommendations:** `BusinessRecommendation` (модель + миграция `20260513_biz_recommendations`); `app/services/recommendations.py` — `generate_recommendations` (product_boost / pricing_adj / geo_expansion / stoplist_impact, детерминированно без LLM); фоновый цикл UTC 04:00; `GET/POST /api/admin/intelligence/recommendations`, `PATCH …/{id}`.
- [x] **Multi-tenant security audit:** `tests/test_multitenant_isolation.py` — 9 тестов изоляции по `organization_id` (Order, ChatLog, MenuItem, EscalationEvent, OperationalInsight, AiUsageLog, PipelineLatencyLog, BusinessRecommendation, cross-org phone); отчёт `docs/SECURITY_AUDIT.md`.
- [x] **Message accounting telemetry:** `MessageAccountingLog` (org + day + direction + source + type, upsert) + [`app/services/message_accounting.py`](app/services/message_accounting.py) (fire-and-forget). Хуки в 4 точках: inbound (text/voice/interactive), outbound AI, outbound operator, outbound blast. `GET /api/superadmin/message-accounting?days=1|7|30` + секция «Сообщения WhatsApp» в суперадминке. Данные только для суперадмина. Миграция `20260515_message_accounting`.
- [x] **Prompt caching (OpenAI):** порядок секций system prompt оптимизирован под автоматический кэш OpenAI (≥1024 токенов, `base → KB → menu` — стабильный префикс); логирование `cached_tokens` из `prompt_tokens_details` в `openai_p.py`.
- [x] **Bot pipeline performance:** `customer_reply.py` — finalize_outbound fire-and-forget (−20–50 мс); `webhooks.py` — `known_user_id` в `_save_chat_log` (убран дублирующий SELECT); `admin-app.js` — параллельный init (Promise.all, ~3–4x); LRU-кэш сообщений чатов (15, TTL 5 мин) + prefetch топ-3.
- [x] **Sprint A — Bot perf:** [`schedule_save_ai_context_snapshot`](app/services/context_engine.py) — снимок AI-контекста fire-and-forget **до** LLM (возвращает uuid синхронно, запись в БД асинхронно); `_start_slow_processing_feedback` + [`send_typing_indicator`](app/integrations/whatsapp.py) (Meta Cloud API `mark_as_read` + `typing_indicator`) после `BOT_SLOW_ACK_DELAY_SEC` (default 2с), fallback — текстовый ack `BOT_SLOW_ACK_MESSAGE` если wamid отсутствует; флаги `BOT_SLOW_ACK_ENABLED` ([`webhooks.py:347-385`](app/api/webhooks.py)). Heuristic intent‑pre‑routing: [`resolve_model_tier`](app/services/ai_brain.py) (`AI_MODEL_ROUTING_ENABLED`) — короткий FAQ‑текст → `fast`, draft/стратегия/keywords («заказ», «доставка», «бронь», >120 симв) → `strong`; `_needs_strong_model_rerun` гонит fast→strong rerun, если LLM вернул `intent ∈ {order, book, complex}` / `items[]` / `booking_details` / `order_actions`. Тесты: [`tests/test_perf_sprint_ab.py`](tests/test_perf_sprint_ab.py).
- [x] **Sprint B — Admin lazy load:** в [`admin-app.js`](app/static/js/admin-app.js) `loadChatList` вызывается только когда вкладка реально chats/inbox (`adminTabNeedsChatList`); `loadRevenueLeak` — только при открытии dashboard; `loadShiftState` — только shift (+ polling badge риска в сайдбаре вне вкладки через `shouldPollShiftStateBadge`); после login грузим данные только активной вкладки, остальное — через `deferIdleWork` (`requestIdleCallback` fallback `setTimeout`). ETag/`If-None-Match` → 304 + `Cache-Control: private, max-age=30` через [`cache_utils.json_with_etag`](app/api/admin/cache_utils.py) на `GET /api/admin/organization/profile` ([`organization.py:306`](app/api/admin/organization.py)) и `GET /api/admin/integrations/status` ([`menu.py:170`](app/api/admin/menu.py)). Тесты: [`tests/test_perf_sprint_ab.py`](tests/test_perf_sprint_ab.py) (`test_organization_profile_etag_304`, `test_integrations_status_etag_304`).
- [x] **Rule 8 — inbox/AI Center tails:** «Action Queue» → «Очередь помощи»; «Final Mile» → «Финал»; вкладка **Финал** и **Настройки → Команда** — русские подписи (сводка ОС, голосовой ИИ, закупки, обучение); analytics без dev-жаргона; карточка «Пик продаж» → под-таб аналитики; [`_tab_operator_queue.html`](app/templates/screens/_tab_operator_queue.html) — фильтр «В работе / Закрытые»; [`_tab_shift_control.html`](app/templates/screens/_tab_shift_control.html) — `shiftStateLabel` / `shiftStateReasonLabel`. Тест: [`tests/test_ui_operator_language.py`](tests/test_ui_operator_language.py).
- [x] **Launch runbook (staging/prod):** [`docs/DEPLOY_RUNBOOK.md`](docs/DEPLOY_RUNBOOK.md) — env-матрица Render (web + ARQ worker), пошаговый деплой Supabase/Upstash, post-deploy smoke (`/health`, `/health/deep`, `task-queue-health`, backfill-stats), troubleshooting; дополняет [`DEPLOY_RENDER.md`](DEPLOY_RENDER.md) и [`docs/FINAL_MILE_OPS_SIGNOFF.md`](docs/FINAL_MILE_OPS_SIGNOFF.md).
- [x] **Stop-list visibility для ИИ:** `context_engine` загружает все позиции включая стоп (`include_unavailable=True`); `build_menu_context` помечает `[СТОП]`; `ValidatedOrder.stoplist_items` в `intent_router` — ответ «временно недоступно» вместо «нет в меню», без эскалации.

---

## 📥 Wishlist Темира (2026-05) — индекс

Список пожеланий из обратной связи Темира (общий список + дополнительный для RestoMind), сверенный с фактическим состоянием кода. Этот блок — **только индекс**: статусы и реальные задачи живут в P0–P4 выше, здесь просто карта «что есть / чего нет / куда класть».

Легенда: ✅ done · ⚠️ partial · ❌ missing.

| # | Пункт | Статус | Где в roadmap / коде |
|---|---|---|---|
| 1 | Меньше шума в админке | ✅ | IA collapse 4+4 ✅ (P1.5.0). Compact Kanban / tenant stripe / skeletons / right context / bulk‑stoplist / AI confidence / AI snooze ✅ (**P1.5**) |
| 2 | Понятные настройки + новый функционал | ✅ | 8 экранов настроек (`_tab_settings_*`), Phase U4 |
| 3 | Видимый failed‑статус сообщений | ✅ | В чатах ✅ (`_tab_chats.html`); в заказах ✅ карточка/модалка + `failed_whatsapp_near_order` в **P1.5** |
| 4 | Кнопка «Выйти» + аутентификация | ✅ | `_header.html:220`, cookie‑session + `ws_token` |
| 5 | Польза от бота для владельца | ✅ | «Вклад ИИ», AI Center, weekly digest (`owner_weekly_digest.py`) |
| 6 | Раздел «Упаковка» | ✅ | Phase U4.5: `scope` item/category/order, миграция `20260507_ui_u45_packaging` |
| 7 | Мобильная адаптация заказов | ⚠️ | snap‑scroll + 44px ✅ (Phase U6); Compact density — в **P1.5** «Compact Kanban» |
| 10 | Модалка удаления заказа | ✅ | Кастомная `ds-modal-panel` с превью и задержкой (**P1.5**) |
| 12 | Telegram‑бот оператора / push | ✅ | Relay оператора ✅ (`telegram_webhook.py` + `telegram_operator.py`, Redis state, ChatLog); кнопка «📩 Ответить» в алерте эскалации (**P2** выполнено) |
| 13 | Унификация под разные заведения | ✅ | `Organization`, `tenant_owner_id`, `select-org`, branding |
| 14 | Рефакторинг админки | ⚠️ | split на `screens/` ✅; Lazy DOM ✅; **E0.1** раскол `_monolith.py` — в **P1** |
| 15 | База знаний разделена + онбординг/туториал | ✅ | Профиль/знания разделены ✅; coach‑marks в админке ✅ (**P1.5**) |
| 16 | AI‑анализ продаж по времени из iiko | ⚠️ | Restaurant Intelligence MVP ✅; «BI по iiko: продажи по времени суток» — в **P3** |
| 17 | Эффективные токены, кэш, счётчик | ⚠️ | Счётчик токенов ✅ (P0); semantic‑кэш и оптимизация промптов — отдельной задачей не созданы (см. **P3** «E12 RAG по меню» как смежное) |
| 18 | Анализ услуг общения | ⚠️ | AI Value метрики ✅; SLA monitor / operator efficiency / AI incident detection — в **P4** |
| 19 | Горячая рассылка по клиентам + бонусы | ✅ | `MarketingBlast` + `LoyaltyBalance` + API + вкладка «Маркетинг» в админке (**P2** выполнено) |
| 20 | Вне рабочее время + ночной предзаказ + Telegram «на смене» | ✅ | `Order.kind='night_preorder'`, ARQ cron, «🟢 Я на смене» Telegram-кнопка (**P2** выполнено) |
| 21 | Экстренное закрытие ресторана | ✅ | `force_closed_until/reason` end‑to‑end (P0/P2) |
| R1 | Авто‑рассылка из iiko по клиентам | ⚠️ | **P3 Growth** «iiko‑маркетинг (MVP)» ✅; полный CRM + PII-legal — backlog |
| R2 | VIP сайт/приложение | ❌ | **P3 Growth** «VIP white‑label» |
| R3 | Авто‑сбор отзывов после заказа | ✅ | `CustomerFeedback` + `send_review_request` ARQ + 👍/👎 кнопки в WhatsApp (**P2** выполнено) |
| R4 | KPI‑центр официантов из iiko | ✅ | **P3 Growth** «KPI‑центр официантов из iiko» |
