# Changelog

Заметные изменения проекта **RestoMind**. Формат близок к [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/).

---

## [Unreleased] — 2026-03-20

### Добавлено (2026-05-25) — Performance Pack (WhatsApp hot path)

- **Kitchen-gate:** при закрытой кухне (но открытом зале) новые заказы без `is_preorder` → `kind='night_preorder'` и утренний flow активации.
- **Quick replies:** детерминированные ответы без LLM (`greeting`, `thanks`, `operator`, `cancel`, `working_hours`) — `app/services/quick_replies.py`, toggle `QUICK_REPLIES_ENABLED`.
- **FAQ cache:** Redis-кеш ответов `intent=faq` по `(org_id, hash вопроса, kb_fingerprint)` — `FAQ_CACHE_ENABLED` (default on).
- **Prompt metrics:** замер размера промпта + обрезка `chat_history` при превышении `PROMPT_MAX_TOKENS_SOFT`.

### Добавлено (2026-05-25) — Performance Pack phase 2

- **Event pipeline async:** analytics / audit / healing после commit родительской транзакции (`event_consumer_runner.py`, `EVENT_CONSUMERS_ASYNC=true` по умолчанию; тесты — sync через `EVENT_CONSUMERS_ASYNC=false` в conftest).
- **Parallel LLM context:** `build_menu_context_for_ai` и `build_sales_strategy` параллельно в `build_llm_prompt_bundle`.
- **Admin perf:** lazy-mount `dashboard` / `menu` / `ai_center` / `marketing`; lazy chunk `admin-marketing.js`; long-cache на versioned `/static/*`.

### Добавлено (2026-05-24) — G10.8.2 zero-friction demo + OS gap closure

- **Public demo:** `GET /demo`, `/demo/money`, `/demo/booking` → demo session + redirect autoplay; `DEMO_PUBLIC_ENABLED`, `DEMO_RATE_LIMIT_PER_HOUR`.
- **Booking pitch:** сценка `booking_rescue_30s` (booking_at_risk, ~8500 ₸).
- **Admin autoplay:** `?demo=1&demo_scene=` на `/admin` без кнопки login.
- **WS hardening:** `publish_org_event` пишет в org-scoped Redis channel + legacy global.
- **Admin audit:** middleware для PATCH/POST admin (вне `emit_event`) + `os.audit` push.
- **Control Plane:** `apply_conversation_state_in_txn`, `GET /replay/scenarios`, `replay_harness.py`.

### Изменено (2026-05-22) — документация demo pitch

- **Канон sales demo:** [`docs/DEMO_PITCH.md`](docs/DEMO_PITCH.md) — pitch/explore, API, smoke, честные gaps vs G10.8.2.
- **Синхронизация:** `README.md`, `codebase.md`, `docs/UI_MAP.md`, `CODEX.md`, baseline/lighthouse README и capture-скрипты — кнопка **«Посмотреть демо»** (не «Попробовать демо»); уточнён seed demo-org при startup.

### Добавлено (2026-05-22) — G10.8.1 Counterfactual Pitch

- **Counterfactual layer:** фазы `hook→tension→action→impact→next→resolve`; dual-state «без системы → потеря / с системой → спасено» в `demo_scene.counterfactual` и live impact.
- **Pitch immersive:** скрыты кнопки focus card, S2-баннеры, demo-баннер; auto-action «✔ Ответ отправлен автоматически»; urgency countdown; live wait timer; micro-flash `−1200 ₸`; success tick.
- **Closing frame (25–30s):** «Система автоматически спасает…» + stat + CTA «Осмотреть демо».
- **Demo explore UX:** без heartbeat/spam 401 при replay; cap риска ~12k на `GET /shift/state`; без «Готовность N%» в шапке.

### Добавлено (2026-05-22) — G10.8 demo pitch seed (осмотр после Esc)

- **Demo seed:** `_seed_demo_pitch_risks` — свежие slow chats, брошенные черновики (~1200 ₸), брони под риском, `daily_org_stats.recovered_kzt` для hero после pitch.
- **Единый demo-login:** одна кнопка «Посмотреть демо» → pitch → read-only осмотр; тесты [`tests/test_demo_pitch_seed.py`](tests/test_demo_pitch_seed.py).

### Добавлено (2026-05-22) — Predictive Shift Layer (G10.7)

- **Anticipation на focus:** `anticipation` (tension_level, anticipation_text, inevitability, predictive_prefix) — probability до клика.
- **Predictive scene:** banner «Система предупреждает» + `predictive_scene` в shift/state.
- **Pre-attention:** idle pulse на focus card, «Риск растёт» каждые 12s, pulsing metric «Под риском».
- **Compressed outcome:** live_impact = prefix → emotion («Вернули клиента») → money (`+N ₸`) с staged reveal в golden flow.

### Добавлено (2026-05-22) — Wow Layer UX choreography (G10.6.1)

- **Golden flow `complete`/`skip`:** staged timeline 150→200→200→300→500 ms (exit → impact strip → pulse → focus enter).
- **Narrative renderer:** одна строка `reason → money` вместо двух равноправных полей в strip.
- **Attention layer:** `ds-shift-scene--impact/focus/exit` — dim metrics, spotlight focus deck во время момента.

### Добавлено (2026-05-22) — Wow Layer (G10.6)

- **Live Impact Strip:** `live_impact` в `GET/POST /shift/state` (Redis TTL 90s) — мгновенная обратная связь после complete/skip; UI strip на экране смены.
- **Focus Card wow:** `why_this_card`, `ai_hint`, `confidence` (heuristics без LLM); `compressed_actions` (primary/secondary/tertiary).
- **State animations:** pulse green / fade shrink / focus slide-in / escalation shake (`admin-input.css` + `admin-app.js`).
- **Operator one-screen:** primary nav = shift + inbox; orders/chats/bookings в «Разделы» и mobile «Ещё»; lazy mount bookings.
- **Owner today impact:** `recovered_today_kzt` в `GET /revenue-leak` + hero «Спасено сегодня» на дашборде с WS pulse.

### Добавлено (2026-05-22) — Money Layer v2

- **Recovered $:** колонки `recovered_kzt`, `focus_completed_count` в `daily_org_stats`; агрегация по `shift.focus_completed` и `order.draft_recovered` ([`money_recovery.py`](app/services/money_recovery.py), [`analytics_consumer.py`](app/services/analytics_consumer.py)).
- **Shift metrics:** `recovered_today_kzt`, `confirmed_revenue_today_kzt` в `GET /shift/state` и на экране смены («Спасено действиями» / «Выручка подтверждена»).
- **Money queue:** `menu_confusion`, `booking_at_risk`; slow_chat с оценкой AOV×0.5; surfaces на дашборде.
- **iiko hourly ETL:** `sales_hourly_daily`, [`iiko_sales_hourly_sync.py`](app/services/iiko_sales_hourly_sync.py), cron worker 23:15 UTC, `GET /api/admin/analytics/sales-heatmap`, heatmap в расширенной аналитике.
- **Daily digest:** строка «Спасено действиями» для владельца.
- **Тесты:** [`tests/test_money_layer.py`](tests/test_money_layer.py).

### Исправлено (2026-05-22) — Shell v2 Role-First (G10.5 / Focus Card)

- **Focus Card Spec:** [`docs/FOCUS_CARD_SPEC.md`](docs/FOCUS_CARD_SPEC.md); mapper `adminFocusCardFromShiftState()` + макрос [`_focus_card.html`](app/templates/components/_focus_card.html).
- **Смена:** focus deck через единый Focus Card (поведение 1:1, staged nav сохранён).
- **Operator scene:** sidebar «Следующее действие» / «Все риски»; inbox hero «Открыть в смене»; `openMoneyQueueItemViaShift` для operator.
- **Smoke operator:** login → landing shift при risk → focus card → mobile context → inbox «Открыть в смене» → карточка риска → shift context.

### Исправлено (2026-05-25)

- **AI Context:** `test_bot` + `telephony` stub → `fetch_ai_read_context` + `build_llm_prompt_bundle` + snapshot; OS_TRANSITION_PLAN Phase 3 актуализирован.
- **Tenant Isolation (~95%):** `legacy_null_org_visible()` — NULL org только default org; `tenant_backfill.py` + `GET/POST /intelligence/tenant-scope-*`; per-org admin rate limit; global KB только default org; тесты `test_tenant_hardening.py`; CI `scripts/check_tenant_scope.py`; SECURITY_AUDIT обновлён.

### Исправлено (2026-05-24)

- **Event-Driven Core (~98%):** admin `bulk-cancel` → `order.cancelled`; confirm из канбана с `actor=operator`; ops-события в `DailyOrgStats`; all-time `/stats` из SUM агрегатов; funnel/ai-value event-first; backfill ops из `system_events`; миграция `20260525_daily_stats_ops_events`; тесты `tests/test_event_driven_tails.py`.
- **`/api/admin/stats` 500 на prod:** после ошибки чтения `daily_org_stats` (migration lag) сессия оставалась в aborted transaction — `await db.rollback()` в `_safe_daily_stats_mappings`, `get_recovered_today_kzt` и `with_location_scope_fallback`; тест [`tests/test_analytics_consumer_safe_read.py`](tests/test_analytics_consumer_safe_read.py).
- **daily_os_digest.py:** SyntaxError при сборке строки digest (`.replace()` ломал конкатенацию f-strings) — CI pytest collection.
- **Postgres pool:** auto default Supabase session pooler `1+0` (было `2+0`); `render.yaml` `DB_POOL_SIZE=1` — снижение `EMAXCONNSESSION` при деплое.
- **Supabase EMAXCONNSESSION (Render):** `SUPABASE_PREFER_TRANSACTION_POOLER=true` в `render.yaml` — авто `:5432`→`:6543`; demo-login fast path (кэш `DEMO_ORGANIZATION_ID` / startup cache) без SELECT; 503 вместо 500 при перегрузке pooler.
- **_tab_dashboard.html:** лишний `</div>` после hero owner impact — регресс layout + `test_all_screen_templates_have_balanced_divs`.
- **Тесты:** sidebar `isTabShownInSidebar`, analytics special events (`shift.focus_completed`, `order.draft_recovered`), visibility copy «Спасено действиями».
- **twilio_media:** fallback `audioop` при отсутствии `audioop-lts` на Python 3.13+.

### Изменено (2026-05-24) — единый demo pitch

- **Login:** одна кнопка «Посмотреть демо» → demo-login + autoplay `money_rescue_30s`; после Esc — read-only осмотр; ↻ — повтор показа.

### Добавлено (2026-05-24) — G10.8 Demo Scene

- **30-сек сценка:** `money_rescue_30s` — hook → tension → auto «Готово» → Live Impact +1 200 ₸ → следующий риск; **единая кнопка «Посмотреть демо»** на login (pitch → read-only осмотр).

### Исправлено (2026-05-22) — Postgres pool (Supabase EMAXCONNSESSION)

- **Пул SQLAlchemy:** вместо `pool_size=20` / `max_overflow=10` — авто по DSN: Supabase session pooler (`:5432`) → **`2+0`** на процесс (было 4+1); env `DB_POOL_SIZE`, `DB_MAX_OVERFLOW`; дефолты в `render.yaml`.
- **Transaction pooler (:6543):** `statement_cache_size=0` для asyncpg.
- Док: [docs/SUPABASE_MIGRATION.md](docs/SUPABASE_MIGRATION.md), [.env.example](.env.example).

### Изменено (2026-05-22) — UI polish: операторский пульт (раунд 3)

- **Плотность операций:** переключатель «Обычный / Компактный» в шапке на вкладках смены, inbox, заказов, чатов и броней; `localStorage` `restomind_density:operations`; compact — список чатов без preview, плотнее таблица заказов и kanban.
- **Шапка:** без дубля названия заведения при одном филиале (остаётся в сайдбаре); dismissible подсказка Ctrl+K.
- **Брони:** боковая «Справка» сворачивается, пока на неделе нет броней.
- **Маркетинг:** подсказка, почему «Создать черновик» неактивна.
- **Чаты:** заголовок «Диалоги» только на мобилке (на десктопе — в глобальной шапке).

### Изменено (2026-05-22) — UI polish: операторский пульт (раунд 2)

- **Смена:** одна строка `shiftStatusHeadline()` вместо бейджей S* + дубля operational_label.
- **Заказы:** без слова «канбан»; пустые колонки — блеклый текст; общая плашка действий над доской.
- **Inbox:** синхронная загрузка (`inboxClientsPaneLoading`); обучающий текст — dismiss в localStorage.
- **Чаты:** фон ленты `#f1f5f9` (slate-100); «Сохранено» у заметки для коллег.
- **Дашборд:** пустая «Живая ОС» — зелёный статус автопилота.
- **Брони:** длинная подсказка empty state скрывается по клику.

### Изменено (2026-05-22) — UI-аудит: владелец и оператор

- **Смена:** бейдж состояния через `shiftStateLabel()` / `shiftStateReasonLabel()` (без «Режим S3»).
- **Диалоги:** системные сбои ИИ — нейтральный стиль, схлопывание дублей, CTA «Взять диалог»; поле ввода активно при fallback; профиль гостя — loading/error/empty.
- **Настройки → Профиль:** человекочитаемые подписи WhatsApp/Telegram + блок «Для техспециалиста».
- **Сайдбар admin:** секция «Управление» выше «Операции».
- **Дашборд:** empty states (выручка, revenue leak, воронка); блок «Сейчас» с live-счётчиками.
- **Заказы:** при 0 заказов — вид «Список»; свёрнутые поздние колонки kanban.
- **Меню:** select раздела + top-5 pills вместо 20+ чипов.
- **ИИ-аналитика:** в режиме «Обзор» — вкладки «Вклад ИИ» + «Инсайты», остальное в «Ещё»; «AI Value» → «Подробнее о вкладе ИИ».
- **Inbox:** единый skeleton при первичной загрузке клиентской очереди.

### Изменено (2026-05-22) — Docs: Launch Window sync

- **UI_DESIGN_SYSTEM / ROADMAP / UI_MAP:** Role-First IA, P5 «выполнен», секция P3 Growth & BI (KPI iiko, iiko-маркетинг MVP), карта экранов оператора и «Официанты» в подробной аналитике.

### Добавлено (2026-05-22) — P3 Growth: KPI официантов из iiko

- **ETL:** [`iiko_waiter_kpi_sync.py`](app/services/iiko_waiter_kpi_sync.py) — iiko Cloud deliveries + iiko Office waiter report → `waiter_registry`, `waiter_kpi_daily`, audit `iiko_sync_runs`; миграция [`20260523_p3_waiter_kpi.py`](alembic/versions/20260523_p3_waiter_kpi.py).
- **Cron:** `waiter_kpi_sync_scheduled_tick` (ежедневно 22:30 UTC) в [`worker.py`](app/worker.py).
- **Admin API:** `POST/GET /api/admin/analytics/waiter-kpi/*` — sync, рейтинг, CSV, sync-status ([`waiter_kpi.py`](app/api/admin/waiter_kpi.py)).
- **UI:** блок «Официанты» на вкладке расширенной аналитики ([`_tab_analytics.html`](app/templates/screens/_tab_analytics.html), [`admin-app.js`](app/static/js/admin-app.js)).
- **Spike:** [`docs/IIKO_WAITER_KPI_SPIKE.md`](docs/IIKO_WAITER_KPI_SPIKE.md) + fixtures Cloud/Office.
- **Тесты:** [`test_iiko_waiter_kpi_sync.py`](tests/test_iiko_waiter_kpi_sync.py), [`test_waiter_kpi_api.py`](tests/test_waiter_kpi_api.py).

### Изменено (2026-05-22) — E0.1 tail (admin API split)

- **`legacy_ops.py` удалён:** домены [`demo.py`](app/api/admin/demo.py), [`settings_ops.py`](app/api/admin/settings_ops.py), [`export.py`](app/api/admin/export.py); composite router — [`core.py`](app/api/admin/core.py).
- Fix: `GET /api/admin/settings/environment` (был без декоратора); `RedisPurgePhoneBody.confirm` + `phone`.

### Изменено (2026-05-22) — Launch runbook

- **Ops:** [`docs/DEPLOY_RUNBOOK.md`](docs/DEPLOY_RUNBOOK.md) — единый чеклист выкатки на Render (Web + ARQ worker), env-матрица Supabase/Upstash, post-deploy smoke, troubleshooting; ссылки на FINAL_MILE sign-off.

### Изменено (2026-05-22) — Rule 8 tail #2 (shift + operator queue)

- **Operator queue:** фильтр «Неразрешённые / Разрешённые» → «В работе / Закрытые»; KPI-подпись без «неразрешённые» ([`_tab_operator_queue.html`](app/templates/screens/_tab_operator_queue.html)).
- **Экран смены:** `shiftStateLabel()` / `shiftStateReasonLabel()` в [`admin-app.js`](app/static/js/admin-app.js) — оператор видит «Стабильно» и «есть гость без ответа в красной зоне» вместо `S3` и `red_chat_exists` ([`_tab_shift_control.html`](app/templates/screens/_tab_shift_control.html)).
- Тесты: [`tests/test_ui_operator_language.py`](tests/test_ui_operator_language.py) (`test_operator_queue_no_dev_resolved_labels`, `test_shift_control_no_raw_state_leak`).

### Изменено (2026-05-22) — Rule 8 tails + BI/Marketing MVP

- **Inbox / Финал:** «Action Queue» → «Очередь помощи»; «Final Mile» → «Финал»; вкладка **Финал** — русские подписи (сводка ОС, голосовой ИИ, закупки); analytics без dev-жаргона (`UpsellRule`, `Menu Engineering`, JSON-поля в подсказках).
- **Настройки → Команда:** «StaffMind onboarding» → «Обучение сотрудников»; без англ. placeholder/empty state.
- **Дашборд:** карточка «Пик продаж сегодня» открывает под-таб аналитики (`dashboardTab: 'analytics'`).
- **BI MVP:** `sales_peak_today` в `GET /stats` + карточка на дашборде; подсказки допродаж — язык оператора ([`sales_insights.py`](app/services/sales_insights.py)).
- **iiko → маркетинг (MVP):** `POST /api/admin/marketing/sync-iiko-customers` — импорт телефонов из истории доставок iiko Cloud; UI «Обновить из iiko» в «Маркетинг».
- **Rule 8 (ранее в этот день):** убран блок «Очередь задач» с дашборда; «DRAFT × AOV» → операторский язык; «эскалация» → «передано оператору»; шапка operational status; маркетинг без `LOYALTY_*` env-имён.
- Тесты: [`tests/test_ui_operator_language.py`](tests/test_ui_operator_language.py), [`tests/test_sales_insights.py`](tests/test_sales_insights.py), [`tests/test_iiko_customer_sync.py`](tests/test_iiko_customer_sync.py), [`tests/test_owner_dashboard.py`](tests/test_owner_dashboard.py).

### Изменено (2026-05-22) — Sprint A/B performance

- **Bot (Sprint A):** `schedule_save_ai_context_snapshot` — снимок AI-контекста fire-and-forget до LLM; typing indicator Meta Cloud API (`send_typing_indicator`) или fast ack «Секунду, смотрю меню…» после `BOT_SLOW_ACK_DELAY_SEC`; heuristic model routing (`resolve_model_tier`, `AI_FAST_MODEL_*`) с fast→strong rerun на order/book.
- **Admin (Sprint B):** lazy init — `loadChatList` только на chats/inbox; `loadRevenueLeak`/`loadShiftState` только dashboard/shift; после login — `loadTabData` текущей вкладки + `deferIdleWork` для profile/integrations; ETag/`If-None-Match` → 304 на `GET /organization/profile` и `GET /integrations/status`.
- Тесты: [`tests/test_perf_sprint_ab.py`](tests/test_perf_sprint_ab.py).

### Изменено (2026-05-21) — Role-first tails (Sprint 5 polish)

- **Mobile bottom nav:** `_bottom_nav.html` — `isTabVisibleForRole()`; оператор: Inbox вместо Dashboard; manager/admin: Dashboard + Menu; `bottomNavMoreTabActive()`.
- **Demo-login:** `applyRoleDefaultLanding()` + `_afterAuthTabBootstrap()` (как обычный login).
- **`analyticsDensity`:** persist при `navigateToTab('dashboard', { dashboardTab })` через `_persistAnalyticsDensity()`.
- **Shift badge polling:** `shouldPollShiftStateBadge()` + `_syncShiftStatePolling()` — обновление риска в сайдбаре вне вкладки «Смена».
- **Доки:** `OS_TRANSITION_PLAN.md` § UI Layer синхронизирован с Sprint 5.
- **Baseline:** пересняты PNG в `docs/ui/baseline/` (`capture_admin_u0_baseline.py`).

### Изменено (2026-05-21) — Role-first Admin IA (Sprint 5 pivot)

- **Mode Bar убран** из `screens/_header.html`; навигация по роли staff, не по трём режимам.
- **Сайдбар:** `isTabVisibleForRole()` — operator (5 вкладок), manager (+ menu, dashboard, ai_center), admin (все).
- **Smart landing оператора:** `applyRoleDefaultLanding()` — shift при `risk_kzt > 0` или `focus.id`, иначе inbox.
- **Дашборд:** один переключатель «Обзор / Подробная аналитика»; дублирующая кнопка справа убрана.
- **Смена calm-empty:** CTA в inbox/chats при S0/S3 без риска (`shiftIsCalmEmpty()`).
- Тесты: [`tests/test_role_first_nav.py`](tests/test_role_first_nav.py); обновлён sprint1 sidebar test.

### Добавлено (2026-05-21) — Control Plane + BI MVP tails

- **Chat context → trace timeline:** `GET /chats/{phone}/state` отдаёт `latest_trace_id`; правая колонка чата — кнопка «Цепочка trace →» (`openActiveChatTraceTimeline`).
- **BI MVP (локальные часы):** `GET /analytics` — `sales_by_hour_local`, `sales_insights` (`peak_hours_local`, `upsell_time_hint` для UpsellRule `time_of_day`); UI аналитики показывает локальный TZ филиала.

### Добавлено (2026-05-21) — P5 backlog tails (StaffMind / SupplyMind / Control Plane UI)

- **StaffMind tracker metrics:** `questions_asked`, `step_target`, `test_passed` в `onboarding_public()` и `progress_json`; инкремент вопросов в `answer_staff_question()` — [`staffmind.py`](app/services/staffmind.py).
- **SupplyMind checklist PATCH:** `PATCH /supplymind/drafts/{id}` принимает `items[]` с `{idx, checked}`; persist в `items_json`; UI чекбоксов пишет на сервер — [`supplymind.py`](app/services/supplymind.py), [`admin-app.js`](app/static/js/admin-app.js).
- **Control Plane timeline UI:** панель «Цепочка trace_id» в AI Center → OS; `loadTraceTimeline()`, `openTraceTimeline()` — [`_tab_ai_center.html`](app/templates/screens/_tab_ai_center.html).

### Документация (2026-05-21) — Focus-Driven OS Sprint 1–4 audit

- Gap-audit: все 4 спринта wired end-to-end (mixins в `adminApp()`, шаблоны/CSS, inbox `loadMoneyQueue` на открытии вкладки, voice `location_id`, Command Bar без конфликта Ctrl+K с `handleGlobalKeydown`).
- Синхронизированы статусы: ROADMAP P5, `OS_TRANSITION_PLAN.md` § UI Layer, `UI_MAP.md`, `UI_DESIGN_SYSTEM.md` (убраны ⏳ / backlog Sprint 3–4).
- Тесты: 30 passed — `test_focus_driven_os_sprint{1,2,3,4}.py`, `test_voice_staging.py`, `test_twilio_routing.py`.

### Добавлено (2026-05-21) — Focus-Driven OS Sprint 4 (Command Bar)

- **Command Bar (`adminMixinCommandBar`):** Ctrl+K / Cmd+K открывает палитру поверх legacy global search (Strangler); `commandBarOpen`, `commandQuery`, `parseCommand()`, `executeCommand()`, `handleCommandBarKeydown()`; `window.adminCommandBar`.
- **Префиксы:** `/leak` → Intelligence + dashboard/revenue leak; `/red` → Shift + вкладка смены; `/force-close` → настройки ресторана + модалка экстренного закрытия.
- **UI:** `components/_command_bar.html`, `ds-command-bar-*` в `src/css/admin-input.css`; include в `_modals.html`; кнопки «Поиск» в шапке → `openCommandBar()`.
- Тесты: [`tests/test_focus_driven_os_sprint4.py`](tests/test_focus_driven_os_sprint4.py).

### Добавлено (2026-05-21) — Focus-Driven OS Sprint 3 (Action Queue + voice tail)

- **Action Queue inbox:** [`_tab_inbox.html`](app/templates/screens/_tab_inbox.html) — карточки из `GET /inbox/money-queue` с `ds-status-*` semantics; дублирующий блок убран из [`_tab_operator_queue.html`](app/templates/screens/_tab_operator_queue.html).
- **`adminMixinInboxActionQueue()`:** хелперы очереди (`moneyQueueStatusClass`, `loadInboxActionQueue`, `runMoneyQueueAction`) и voice strip (`refreshVoiceCallStrip`, `loadVoiceCallLogs` с `locationQueryParams()` + pagination).
- **Voice `location_id` в payload:** `record_voice_call(..., location_id=)` + Twilio routing [`resolve_location_from_twilio_number`](app/services/twilio_routing.py); кэш `twilio:location:{callSid}` в webhook.
- **Final Mile voice strip:** [`_tab_ai_center.html`](app/templates/screens/_tab_ai_center.html) — `ds-status-surface` badges, кнопка «Ещё», фильтр по точке.
- Тесты: [`tests/test_focus_driven_os_sprint3.py`](tests/test_focus_driven_os_sprint3.py), расширены [`tests/test_voice_staging.py`](tests/test_voice_staging.py), [`tests/test_twilio_routing.py`](tests/test_twilio_routing.py).

### Добавлено (2026-05-21) — Focus-Driven OS Sprint 2 (Shift split + staged nav)

- **Shift split layout:** `_tab_shift_control.html` — Focus Deck (слева) + Context Dock (справа) на `≥lg`; partials `_shift_focus_chat.html` (slow_chat / pulse red|amber), `_shift_focus_order.html` (abandoned_draft / pending_prepay).
- **Mobile staged nav (LAW 2):** `adminMixinShiftStagedNav()` — `mobileActiveScreen` `focus`|`context`, `openShiftContext()`, `backToShiftFocus()`, кнопка «⬅ Назад к задаче» на `<lg`.
- **CSS:** `src/css/admin-input.css` — `.ds-shift-split*`, `.ds-shift-pane--hidden-mobile`; `npm run build:admin-css`.
- Тесты: [`tests/test_focus_driven_os_sprint2.py`](tests/test_focus_driven_os_sprint2.py).

### Добавлено (2026-05-21) — Focus-Driven OS Sprint 1 (shell complete)

- **Mode Engine (`admin-app.js`):** `currentMode` (`shift` \| `control` \| `intelligence`), `setMode()`, `_bootstrapAdminMode`, matrix mode↔tab по [`docs/UI_MAP.md`](docs/UI_MAP.md); `window.adminModeEngine` + event `restomind:admin-mode`; persistence `localStorage.restomind_admin_mode`.
- **Mode Bar:** `components/_mode_bar.html` (три режима на `ds-segmented`, `@click="setMode(...)"`); include в `screens/_header.html` при `authenticated`.
- **Universal Semantics + Mode Bar CSS:** `src/css/admin-input.css` — `ds-status-ok|warn|danger|ai|inactive`, `--color-mode-*`, `.ds-mode-bar*` / `.ds-status-shift|control|intelligence`; сборка `npm run build:admin-css`.
- **Условный сайдбар (Strangler):** `screens/_sidebar.html` — `navItems` и секции фильтруются через `isTabInCurrentMode()`; legacy-навигация не удалена.
- Тесты: [`tests/test_focus_driven_os_sprint1.py`](tests/test_focus_driven_os_sprint1.py).

### Исправлено (2026-05-21) — Admin checkSession 500 (prod resilience)

- **`check_operational_status`:** сравнение `force_closed_until` с aware UTC — устранён `TypeError` на `GET /organization/profile` при naive timestamp из Postgres.
- **`integration_health`:** безопасное чтение `organization_integration_sync` при отставании миграции `20260522_iiko_office_inventory` (`last_inventory_sync_*`).
- **Location scope fallback:** `money_queue`, `revenue_leak`, `GET /chats` — retry без `location_id` фильтра при schema lag (`20260520_locations_phase11`).
- **`GET /chats` + `location_id`:** legacy `chat_logs` с `location_id IS NULL` снова видны при выборе точки (до backfill после миграции Phase 1.1).
- **`shift_state_engine`:** Redis S1 latch / focus lease / exclusions не рвут `GET /shift/state` при недоступном Redis.
- Тесты: [`tests/test_admin_session_deps_http.py`](tests/test_admin_session_deps_http.py) (`checkSession` smoke), [`tests/test_time_context_schedule.py`](tests/test_time_context_schedule.py) (naive `force_closed_until`).

### Исправлено (2026-05-21) — Admin 500 и Alpine latencyData

- **Alpine:** `latencyData?.sla_violations` в `_tab_intelligence.html` — убран crash до загрузки Intelligence.
- **Admin API:** `await _session_staff_user` / `_session_is_superadmin` в `chats.py`, `bookings.py`, `orders.py` — устранены 500 на `/chats/{phone}`, `/bookings`, ручной заказ.
- **Analytics:** безопасное чтение `daily_org_stats` при отставании миграций на prod (`/stats`, `/funnel` fallback на SQL).
- Тесты: [`tests/test_admin_session_deps_http.py`](tests/test_admin_session_deps_http.py).

### Документация (2026-05-21) — Focus-Driven OS (Admin Shell)

- Зафиксированы три закона Execution OS, матрица режимов SHIFT/CONTROL/INTELLIGENCE, Universal Semantics (`ds-status-*`), mobile staged nav, locality voice calls, Focus Card ↔ `GET /shift/state`.
- Обновлены: [`docs/UI_DESIGN_SYSTEM.md`](docs/UI_DESIGN_SYSTEM.md), [`docs/OS_TRANSITION_PLAN.md`](docs/OS_TRANSITION_PLAN.md) § UI Layer, [`docs/ROADMAP.md`](docs/ROADMAP.md) P5 (4 спринта, без дублирования G7/G10/Voice API).

### Добавлено (2026-05-21) — Superadmin · Control Plane · Admin UI

- **Superadmin:** поля `iiko_api_login`, `iiko_terminal_group_id`, `telegram_ops_chat_id` в таблице ресторанов; `SuperadminAuditLog` + миграция `20260521_superadmin_audit`; `GET /api/superadmin/audit`; аудит на ключевых мутациях (status, credentials, schedule, sync, password reset, approve/reject). UI «Журнал действий Super Admin». Тесты: [`tests/test_superadmin_audit.py`](tests/test_superadmin_audit.py).
- **Control Plane Phase 2 (start):** `trace_context.py` — `contextvars`, seed из WhatsApp `message_id`; propagation webhook → ARQ → `emit_event` → `order_meta` / AI logs; structured `[trace_id=…]` в логах. Тесты: [`tests/test_control_plane_trace.py`](tests/test_control_plane_trace.py).
- **Admin UI:** Voice call log strip, StaffMind step tracker, SupplyMind раскрываемые черновики в Final Mile / **Настройки → Команда** (см. backlog gaps в ROADMAP).

### Добавлено (2026-05-21) — Launch prep (voice pagination · Control Plane tail · deploy checklist)

- **`GET /voice/calls`:** пагинация `limit`/`offset`, `total`, опциональный `location_id` (фильтр по `payload_json`, запись в webhook — backlog); RBAC локаций.
- **Control Plane Phase 2 tail:** `parent_event_id`/`caused_by` на `BusinessEvent`; trace в iiko/WA/operator logs; `GET /trace-timeline` (API без UI-панели); [`trace_timeline.py`](app/services/trace_timeline.py).
- **Ops:** секция **D. Deploy** в [`docs/FINAL_MILE_OPS_SIGNOFF.md`](docs/FINAL_MILE_OPS_SIGNOFF.md) — `alembic upgrade head`, перезапуск ARQ, cron ticks.

### Добавлено (2026-05-21) — Voice call log API

- **`GET /api/admin/intelligence/voice/calls`:** список `voice_call_logs` (status, mode, transcript, `duration_sec`/`recording_url` из `payload_json`); tenant scope; тест `test_voice_calls_api_lists_org_logs`. UI: `loadVoiceCallLogs()` в Final Mile (пока без `locationQueryParams` / offset на клиенте).

### Исправлено (2026-05-21) — docs↔code sync (audit drift)

- Синхронизированы [`CHANGELOG.md`](CHANGELOG.md), [`docs/ROADMAP.md`](docs/ROADMAP.md), [`docs/REMAINING_UPDATES.md`](docs/REMAINING_UPDATES.md), [`docs/UI_MAP.md`](docs/UI_MAP.md), [`codebase.md`](codebase.md), [`docs/CONTROL_PLANE.md`](docs/CONTROL_PLANE.md), [`docs/SUPPLYMIND_STAFFMIND.md`](docs/SUPPLYMIND_STAFFMIND.md): убраны устаревшие «placeholder until API»; backlog gaps (SupplyMind item PATCH, StaffMind metrics, voice location_id payload, timeline UI) явно в ROADMAP `[ ]`.

### Исправлено (2026-05-21) — Alpine null-safe + revenue-leak 500

- **Frontend:** `shiftState`, `revenueLeak`, `moneyQueue` инициализируются пустыми объектами (`adminDefault*()`); optional chaining в sidebar/bottom nav/operator queue; сброс при смене локации — без `null` (Alpine не ломает реактивный цикл до fetch).
- **Backend:** `revenue_leak._slow_response_kzt` — `GROUP BY user_id` для PostgreSQL; `_menu_confusion_kzt` — убран невалидный `GROUP BY`; `GET /revenue-leak?location_id=` — FastAPI `int | None`.
- **Syntax:** IndentationError в `intelligence.py` (replay snapshot → legacy menu fallback).

### Final Mile gap closure (2026-05-21)

- **iiko Office RBAC UI:** подтверждено тестами `test_iiko_office_connections_rbac_ui_wired`, operator PATCH → 403.
- **GuestCare Google:** auto-sync только 2GIS; Google — `google_manual_only` + ручной import; Places API WONTFIX.
- **HTTP smoke:** [`tests/test_final_mile_smoke.py`](tests/test_final_mile_smoke.py) — Final Mile API endpoints.
- **Ops (остаётся вручную):** Voice Twilio + iiko Office live — [`docs/FINAL_MILE_OPS_SIGNOFF.md`](docs/FINAL_MILE_OPS_SIGNOFF.md).
- **Admin i18n ru/kk:** deferred, ROADMAP L141.

### Final Mile 100% — код и чеклисты (2026-05-21)

- **iiko Office RBAC UI:** `canStaffAdminOnly()` + disabled inputs/hint в Настройки → Подключения; guard и 403 в `saveIikoOfficeConfig()`; тест operator PATCH → 403.
- **GuestCare 2GIS primary:** подсказки в настройках ресторана и вкладке guestcare; отображение `sync_meta.limitations`; ROADMAP/AI_OPERATIONS — Google Places API вне scope.
- **Ops / smoke docs:** [`docs/FINAL_MILE_BROWSER_SMOKE.md`](docs/FINAL_MILE_BROWSER_SMOKE.md), [`docs/FINAL_MILE_OPS_SIGNOFF.md`](docs/FINAL_MILE_OPS_SIGNOFF.md); `capture_admin_u0_baseline.py` — shots guestcare + shift.
- **ROADMAP:** Voice `[ ]` и iiko live smoke — только после sign-off в OPS doc (код ✅).

### Документация (2026-05-21) — sync audit drift

- Синхронизированы [`docs/REMAINING_UPDATES.md`](docs/REMAINING_UPDATES.md), [`docs/FINAL_MILE_IMPLEMENTED.md`](docs/FINAL_MILE_IMPLEMENTED.md), [`docs/AI_OPERATIONS.md`](docs/AI_OPERATIONS.md), [`docs/OS_TRANSITION_PLAN.md`](docs/OS_TRANSITION_PLAN.md), [`docs/ROADMAP.md`](docs/ROADMAP.md): head `20260522_iiko_office_inventory`; iiko Office + Voice Realtime + GuestCare 2GIS — code ✅; Voice `[ ]` = ops/staging gate; Phase 3 ~100%; StaffMind UI + RBAC; единый контракт `GET/POST /snapshots*`.

### Исправлено (2026-05-21) — docs↔code audit

- **P0:** удалён дубликат `GET /api/admin/intelligence/snapshots`; один handler с `retention_days` и расширенными полями items.
- **P2:** RBAC `require_staff_manager_or_admin` на мутациях StaffMind onboarding (`POST …/onboarding`, `POST …/message`); operator — только `GET`.

### Добавлено (2026-05-21) — Platform polish tails

- **StaffMind Step 1:** `StaffUser.meta_json` — `role_metadata` (title/department) и `assigned_location_ids`; `PATCH /api/admin/staff/{id}`; UI в **Настройки → Команда**.
- **Franchise Phase 1.2:** per-location analytics rollup из `SystemEvent` (`rollup_location_event_stats`) для non-shift маршрутов в [`analytics.py`](app/api/admin/analytics.py).
- **P15 coach-marks:** `POST /api/admin/auth/tour-complete` → `StaffUser.meta_json.tour_completed_at`; `/auth/me` отдаёт флаг.
- **AI Context Snapshot:** replay из `menu_prices_snapshot`, если `menu_context_text` пуст.
- **Ops:** [`docs/TELEGRAM_DIGEST_STAGING.md`](docs/TELEGRAM_DIGEST_STAGING.md).
- **Тесты:** [`tests/test_platform_polish_tails.py`](tests/test_platform_polish_tails.py).

### Удалено (2026-05-21) — G10.3 Legacy shift cleanup

- **API:** удалён `GET /api/admin/shift-control` (G9); единственный контракт смены — `GET /api/admin/shift/state`, `POST /api/admin/shift/action`, heartbeat `POST|DELETE /shift/heartbeat`.
- **Сервис:** [`shift_control.py`](app/services/shift_control.py) — только `_saved_today_kzt` для engine; `build_shift_control` удалён.
- **UI:** heartbeat смены без legacy `owner_token` в [`admin-app.js`](app/static/js/admin-app.js); polling 45s сохранён (WS push не добавлялся).
- **Тесты:** `test_legacy_shift_control_endpoint_removed`, `test_shift_control.py` → метрики; регрессия shift: `pytest tests/test_shift_api_http.py tests/test_shift_state_engine.py tests/test_shift_control.py -q`.

### Добавлено (2026-05-21) — GuestCare External auto-sync

- **GuestCare parser + sync:** [`guestcare_parser.py`](app/services/guestcare_parser.py) — conservative JSON-LD / embedded state parse для 2GIS; Google — best-effort + documented Places API limitation. [`external_reviews_sync.py`](app/services/external_reviews_sync.py) — upsert в `external_reviews` с dedupe `(organization_id, source, external_id)`.
- **API:** `POST /api/admin/intelligence/reviews/external/sync`; `GET …/reviews/external` возвращает `sync_meta` из `Organization.meta_json.guestcare_sync`.
- **Worker:** ARQ `external_reviews_sync` + cron `external_reviews_sync_scheduled_tick` (02:10, 14:10 UTC); env `GUESTCARE_SYNC_ENABLED`.
- **UI:** кнопка «Синхронизировать» во вкладке `guestcare` (`_tab_ai_center.html`, `syncGuestCareReviews` в `admin-app.js`).
- **Тесты:** [`tests/test_guestcare_parser.py`](tests/test_guestcare_parser.py), fixtures [`tests/fixtures/guestcare/`](tests/fixtures/guestcare/).

### Добавлено (2026-05-20) — Integration epics (SupplyMind · Voice)

- **SupplyMind — чеклист закупки:** `supply_purchase_drafts` как внутренний чеклист (без экспорта в iiko); статусы `draft` → `approved` → `completed` / `cancelled`; `GET/PATCH /api/admin/intelligence/supplymind/drafts/{id}`, `GET …/export?format=csv`; панель «Чеклист закупки» в `aiCenterTab=final_mile` ([`docs/SUPPLYMIND_STAFFMIND.md`](docs/SUPPLYMIND_STAFFMIND.md)).
- **SupplyMind — iiko Office inventory sync (partial):** [`iiko_office_client.py`](app/integrations/iiko_office_client.py), [`iiko_inventory_sync.py`](app/services/iiko_inventory_sync.py) → upsert `inventory_stock_snapshots` (`source=iiko_office`); `POST/GET /api/admin/inventory/sync-iiko|sync-status`; `GET/PATCH /api/admin/organization/iiko-office` (шифрование пароля, UI в Настройки → Подключения); маппинг `store_id` → `location_id` (`location_id`, `store_location_map`); RBAC `require_staff_manager_or_admin` на sync/мутации SupplyMind; ARQ cron 6 ч; status/manual sync в `aiCenterTab=final_mile`. Без live Office — тесты на fixture.
- **Voice AI — OpenAI Realtime connector:** `voice_ai_mode=realtime` — bridge Twilio μ-law ↔ OpenAI Realtime WSS ([`voice_realtime/`](app/services/voice_realtime/), [`twilio_routing.py`](app/services/twilio_routing.py)); `stt_fallback` сохранён; env `OPENAI_REALTIME_*`. **Хвост:** staging call на реальном Twilio номере ([`docs/VOICE_AI_SPIKE.md`](docs/VOICE_AI_SPIKE.md)).
- **Voice AI — production tail:** Realtime tools `lookup_menu` (menu DB по `organization_id`) и `escalate_to_whatsapp` (отправка WA + message accounting); `require_staff_admin` на `POST /voice/config`; UI — `realtime_ready` + 403 hint; чеклист [`docs/VOICE_STAGING_CHECKLIST.md`](docs/VOICE_STAGING_CHECKLIST.md); тесты [`test_voice_realtime.py`](tests/test_voice_realtime.py), [`test_voice_staging.py`](tests/test_voice_staging.py).
- **Тесты (integration tails):** [`test_shift_api_http.py`](tests/test_shift_api_http.py) (GET/POST shift API), [`test_healing_actions_cron.py`](tests/test_healing_actions_cron.py) (cancellation_surge, ai_message_drop), [`test_voice_staging.py`](tests/test_voice_staging.py) (incoming TwiML + dispatch), iiko HTTP mock в [`test_iiko_inventory_sync.py`](tests/test_iiko_inventory_sync.py).

### Изменено (2026-05-20) — G10 Simplification Map (текущая модель)

- **Chat:** [`chat_serializer.py`](app/services/chat_serializer.py) — `chat:lock` (15s) + FIFO `chat:queue`; убраны `active_pipeline`, epoch, shadow.
- **Shift:** `shift:active_focus:{org}:{operator}` — один lease TTL 45s; убраны `focus_lock`/`focus_claim`/`owner_token` в engine.
- **Healing:** realtime (`payment.failed`, `ai.escalated`) + `heal:mute` 30m; cron только cold (7d cancel, ai drop, WA nudge). Убран `heal:fp`.
- **Доки:** синхронизированы [`G10_SEMANTIC_CONTRACT.md`](docs/G10_SEMANTIC_CONTRACT.md), [`G10_FAILURE_SIMULATION.md`](docs/G10_FAILURE_SIMULATION.md), [`G10_SHIFT_CONTROL_PLANE.md`](docs/G10_SHIFT_CONTROL_PLANE.md), [`AI_OPERATIONS.md`](docs/AI_OPERATIONS.md).
- **Freeze:** новые consistency-слои без prod-инцидента — [`docs/G10_SIMPLIFICATION.md`](docs/G10_SIMPLIFICATION.md).

### Добавлено (2026-05-20) — G10 Production Hardening (промежуточный слой, superseded)

- Промежуточно: `active_pipeline`, dual focus keys, `heal:fp` — **заменено** Simplification Map (см. блок выше).
- Сохранено: S1 hysteresis, degraded UI, heartbeat API, FS-8/9/10 в failure sim (обновлены под simplify).

### Добавлено (2026-05-20) — G10 v1.2 Semantic Hardening

- **Projection diff:** `presentation.projection_gap`, `state_reason`, `debug_trace`; UI баннер расхождения.
- **Ownership:** `focus.ownership`, complete org-wide SETNX; Redis keys — см. Simplification (`active_focus`).
- **Redis:** prune ghost SET members; action labels «Другое дело» / «Не сейчас».
- **Contract:** [`docs/G10_SEMANTIC_CONTRACT.md`](docs/G10_SEMANTIC_CONTRACT.md) §7–§10.

### Добавлено (2026-05-20) — G10 v1.1 Semantic Contract + Trust Layer

- **Contract:** [`docs/G10_SEMANTIC_CONTRACT.md`](docs/G10_SEMANTIC_CONTRACT.md) — system truth vs operational projection, guarantees, operator mental model, UI renderer, logging.
- **Engine:** focus lock 45s; `shift:next:*` отдельно от `shift:skip:*` + SET index; `SETNX` complete; `presentation.empty_focus_reason`; structured logs `shift_state_built` / `shift_action_applied`.
- **UI:** баннер при `focus=null` по `presentation` (не эвристика на клиенте).

### Добавлено (2026-05-20) — G10 v1 Next Action Mode (Money Core)

- **State machine:** S0–S5 детерминированно из G5–G8 сигналов — [`shift_state_engine.py`](app/services/shift_state_engine.py).
- **API:** `GET /api/admin/shift/state`, `POST /api/admin/shift/action` (next/skip/complete, Redis dedupe 10/60 мин, событие `shift.focus_completed`).
- **Контракт:** engine pure (state из G5–G8 без Redis); Redis — только фильтр skip/done; `next` = soft skip + refresh; `complete` — единственная business mutation (idempotent).
- **UI:** `_tab_shift_control.html` — один layout на state, focus card + queue preview; `loadShiftState()` / `runShiftStateAction()` в [`admin-app.js`](app/static/js/admin-app.js).
- **Тесты:** [`tests/test_shift_state_engine.py`](tests/test_shift_state_engine.py).
- **Операции:** PR rollout, failure modes, hardening checklist — [`docs/G10_SHIFT_CONTROL_PLANE.md`](docs/G10_SHIFT_CONTROL_PLANE.md).

### Добавлено (2026-05-20) — G9 Shift Control Screen (Money Core)

- **Экран смены:** вкладка «Смена» — единый контур G5–G8: метрики (под риском / сохранено / ожидание), focus «Что делать сейчас», очередь до 8 позиций, quick actions, live chats, orders strip, leak summary, system status.
- **API:** `GET /api/admin/shift-control?location_id=` — [`shift_control.py`](app/services/shift_control.py); reuse `build_money_queue` + `build_revenue_leak`.
- **UX:** оператор стартует на вкладке `shift`; auto-refresh 45с; mobile bottom nav «Смена».

### Добавлено (2026-05-20) — G8 Revenue Leak → Action Layer (Money Core)

- **Action surfaces:** `GET /api/admin/intelligence/revenue-leak` возвращает `surfaces[]` — брошенные заказы, медленные чаты, pending prepay, крупные зависшие; агрегаты через [`summarize_queue_counts`](app/services/money_queue.py).
- **1-клик:** `POST /api/admin/intelligence/revenue-leak/recover-drafts` — ручной запуск G6 draft recovery; навигация в Inbox/чаты/заказы из дашборда.
- **UI:** блок «Деньги под контролем» в `_tab_dashboard.html`, `runRevenueLeakAction()` и `chatPulseFilter` в [`admin-app.js`](app/static/js/admin-app.js).

### Добавлено (2026-05-20) — G7 Inbox = money queue (Money Core)

- **Money queue:** единая очередь «Деньги на кону» в Inbox — брошенные DRAFT (&gt;30 мин), заказы с `prepayment_status=pending`, медленные чаты (pulse amber/red ≥2 мин).
- **API:** `GET /api/admin/inbox/money-queue?location_id=` — [`money_queue.py`](app/services/money_queue.py), endpoint в [`analytics.py`](app/api/admin/analytics.py).
- **UI:** блок в `_tab_operator_queue.html`, `loadMoneyQueue()` / счётчик в бейдже Inbox в [`admin-app.js`](app/static/js/admin-app.js).
- **Тесты:** [`tests/test_money_queue.py`](tests/test_money_queue.py).

### Добавлено (2026-05-20) — G6 Draft Recovery (Money Core)

- **Draft Recovery:** cron `draft_recovery_scheduled_tick` ищет DRAFT старше 45 мин, шлёт WhatsApp-кнопки «Оформить» / «Отменить», dedupe Redis 24ч/заказ, восстанавливает `CONFIRMING_ORDER` + pending order для `handle_confirmation`.
- **Событие:** `order.draft_recovery_sent` через `emit_event`.
- **Конфиг:** `DRAFT_RECOVERY_ENABLED` (default true).

### Добавлено (2026-05-20) — G5 Live Pulse (Money Core)

- **Live Pulse в чатах:** индикатор ожидания ответа гостю — 🟢 &lt;2 мин, 🟡 2–5 мин, 🔴 &gt;5 мин; красные чаты поднимаются вверх списка.
- **API:** `GET /api/admin/chats` возвращает `last_role`, `wait_seconds`, `pulse`; логика в [`bot_sla_status.py`](app/services/bot_sla_status.py).
- **Тесты:** [`tests/test_visibility_money.py`](tests/test_visibility_money.py) — пороги pulse и sidebar payload.

### Документация (2026-05-20)

- Синхронизированы [`docs/OS_TRANSITION_PLAN.md`](docs/OS_TRANSITION_PLAN.md) (Phase 1–4 → 100%, Phase 3 → 95%, Final Mile backend/UI split), [`docs/ROADMAP.md`](docs/ROADMAP.md) (Final Mile backend `[x]`, UI gaps явно в backlog), [`docs/AI_OPERATIONS.md`](docs/AI_OPERATIONS.md), [`docs/UI_MAP.md`](docs/UI_MAP.md), [`docs/REMAINING_UPDATES.md`](docs/REMAINING_UPDATES.md), [`codebase.md`](codebase.md) с текущим кодом после Location Enterprise Metrics и Final Mile backend MVP.

### Добавлено (2026-05-20) — Location Enterprise Metrics

- **Location-aware dashboard metrics:** `/stats`, `/funnel`, `/analytics`, `/activity`, `/incidents`, `/roi/today` принимают `location_id`; при location scope не используют org-wide `DailyOrgStats` как точный источник и возвращают `location_scope`.
- **Location-aware Intelligence:** `/overview`, `/digital-twin`, `/latency`, `/os-dashboard`, `/revenue-leak`, `/inventory/stock-alerts` проверяют `assigned_location_ids`; `os-dashboard` для точки использует SQL/inventory fallback.
- **Admin UI filter:** селектор точки в шапке берёт `available_locations` из `/auth/me`, сбрасывает активный чат/заказ при смене точки и прокидывает `location_id` в dashboard, AI Center, chats и orders.
- **Тесты:** добавлены проверки location metrics и UI surface (`tests/test_location_scope.py`, `tests/test_location_ui_surface.py`).

### Документация (2026-05-21)

- Синхронизированы [`docs/ROADMAP.md`](docs/ROADMAP.md), [`docs/OS_TRANSITION_PLAN.md`](docs/OS_TRANSITION_PLAN.md), [`docs/AI_OPERATIONS.md`](docs/AI_OPERATIONS.md), [`docs/UI_MAP.md`](docs/UI_MAP.md) с текущим кодом (Location 1.1, os.audit, bulk pricing, GuestCare, digest, PWA, operator-facing UI).
- Тест [`tests/test_os_phase5.py`](tests/test_os_phase5.py): бейдж автопилота — «данные ОС» / «по событиям ОС» вместо `event bus`.

### Добавлено (2026-05-19) — Ultimate Platform 2026 (Sprint A/B/C)

- **Phase 1.1 Location:** модель `Location`, `location_id` на Order/ChatLog/Booking, миграция `20260520_locations_phase11`, RBAC в `tenant_scope` + `deps.py`, тесты `tests/test_location_scope.py`.
- **Full replay:** `chat_history_slice` в `save_ai_context_snapshot`, `replay_ai_decision` с историей диалога.
- **Audit tail:** `integration.iiko.failed`, `integration.whatsapp.failed`, `ai.dialog.started` (`dialog_events.py`), backfill `dialogs_count`, WS `os.audit`.
- **OS UI:** лента решений, `dashLiveFeed`, WS handlers для business events, bulk apply pricing, GuestCare External (import + reply draft), PWA `manifest.webmanifest` + `sw.js`.
- **Autopilot B:** `POST /apply-pricing/bulk`, Self-Healing 2.0 WA nudges, `stock_alerts` на `/os-dashboard`.
- **Sprint C docs:** [`docs/VOICE_AI_SPIKE.md`](docs/VOICE_AI_SPIKE.md), [`docs/SUPPLYMIND_STAFFMIND.md`](docs/SUPPLYMIND_STAFFMIND.md).

### Добавлено (2026-05-19) — Phase 5 OS: Sprint P5-Complete (~98% Full OS Behavior)

- **Audit consumer полный охват**: все бизнес-события (кроме высокочастотных `ai.response.generated`, `conversation.state_changed`) логируются в `audit_log`. `get_audit_log()` объединяет `AuditLog` + `SystemEvent` (для legacy событий без emit_event) в единый ответ с полем `source`.
- **Auto-price changes**: `generate_autopilot_pricing_recommendation()` в [`recommendations.py`](app/services/recommendations.py) — создаётся автоматически в UTC 04:00 при tactic≠stable. `POST /api/admin/intelligence/apply-pricing/{rec_id}` — применяет изменение цен ко всем активным позициям меню (is_available=True, price>0), эмитирует `system.pricing_adjusted` с snapshot первых 5 позиций.

### Добавлено (2026-05-19) — Phase 5 OS: Sprint P5-Final (~92% Full OS Behavior)

- **Audit Consumer** [`app/services/audit_consumer.py`](app/services/audit_consumer.py): иммутабельный лог всех бизнес-событий в `audit_log` (новая таблица, миграция [`20260519_audit_log.py`](alembic/versions/20260519_audit_log.py)). Подключён в `emit_event()` после `analytics_consumer`. `GET /api/admin/intelligence/audit-log` (фильтры: action, actor, limit).
- **Self-healing actions** [`app/services/healing_actions.py`](app/services/healing_actions.py): `run_healing_actions(db, org_id)` — 4 детектора: escalation spike (≥5/день), payment failed spike (≥3/день), cancellation surge (≥25%/7д) + auto-trigger recommendations, AI message drop (−70% от предыдущей недели). Вызывается из `ai_incidents_hourly_tick` в [`worker.py`](app/worker.py).
- **Autopilot pricing** [`owner_dashboard.py`](app/services/owner_dashboard.py): `build_autopilot_pricing(stats_rows)` — 5 тактик (demand_up/down, upsell_needed, avg_check_up, stable) с конкретным `price_adj_pct`. Добавлен в `/os-dashboard` как `autopilot_pricing`.
- **`/activity` event-first**: [`analytics.py`](app/api/admin/analytics.py) — один запрос к `SystemEvent` вместо 4 запросов к Order/ChatLog/EscalationEvent/Booking. Delivery failed остался в ChatLog.
- **Legacy `emit_system_event` мигрированы**: [`dialog_mgr.py`](app/services/dialog_mgr.py) (`conversation.state_changed`) и [`pipeline_latency.py`](app/services/pipeline_latency.py) (`system.sla_violated`) переведены на `emit_event(BusinessEvent(...))`.

### Добавлено (2026-05-19) — Phase 5 OS: Sprint P5-Complete (~85-90% Full OS Behavior)

- **Predictive analytics (P4 ✅):** три алгоритма прогнозирования в [`owner_dashboard.py`](app/services/owner_dashboard.py):
  - `build_demand_forecast` — объём заказов до конца недели (linear avg)
  - `build_cancellation_forecast` — риск отмен (low/medium/high) из 28-дн. истории DailyOrgStats
  - `build_overload_risk` — риск перегрузки: текущий темп заказов vs 4-нед. исторический avg по дням недели
  Все три поля добавлены в ответ `GET /api/admin/intelligence/os-dashboard`.
- **Event tracking расширен:** новое событие `ai.response.generated` эмитируется в [`webhooks.py`](app/api/webhooks.py) после каждого AI-ответа → `DailyOrgStats.ai_messages_count` (миграция [`20260519_daily_stats_ai.py`](alembic/versions/20260519_daily_stats_ai.py)). Добавлен тип `ai.dialog.started` для `dialogs_count`.
- **`/stats` event-first (Phase 5 → ~90% event coverage):** [`analytics.py`](app/api/admin/analytics.py) теперь использует `DailyOrgStats` как основной источник для `today_orders`, `today_revenue`, `yesterday_orders`, `yesterday_revenue`, `escalations_today`, `ai_messages_today`, `daily_series` (revenue/orders). SQL остаётся только для: cumulative totals, upsell details (items_json), bot_orders, dialogs_today, iiko_errors.
- **DailyOrgStats: 2 новые колонки** `ai_messages_count`, `dialogs_count` (миграция `20260519_daily_stats_ai`). [`analytics_consumer.py`](app/services/analytics_consumer.py): `ai.response.generated` → `ai_messages_count`, `ai.dialog.started` → `dialogs_count`. + новая функция `get_event_stats_for_range(start_date, end_date)` для произвольного диапазона.
- **Backfill** [`app/services/analytics_backfill.py`](app/services/analytics_backfill.py): заполняет `daily_org_stats` за N дней из Order/ChatLog/EscalationEvent. `GREATEST(existing, backfill)` — живые данные не перетираются. `POST /api/admin/intelligence/backfill-stats`.
- **websocket_consumer**: `emit_event()` → `asyncio.create_task(publish_event(...))` — все бизнес-события попадают в Pub/Sub (real-time обновление admin UI без polling).
- **`/analytics` event-first**: revenue/orders из `DailyOrgStats` через `get_event_stats_for_range`. SQL остаётся для ai_profit (items_json), top_items, heatmap.
- **`/funnel` event-first**: `dialogs_count` и `orders_confirmed` из DailyOrgStats при наличии данных.
- **`network/stats` event-first**: `get_today_event_summary` для каждого филиала сети; SQL только для all-time cumulative total.
- **Event-driven recommendations** (новые типы): `cancellation_surge`, `revenue_dip`, `low_conversion` в [`recommendations.py`](app/services/recommendations.py) — читают только DailyOrgStats. Старые типы (product_boost, geo) остаются Order-based.

### Добавлено (2026-05-19) — Фундамент к пилоту Фазы 5

- **Tenant / RBAC — Manager + `assigned_org_ids`:** роль `manager` в [`StaffRole`](app/db/models.py); колонка `staff_users.meta_json` (миграция [`20260519_staff_meta_json.py`](alembic/versions/20260519_staff_meta_json.py)). [`tenant_scope.py`](app/services/tenant_scope.py) — `staff_assigned_org_ids`, фильтрация `available_organizations_for_admin_session` для manager/operator. `POST /staff` принимает `assigned_org_ids` для manager.
- **`location_id` на шине:** [`emit_event`](app/services/system_events.py) всегда пишет `_location_id` (явный `location_id` или `org_id` филиала).
- **Event System — прогноз и totals:** `week_forecast.source` = `event_driven` при ≥3 днях `revenue_kzt` в `DailyOrgStats` ([`owner_dashboard.py`](app/services/owner_dashboard.py), [`analytics.py`](app/api/admin/analytics.py)). `GET /intelligence/event-stats` — полные totals (`bookings_created`, payments, `revenue_kzt`). `payment.expired` на `emit_event` в [`payment_expiry.py`](app/services/payment_expiry.py).
- **Тесты:** [`tests/test_phase5_foundation.py`](tests/test_phase5_foundation.py).

### Исправлено (2026-05-18) — Sprint H: аудит Phase 5 readiness

- **H1 — DailyOrgStats: 4 новые колонки** ([`app/db/models.py`](app/db/models.py), [`analytics_consumer.py`](app/services/analytics_consumer.py)): `bookings_created`, `payments_completed`, `payments_failed`, `revenue_kzt`. Миграция [`20260518_daily_org_stats_v2.py`](alembic/versions/20260518_daily_org_stats_v2.py) + SQLite-патч. `_EVENT_COLUMN` расширен до всех 10 типов. `payment.completed` дополнительно вызывает `_upsert_daily_revenue(amount)` через новую функцию — выручка от оплат теперь в event-driven агрегатах.
- **H2 — DE `book` → `faq` при block** ([`decision_engine.py`](app/services/decision_engine.py)): `_build_corrected_response` теперь меняет `intent` на `faq` для `order` **и** `book`. До этого при блокировке брони (billing_suspended, force_closed) `_handle_booking` всё равно создавал DRAFT-бронь.
- **H3 — `tenant` в `AIReadContext`** ([`context_engine.py`](app/services/context_engine.py)): новое поле `tenant: Tenant | None = None`. `fetch_ai_read_context` загружает `Tenant` параллельно с остальными данными. [`webhooks.py`](app/api/webhooks.py) передаёт `tenant=read_ctx.tenant` в `decision_engine.validate()`. Теперь DE получает реальный `tenant.plan_status` из контекста, а не только `org.is_active` как прокси.
- **H4 — `event_driven_stats` в UI** ([`_tab_dashboard.html`](app/templates/screens/_tab_dashboard.html)): бейдж «данные ОС» при `event_driven_stats.source === 'event_driven'` (язык оператора; ранее dev-лейбл `event bus`).
- **Тесты Sprint H:** [`tests/test_sprint_h.py`](tests/test_sprint_h.py) — 295 строк, 4 класса: `TestDailyOrgStatsH1` (5 тестов: payment.completed/failed, booking.created, revenue_kzt accumulation, все 10 типов), `TestBookingBlockH2` (2 теста: billing+book→faq, force_closed+book→faq), `TestTenantInContextH3` (3 теста: поле в dataclass, optional default, DE видит tenant.plan_status), `TestEventDrivenStatsUIH4` (2 теста: template содержит event_driven_stats, индикатор payments_completed).

### Исправлено (2026-05-18) — Sprint G Staff Review (DoD)

- **G1 — Hallucination: `isdisjoint()` вместо 5-char prefix** ([`decision_engine.py`](app/services/decision_engine.py)): `_check_all_items_hallucinated` теперь использует `proposed_names.isdisjoint(menu_names)` — точное множественное пересечение O(n+m), нет ложных срабатываний от prefix heuristic. Если хотя бы одна позиция совпала → не блокируем; `validate_order` обработает неизвестные позиции.
- **G2 — WS явный разрыв при switch** ([`admin-app.js`](app/static/js/admin-app.js)): `switchNetworkOrg` перед переключением явно закрывает текущий WebSocket и сбрасывает `_wsTokenInUse = null`, исключая получение real-time событий чужого заведения в окне между сменой сессии и переподключением WS. После — `selectOrganization` получает новый `ws_token` и пересоздаёт соединение.
- **G3 — Storage: только `{iiko_id, price, is_available}`** ([`context_engine.py`](app/services/context_engine.py)): `menu_prices_snapshot` хранит 3 поля вместо 5 (убраны `name`, `category`). `iiko_id` — стабильный идентификатор; `name` и `category` исключены — `menu_context_text` (frozen string) уже содержит полную читаемую информацию для replay. Экономия >30% на типичном меню 80 позиций.
- **DoD тесты** ([`tests/test_sprint_g_dod.py`](tests/test_sprint_g_dod.py)) — 366 строк, 4 класса: `TestDoDOne` (4 теста: force-close+block, suspended+block, faq/escalate разрешены, book+block), `TestDoDTwo` (4 теста: owner видит все branches, single=empty, `userData?.is_network` в шаблоне, `_wsTokenInUse=null` в JS), `TestDoDThree` (3 теста: frozen price in replay, minimal fields, size savings >30%), `TestDoDHallucinationIsdisjoint` (3 теста: partial match, fully unknown, exact vs prefix).

### Добавлено (2026-05-18) — Sprint G: DE 95% + Franchise Phase 1 + Snapshot 80%

**G1 — Decision Engine → ~95%:**
- **`_check_billing_suspended`** ([`app/services/decision_engine.py`](app/services/decision_engine.py)) — **block** + intent→faq для `order`/`book` при трёх условиях (defense-in-depth): `billing_suspended=True` из webhooks, `tenant.plan_status=suspended`, `org.is_active=False`. FAQ и escalate всегда разрешены.
- **`_check_all_items_hallucinated`** — **block** + intent→faq если ВСЕ позиции заказа отсутствуют в меню (нечёткое сравнение по первым 5 символам). Предотвращает создание бессмысленного черновика с 100% неизвестными позициями. Если меню не загружено — проверка пропускается.
- **`_check_pricing_policy` реализован (Phase 4.2):** если `org.max_discount_pct > 0` и AI предлагает `discount_pct > max_pct` → **block**. Также заготовка для estimated_total check.
- **Billing guard в pipeline:** [`app/api/webhooks.py`](app/api/webhooks.py) — `decision_engine.validate(..., billing_suspended=...)` получает флаг из `org.is_active`.

**G2 — Franchise Phase 1 OS (Tenant → ~95%):**
- **`Tenant.is_network`** ([`app/db/models.py`](app/db/models.py)) — новое булево поле (default=False). Миграция [`20260518_tenant_is_network.py`](alembic/versions/20260518_tenant_is_network.py) + SQLite-патч.
- **`GET /api/admin/network/orgs`** — список активных филиалов сети для Branch Switcher. Только при `is_network=True`.
- **`GET /api/admin/network/stats`** — агрегированная аналитика по всей сети (SUM/COUNT, не сырые данные). Per-org breakdown за сегодня. Изолировано по tenant_id.
- **`POST /api/admin/network/switch/{org_id}`** — переключение в контекст конкретного филиала с проверкой принадлежности к тенанту.
- **`GET /api/admin/auth/me`** — обогащён полями `is_network` и `network_orgs`. Вспомогательные функции `_resolve_is_network` / `_resolve_network_orgs` в [`auth.py`](app/api/admin/auth.py).
- **Branch Switcher UI** — [`app/templates/screens/_header.html`](app/templates/screens/_header.html): кнопка «Сеть ▾» с дропдауном всех филиалов, видна только при `userData.is_network && network_orgs.length > 1`. [`admin-app.js`](app/static/js/admin-app.js): `userData.is_network`, `userData.network_orgs`, метод `switchNetworkOrg(orgId)`.

**G3 — AI Context Snapshot → ~80%:**
- **Полный снимок цен** ([`app/services/context_engine.py`](app/services/context_engine.py)) — `menu_prices_snapshot` содержит **все** позиции меню (не 40), с полями `name/price/available/category/iiko_id`. Заморозка цен в момент LLM-решения для точного replay.
- **`menu_context_text`** — строка, переданная LLM (из `build_menu_context_for_ai`), теперь сохраняется в `business_state`. Передаётся из `webhooks.py` через `save_ai_context_snapshot(..., menu_context_text=menu_context)`.
- **Replay использует frozen context** ([`app/api/admin/intelligence.py`](app/api/admin/intelligence.py)) — если `business_state.menu_context_text` есть → replay воспроизводит LLM с точно тем же контекстом меню. Иначе fallback на текущее меню из БД.
- **`GET /snapshots/{id}`** — показывает `has_menu_context_text`, `has_menu_prices_snapshot`, `menu_prices_count` без раскрытия полного текста.
- **Тесты Sprint G:** [`tests/test_sprint_g.py`](tests/test_sprint_g.py) — 445 строк, 3 класса, 20 тестов.

### Добавлено (2026-05-18) — Sprint F: Замыкание шины событий + Phase 3.2 (к Фазе 5)

- **`payment.completed` / `payment.failed` на emit_event (F1):** [`app/services/payment_webhook.py`](app/services/payment_webhook.py) — два последних ключевых события мигрированы с `emit_system_event` на `emit_event(BusinessEvent(...))`. Теперь **все деньги на шине**: `payment.completed` (id = `"payment.completed:{provider}:{payment_id}"`) и `payment.failed`. Idempotency сохранена. Event System: 10 типов из 10 ключевых теперь на `emit_event`.
- **`booking.created` на шине (F2):** [`app/services/intent_router.py`](app/services/intent_router.py) — при создании DRAFT-брони в `_handle_booking` добавлен `emit_event(BusinessEvent(type="booking.created", ...))`. Полный lifecycle брони теперь на шине: `created → confirmed → cancelled`.
- **`event_slice` в AI Context Snapshot (F3 / Phase 3.2):** [`app/services/context_engine.py`](app/services/context_engine.py) — добавлена `_load_recent_event_slice(db, org_id, minutes=15)`: запрашивает последние 20 `SystemEvent` за 15 минут, фильтрует служебные ключи `_actor`/`_version`, возвращает в хронологическом порядке. `save_ai_context_snapshot` теперь сохраняет реальный `event_slice` (был `{}`). AI Snapshot: **50% → ~70%** (порог Фазы 5 достигнут).
- **`analytics_consumer` — 10 типов:** [`app/services/analytics_consumer.py`](app/services/analytics_consumer.py) — `HANDLED_EVENT_TYPES` расширен до 10 типов: добавлены `booking.created`, `payment.completed`, `payment.failed`.
- **Тесты Sprint F:** [`tests/test_sprint_f.py`](tests/test_sprint_f.py) — 334 строки, 3 класса: `TestPaymentAndBookingEvents` (5 тестов: dotted-нотация, idempotency payment, booking.created, все 10 типов в consumer), `TestEventSlice` (6 тестов: заполнение из SystemEvent, org-isolation, фильтрация _-ключей, time-window, хронологический порядок, fallback при ошибке DB), `TestEventCoverage` (2 теста: таксономия 10 типов, отсутствие legacy underscore).

### Добавлено (2026-05-18) — Phase 2.3: Event-Driven Aggregates (Event System ~80%)

- **`DailyOrgStats` модель:** [`app/db/models.py`](app/db/models.py) — широкая таблица `daily_org_stats` с составным PK `(organization_id, day)`, 7 счётчиков событий: `orders_created/confirmed/cancelled`, `bookings_confirmed/cancelled`, `escalations`, `operator_takeovers`. Миграция [`20260518_daily_org_stats.py`](alembic/versions/20260518_daily_org_stats.py) + SQLite-патч в [`app/main.py`](app/main.py).
- **`analytics_consumer` — реальный upsert:** [`app/services/analytics_consumer.py`](app/services/analytics_consumer.py) — `on_business_event` теперь реально инкрементирует строку `DailyOrgStats` через `ON CONFLICT DO UPDATE`. Атомично: если транзакция `emit_event` откатится — агрегат тоже не сохранится. Добавлены `get_event_stats(db, org_id, days)` и `get_today_event_summary(db, org_id)`.
- **`GET /api/admin/intelligence/event-stats`:** [`app/api/admin/intelligence.py`](app/api/admin/intelligence.py) — новый endpoint читает из `DailyOrgStats` (не из Order/ChatLog), возвращает `daily[]` + `totals` + `conversion_pct`. Org-scoped. Явная пометка `source: "event_driven"`.
- **`/api/admin/stats` → `event_driven_stats`:** [`app/api/admin/analytics.py`](app/api/admin/analytics.py) — ответ обогащён полем `event_driven_stats` (сводка за сегодня из `DailyOrgStats`). При ошибке чтения — `null` (не рвёт endpoint). Позволяет сравнивать SQL-источник и event-driven рядом.
- **Тесты Phase 2.3:** [`tests/test_os_sprints.py`](tests/test_os_sprints.py) расширен до **803 строк**, добавлен класс `TestDailyOrgStats` — 7 тестов: upsert увеличивает колонку, двойной upsert = 2, маппинг event→column, игнор unknown event, emit_event → consumer → DailyOrgStats (end-to-end), summary с нулями, org-isolation.

### Добавлено (2026-05-18) — Sprint E: Event System + Decision Engine (к Фазе 5)

- **Event System 30% → 70% (E1):** [`app/services/intent_router.py`](app/services/intent_router.py) — 5 ключевых бизнес-событий мигрированы с `emit_system_event()` на `emit_event(BusinessEvent(...))`: `order.created`, `order.confirmed`, `order.cancelled`, `booking.confirmed`, `booking.cancelled`. Детерминированные id (`"order.created:123"`) сохраняют idempotency. Dotted-нотация типов (`order.created` вместо `order_created`). `analytics_consumer.py` расширен под все 7 типов.
- **Decision Engine 40% → 85% (E2):** [`app/services/decision_engine.py`](app/services/decision_engine.py) — три новых правила: `_check_empty_order` (**block** + intent→faq: LLM сгенерировал «заказ принят» без позиций — пустой черновик не создаётся), `_check_delivery_no_address` (warn: доставка без адреса), `_check_max_order_items` (warn: > 20 позиций — аномалия парсинга). `MAX_ORDER_ITEMS = 20` как классовый атрибут.
- **Тесты (E3):** [`tests/test_os_sprints.py`](tests/test_os_sprints.py) расширен до 26 тестов: `TestDecisionEngineNewRules` (8 тестов: empty_order block, intent→faq, order_actions не пустой, delivery warn, max_items warn), `TestEventSystemMigration` (4 теста: dotted-нотация, детерминированный idempotency key, сохранение в БД с правильным полями).

### Исправлено (2026-05-18) — OS Sprint post-audit fixes

- **DE критический фикс:** [`app/services/decision_engine.py`](app/services/decision_engine.py) — `_build_corrected_response` теперь при `severity=block` и `intent=order` меняет `intent` на `"faq"`. До этого `route_intent` продолжал создавать черновик заказа даже при force-closed блокировке, т.к. `intent=order` сохранялся.
- **chats.py атомарность takeover:** [`app/api/admin/chats.py`](app/api/admin/chats.py) — `emit_event("operator.took_over")` перемещён внутрь `try/except` вместе с `_save_chat_triage`. Теперь если пользователь не найден (404 в `_user_for_chat`), оба изменения (событие + triage) откатываются вместе — нет висящих событий без triage update.
- **Тесты OS Sprints:** [`tests/test_os_sprints.py`](tests/test_os_sprints.py) — 14 тестов для трёх спринтов: `TestDecisionEngine` (8 тестов: force_closed блокирует order, intent меняется на faq, FAQ не блокируется, expired не блокирует, stoplist=warn, org=None, naive datetime); `TestBusinessEvent` (4 теста: UUID генерация, idempotency, actor→source); `TestAIContextSnapshot` (3 теста: org-scope, isolation, сохранение).

### Добавлено (2026-05-18) — Sprint D: Decision Engine (Phase 4 OS)

- **`decision_engine.py`:** [`app/services/decision_engine.py`](app/services/decision_engine.py) — `PolicyViolation` (rule, severity block/warn, detail), `ValidationResult` (is_valid, violations, corrected_response), `DecisionEngine` класс с тремя проверками: `_check_force_closed` (блокирует заказы при активном экстренном закрытии), `_check_stoplist_quick` (предупреждение по стоп-позициям без повторного DB-запроса, используя `read_ctx.menu_items`), `_check_pricing_policy` (заглушка Phase 4.1 для будущего поля discount в AIBrainResponse). Singleton `decision_engine` для использования в webhooks.py.
- **Decision Engine в pipeline:** [`app/api/webhooks.py`](app/api/webhooks.py) — после LLM-вызова (до `route_intent`) вызывается `decision_engine.validate(ai_response, read_ctx, read_ctx.org)`. При blocking-нарушении `ai_response` заменяется на `corrected_response` с объяснением. Ошибка DE логируется, pipeline продолжается с оригинальным ответом.
- **`max_discount_pct` в Organization:** [`app/db/models.py`](app/db/models.py) — новое поле `max_discount_pct: int = 0` (Policy Engine: максимальный % скидки, 0 = запрещено). Миграция [`20260518_org_max_discount.py`](alembic/versions/20260518_org_max_discount.py) + SQLite-патч в [`app/main.py`](app/main.py).

### Добавлено (2026-05-18) — Sprint C: AI Context Snapshot (Phase 3 OS)

- **`AIContextSnapshot` модель:** [`app/db/models.py`](app/db/models.py) — новая таблица `ai_context_snapshots` (UUID PK, `org_id`, `phone`, `business_state` JSON, `customer_state` JSON, `event_slice` JSON). Миграция [`20260518_ai_context_snapshots.py`](alembic/versions/20260518_ai_context_snapshots.py) + SQLite-патч в [`app/main.py`](app/main.py). Индексы по `(org_id, created_at)` и `(org_id, phone)`.
- **`save_ai_context_snapshot()`:** [`app/services/context_engine.py`](app/services/context_engine.py) — сохраняет снимок `AIReadContext` перед LLM-вызовом: состояние меню (count, stoplist_count, preview 40 позиций), состояние клиента (draft, history snippet, preferences). Открывает собственную сессию, коммитит. Ошибки логирует без пробрасывания — snapshot не в critical path.
- **Snapshot в pipeline:** [`app/api/webhooks.py`](app/api/webhooks.py) — перед каждым LLM-вызовом (`call_openai` / `call_ai_with_audio`) вызывается `save_ai_context_snapshot`. Полученный `snapshot_id` (UUID) сохраняется в `ChatLog.meta_json["snapshot_id"]` через `assistant_meta`. Ошибка save не прерывает pipeline.
- **Replay API:** [`app/api/admin/intelligence.py`](app/api/admin/intelligence.py) — три новых endpoint'а: `GET /admin/intelligence/snapshots` (список последних снимков org), `GET /admin/intelligence/snapshots/{id}` (полный снимок), `POST /admin/intelligence/snapshots/{id}/replay?user_text=...` (воспроизвести решение AI с тем же контекстом без отправки клиенту). Изолировано по `organization_id`.

### Добавлено (2026-05-18) — Sprint B: Event System Stabilization (Phase 2 OS)

- **`BusinessEvent` dataclass + `emit_event()`:** [`app/services/system_events.py`](app/services/system_events.py) — добавлен унифицированный `BusinessEvent` (поля: `org_id`, `type`, `actor`, `payload`, `id`, `timestamp`, `location_id`, `entity_type`, `entity_id`, `version`) и `emit_event(db, event)` как единственный способ записи новых бизнес-событий через OS Event Layer. Маппинг на существующую модель `SystemEvent` без новых миграций: `actor` → `source`, `location_id`/`version` → `payload_json`. Существующие вызовы `emit_system_event()` не тронуты (обратная совместимость).
- **`ai.escalated` событие:** [`app/api/webhooks.py`](app/api/webhooks.py) — при переходе в `HUMAN_MODE` по эскалации ИИ теперь дополнительно к `EscalationEvent` записывается `BusinessEvent(type="ai.escalated")` через `emit_event()`. Атомарно с коммитом `EscalationEvent`.
- **`operator.took_over` событие:** [`app/api/admin/chats.py`](app/api/admin/chats.py) — при явном перехвате диалога оператором (`POST /api/admin/chats/{phone}/takeover`) записывается `BusinessEvent(type="operator.took_over")`, коммитится вместе с triage update.
- **`analytics_consumer.py`:** [`app/services/analytics_consumer.py`](app/services/analytics_consumer.py) — новый consumer, подключённый к `emit_event()`. Текущая реализация — logging stub + TODO-заглушки для будущих агрегатов (`Phase 2.3`); не нагружает critical path. Consumer вызывается синхронно внутри транзакции и не перехватывает исключения — ошибки логируются, но не блокируют запись события.

### Стратегия (2026-05-18)

- **RestoMind OS:** репозиторий официально переходит на концепцию AI Operating System. Позиционирование изменено с «AI-оператор для ресторана» на «AI-операционная система для ресторанного бизнеса». Обновлены [`README.md`](README.md) (разделы «Архитектура ядра» и «Модули»), [`codebase.md`](codebase.md) (суть проекта), [`docs/CONVENTIONS.md`](docs/CONVENTIONS.md) (Rules 9–11: Tenant Isolation, Event-First, AI Context через ContextBuilder).
- **Утверждён `OS_TRANSITION_PLAN` (5 фаз):** [`docs/OS_TRANSITION_PLAN.md`](docs/OS_TRANSITION_PLAN.md) — честная оценка текущего состояния по каждой фазе, конкретные схемы реализации, антипаттерны.
- **Запланировано — Franchise / Branch (Phase 1):** иерархия `Tenant → Organization`, флаг `Tenant.is_network`, Branch Switcher, агрегированная аналитика «Вся сеть», матрица ролей Owner/Manager/Operator. Задача в ROADMAP P1: [`docs/ROADMAP.md`](docs/ROADMAP.md).
- **Запланировано — Event System Stabilization (Phase 2) и AI Context Snapshot (Phase 3):** задачи добавлены в ROADMAP P3.

### Добавлено (2026-05-18)

- **Dialog / `is_cancel_all_message` расширение:** фраза «Отмени эти все заявки» и аналогичные натуральные формулировки (произвольный порядок слов) теперь корректно детектируются до LLM. Добавлены `_CANCEL_VERBS` + `_ALL_MARKERS` keyword-combo проверка и новые фразы в `CANCEL_ALL_PHRASES` в [`app/services/dialog_mgr.py`](app/services/dialog_mgr.py); тесты расширены в [`tests/test_dialog_session_fixes.py`](tests/test_dialog_session_fixes.py) — 16 кейсов (11 позитивных + 5 негативных, включая защиту от «отмени плов»).
- **Owner Dashboard Spec:** [`docs/OWNER_DASHBOARD_SPEC.md`](docs/OWNER_DASHBOARD_SPEC.md) — полная спецификация для реализации 4 ответов Owner Dashboard: прогноз выручки до конца недели (`_linear_week_forecast` + карточка), метрики эффективности бота на главном экране (`bot_handled_pct`, `escalations_today`), воронка потерь `GET /api/admin/funnel` (диалогов → черновиков → заказов, отток за 30 дней), рекомендации с ROI-ранжированием `top_actions` в `/api/admin/stats`.
- **OS Transition Plan:** [`docs/OS_TRANSITION_PLAN.md`](docs/OS_TRANSITION_PLAN.md) — стратегический план перехода RestoMind → AI OS по 5 фазам с честной оценкой текущего состояния (Phase 1 ~90%, Phase 2 ~40%, Phase 3 ~70% и т.д.), Strangler Pattern как основной принцип, приоритет Resource-Scope RBAC как ближайшего блокера enterprise-продаж.

### Исправлено (2026-05-18)

- **Админка / «Требует внимания»:** проверка WhatsApp в инцидентах и `/integrations/status` совпадает с онбордингом — учитывается `phone_number_id` филиала в БД при заданном `WHATSAPP_API_TOKEN`; нет ложного «WhatsApp не настроен».
- **Админка / дашборд владельца (Q1–Q4):** прогноз выручки до конца недели (`week_forecast` в `/stats`, модель по дням недели + fallback), блок эффективности бота (`bot_handled_pct`, `escalation_rate_pct`), воронка потерь `GET /funnel` (drop-off, отток, отмены, негативные отзывы), топ-3 рекомендации с ROI и переходом по клику (`top_actions` + `openTopAction`).
- **WhatsApp / стоп-лист:** кэш промпта меню (Redis + in-process) учитывает отпечаток `is_available`; фоновый sync стоп-листов из `main.py` инвалидирует кэш — нет противоречий «в наличии» / «на стопе» между сообщениями.
- **WhatsApp / смена стопа в диалоге:** `stoplist_session` запоминает стоп-позиции по `(org, phone)`; при новом стопе — блок в промпт для LLM и ответы сервера («только что ушло на стоп», «убрал из заказа»), а не сухое «недоступно».
- **WhatsApp / корзина:** канонический телефон E.164 на входе; сброс DRAFT при пустой Redis-истории (TTL 24 ч); `current_pending_order_id` сбрасывается в БД вместе с Redis.
- **WhatsApp / «отмени всё»:** детерминированная отмена всех черновиков до LLM (`is_cancel_all_message`), без ложного «отменил» от модели.
- **Промпт / escalate:** короткие отрицания в заказе («не плов») → `order`, не `escalate`; FAQ на «есть X?» без автодобавления в корзину.
- **HUMAN_MODE:** `clear_pending_order` не сбрасывает эскалацию в CHATTING; сверка с `User.current_state` в БД; шаблон «менеджер ответит» вместо повторных ответов LLM.
- **Админка / чаты:** при эскалации из WhatsApp — WebSocket `state_changed` (`human_mode`) и обновление `onHumanNeeded`; в ленте — подпись вместо `[OPERATOR_ONLY]`, бейдж «Сбой ИИ» при `meta.technical_fallback`.
- **Документация:** обновлены `docs/STATE_MACHINE.md`, `docs/EVENT_ARCHITECTURE.md`, `docs/UI_MAP.md`, `codebase.md`, `README.md` (WS/FSM/чаты).
- **Админка / UX чатов:** компактная шапка (один статус `chatModeSummary`, кнопка «Ответить самому» / «Вернуть боту», остальное в «⋯»); больше места под ленту; без тех. «интентов» в пузырях ИИ.
- **Админка / бронирования:** недельный мини-календарь, KPI всегда видны, список по выбранному дню; боковая справка (залы, режим, как бронируют); `GET /bookings?date_from&date_to`; убран дубль заголовка вкладки.

### Исправлено (2026-05-12)

- **Маркетинг / DELETE рассылки:** удаление `MarketingBlast` через ORM вызывало `UPDATE marketing_blast_recipients SET blast_id=NULL` и падало по NOT NULL. Теперь оба шага — только Core `DELETE` (сначала получатели, затем кампания) с фильтром по `organization_id`.
- **Интеграции / ручной sync:** `POST /api/admin/integrations/sync` ставит в очередь `BackgroundTasks` полную синхронизацию меню + стоп-листов (`run_full_iiko_sync_for_org`), ответ сразу возвращает текущий `status` и `mode: background` без долгого HTTP.
- **Админка / баннер внимания:** подсказка рядом со счётчиком берёт первую причину из группы `integrations_degraded`, а не из первой группы списка.

### Добавлено (2026-05-15) — E11/E12

- **E11 Strategy Engine расширение:** новые `trigger_mode` в `UpsellRule` (без миграции): `time_of_day` — срабатывает в указанном диапазоне часов `"HH-HH"` с учётом org timezone; `item_present` — срабатывает если категория уже есть в корзине (обратное `missing_category`). Реализовано в [`strategy_engine.py`](app/services/strategy_engine.py).
- **E11 Антишум per-session:** новое правило [`rule_session_rejection_cap`](app/services/sales_strategy_engine.py) — если клиент 2+ раза проигнорировал предложенные позиции в текущем заказе (есть в `recommendation_trace`, но нет в корзине), upsell прекращается и бот переключается на завершение заказа.
- **E11 Персонализация upsell:** новый модуль [`app/services/personalization.py`](app/services/personalization.py) — `get_user_preferences(db, user_id, org_id)` анализирует последние 20 подтверждённых заказов, возвращает `never_categories` (частота < 5%), `avg_total`, `drinks_frequency`. Загружается параллельно с остальным контекстом в `fetch_ai_read_context` (нулевая задержка). `build_sales_strategy` принимает `user_preferences` и исключает из кандидатов категории/напитки, которые клиент никогда не берёт. Для новых клиентов (нет истории) персонализация не применяется.
- **E12 Smart Category Filter:** [`order_logic.py`](app/services/order_logic.py) — `detect_category_hint(message, menu_items)` определяет категорию по сообщению гостя (string-match, без LLM). `build_menu_context_filtered()` — полный контекст для найденной категории + drinks/upsell/desserts, компактный (name + price) для остального. Включается при `MENU_SMART_FILTER_ENABLED=true` и меню ≥ `MENU_SMART_FILTER_MIN_ITEMS` позиций (default 60). Кэш расширен: ключ `(org_id, category_hint)`. Заменяет неактивный RAG-путь без embeddings и pgvector.

### Добавлено (2026-05-15) — текущая сессия

- **Message accounting layer:** новая модель [`MessageAccountingLog`](app/db/models.py) — агрегированный учёт входящих/исходящих WhatsApp-сообщений по `(org, day, direction, source, type)`, upsert-паттерн как у `AiUsageLog`. Сервис [`app/services/message_accounting.py`](app/services/message_accounting.py) — `schedule_log_message` (fire-and-forget, не блокирует pipeline). Хуки: inbound text/voice/interactive в `receive_message` (webhooks.py), outbound AI в `process_message`, outbound operator в `admin_send_message` (chats.py), outbound blast в `run_send_blast_batch` (marketing.py). Миграция [`20260515_message_accounting`](alembic/versions/20260515_message_accounting.py) + SQLite-патч в `main.py`. Эндпоинт `GET /api/superadmin/message-accounting?days=1|7|30` (superadmin.py). Секция «Сообщения WhatsApp» в [`superadmin.html`](app/templates/superadmin.html) с переключателем периода — видна только суперадмину.
- **Суперадмин / Токены ИИ:** блок «Токены сегодня» перенесён из вкладки «Тест бота» обычной админки в суперадминку (`GET /api/superadmin/ai-usage`). В обычной админке счётчик удалён — он не нужен оператору ресторана.
- **Стоп-лист / видимость для ИИ:** [`context_engine.py`](app/services/context_engine.py) теперь загружает **все** позиции меню (включая `is_available=False`) через `load_available_menu(include_unavailable=True)`. [`build_menu_context`](app/services/order_logic.py) помечает стоп-позиции тегом `[СТОП — временно недоступно, нельзя добавить в заказ]`. `ValidatedOrder` расширен полем `stoplist_items: list[str]`. [`validate_order`](app/services/order_logic.py) при совпадении с недоступной позицией добавляет её в `stoplist_items` (не в `valid_items`). [`intent_router`](app/services/intent_router.py) возвращает понятное сообщение «🚫 «X» — сейчас временно недоступно» вместо «не нашёл в меню» и не эскалирует. Тест [`test_context_engine.py`](tests/test_context_engine.py) обновлён под новое поведение.

### Улучшено (2026-05-15) — текущая сессия

- **Производительность / Bot pipeline:** [`prompting.py`](app/services/ai_engine/prompting.py) — порядок секций system prompt изменён на `base → KB → menu → customer → time → draft → strategy`: статичный префикс (base+KB+menu ≥1024 токенов) автоматически кэшируется OpenAI между запросами разных пользователей одного ресторана. [`openai_p.py`](app/services/ai_engine/openai_p.py) — логирует `cached_tokens` из `prompt_tokens_details` для мониторинга cache hit rate.
- **Производительность / Finalize non-blocking:** [`customer_reply.py`](app/services/customer_reply.py) — `finalize_outbound_delivery` запускается через `asyncio.ensure_future` после `send_message` и не блокирует pipeline бота (−20–50 мс на каждый ответ).
- **Производительность / Убран дублирующий get_or_create_user:** [`webhooks.py`](app/api/webhooks.py) — `_save_chat_log` принимает `known_user_id: int | None`; в главном LLM-пути передаётся `u_row.id` из preflight-контекста, SELECT к БД за пользователем не повторяется.
- **Производительность / Параллельная загрузка админки:** [`admin-app.js`](app/static/js/admin-app.js) — все 5 init-запросов при входе (`refreshDemoStatus`, `loadOrgProfile`, `loadTabData`, `loadIntegrationStatus`, `loadChatList`) запускаются в `Promise.all` вместо последовательной цепочки `await` (~3–4x ускорение). В `loadIntegrationStatus` три внутренних запроса также параллельны.
- **Производительность / Диалоги:** в `selectChat` все 5 запросов (`/state`, `/chats/{phone}`, `/customers/summary`, `/orders`, `/bookings`) запускаются параллельно в одном `Promise.all` (было 4 последовательных раунда). LRU-кэш сообщений в памяти (15 чатов, TTL 5 мин) — мгновенное переключение между недавними диалогами. WS-событие `new_message` обновляет кэш для всех чатов, не только активного. Prefetch топ-3 чатов через 600 мс после загрузки списка.
- **WebSocket / reconnect fix:** [`admin-app.js`](app/static/js/admin-app.js) — при получении кода `4003 (Unauthorized)` вызывался несуществующий `checkAuth()`, что бросало `TypeError` и оставляло WS зависшим навсегда. Исправлено на `checkSession()`. Убран дублирующий `wsEpoch++` в `scheduleReconnect`.
- **UI / Без IT-терминов:** в шаблонах удалены/заменены `webhook`, `payload`, `callback URL`, `verify token`, `UUID`, `JSON`, `Токены сегодня`, `API`, `id` и другие технические термины из видимых оператору текстов (20 мест в 8 файлах). Исключение — раздел технических интеграций (iiko, WhatsApp), где термины неизбежны.

### Добавлено (2026-05-15)

- **AI Operations / Decision Intelligence (Phase 2):** `OperationalInsight` расширен полями `was_useful` (bool, оценка оператора) и `notes` (str, заметка) — миграция `20260515_insight_feedback`. `PATCH /insights/{id}` принимает `was_useful` и `notes`. `_insight_public` возвращает новые поля + `cause_hypotheses`, `recommended_actions`, `weekday_baseline` из payload.
- **Temporal baselines:** `generate_revenue_order_insights` теперь сравнивает не только с duration-match периодом, но и с тем же днём предыдущей недели (weekday baseline). Результат сохраняется в `payload_json["weekday_baseline"]` с `comparison_label` (например «vs прошлый Monday»).
- **Causal attribution (v1):** при генерации `revenue_drop`/`orders_drop` инсайтов функция `_build_cause_hypotheses` проверяет коррелирующие сигналы (high_cancellation_rate, kitchen_overload, stoplist_growth, ai_escalation_spike) → `payload_json["cause_hypotheses"]`.
- **Linked actions:** функция `_build_recommended_actions` добавляет конкретные шаги в `payload_json["recommended_actions"]` (стоп-лист, отмены, потерянная выручка).
- **Документация:** [`docs/AI_OPERATIONS.md`](docs/AI_OPERATIONS.md) полностью переписан: актуальный API, полная структура payload_json, разграничение Intelligence vs Analytics, temporal baseline model, causal attribution, feedback loop, roadmap Phase 3.

### Добавлено (2026-05-14)

- **CI / Smart test runner:** [`scripts/smart_test.py`](scripts/smart_test.py) — автоматически определяет какие файлы изменились и запускает только связанные тесты (UI → 6 тестов, payments → 8, admin API → 17, infra/db → полный прогон). `.github/workflows/ci.yml` обновлён: на PR запускается `smart_test.py --base origin/main`, на push в `main` — полный прогон. Локально: `python scripts/smart_test.py [--dry|--all|--base <ref>]`.

### Исправлено (2026-05-14)

- **CI / `ClearMenuBody` NameError:** при E0.1-расколе монолита из `_monolith.py` был удалён импорт `ClearMenuBody`, но endpoint `POST /settings/clear-menu-and-stop-snapshot` остался в монолите и использовал его — `NameError` при коллекции тестов. Исправлено добавлением `from .menu_schemas import ClearMenuBody` в `_monolith.py`.
- **E0.1 / дублирующиеся маршруты:** `_monolith.py` содержал оригинальные роуты параллельно с include_router — FastAPI обслуживал monolith-версии (первая регистрация). Удалено ~4 700 строк дублей; монолит сжат до 1 199 строк. Split-роутеры зарегистрированы в `main.py` напрямую на уровне `/api`. [`app/api/admin/__init__.py`](app/api/admin/__init__.py) переключён на импорты из split-модулей (`admin_incidents`, `analytics`, `retry_failed_task`, `admin_ai_value`).
- **Night preorder / booking orders:** добавлена явная проверка `booking_row is None` в `intent_router._handle_order()` — заказы с бронированием в зал больше не помечались `kind='night_preorder'` когда ресторан закрыт (гость едет к конкретному времени брони, а не «прямо сейчас»).
- **UI / `.ds-segmented` высота:** контейнер `py-0.5` (4px padding) + кнопки `min-h-[44px]` давали 48px вместо целевых 44px. Исправлено: `min-h-[40px]` для кнопок → 2 + 40 + 2 = 44px в сумме.
- **UI / scrollbar gap на Windows:** sticky-шапки вкладок (меню, заказы) использовали паттерн `-mx-*` чтобы выйти за padding контейнера. На Windows native scrollbar (14-17px) создавал белую полосу у правого края. Исправлено одной строкой CSS: `scrollbar-gutter: stable` для `#admin-content-scroll` — браузер всегда резервирует место под скроллбар.

### Добавлено (2026-05-12)

- **Диалог / observability:** модуль [`app/services/conversation_state.py`](app/services/conversation_state.py), [`app/services/trace_context.py`](app/services/trace_context.py); доработки [`dialog_mgr.py`](app/services/dialog_mgr.py), [`intent_router.py`](app/services/intent_router.py), [`webhooks.py`](app/api/webhooks.py); админ-эндпоинты в [`chats.py`](app/api/admin/chats.py), [`customers.py`](app/api/admin/customers.py), [`orders.py`](app/api/admin/orders.py); документы [`docs/CONTROL_PLANE.md`](docs/CONTROL_PLANE.md), [`docs/STATE_MACHINE.md`](docs/STATE_MACHINE.md); тесты [`test_conversation_state.py`](tests/test_conversation_state.py), [`test_dialog_state_events.py`](tests/test_dialog_state_events.py); правки [`test_admin_readiness.py`](tests/test_admin_readiness.py), [`test_booking_preorder.py`](tests/test_booking_preorder.py).

### Изменено (2026-05-12)

- **E0.1 / админ-API:** продолжен раскол [`app/api/admin/_monolith.py`](app/api/admin/_monolith.py): маршруты заказов и failed tasks вынесены в [`orders.py`](app/api/admin/orders.py), меню/поиск/интеграции — в [`menu.py`](app/api/admin/menu.py), организация/staff/payment config — в [`organization.py`](app/api/admin/organization.py), правила upsell/packaging — в [`rules.py`](app/api/admin/rules.py), аналитика/readiness/incidents/exports — в [`analytics.py`](app/api/admin/analytics.py). Роутеры подключены через `router.include_router(...)`; дубли маршрутов `/api/admin/*` не обнаружены, публичные реэкспорты `app.api.admin` сохранены.
- **Quality / lint:** полный `ruff check app tests` очищен от текущих `F401/F841` в новых P2/E0.1 файлах без изменения контрактов API.

### Исправлено (2026-05-12)

- **Night preorders / hall preorder:** явный предзаказ в зал (`order_type="hall"`, `is_preorder=true`) больше не превращается в `night_preorder`, если ресторан сейчас закрыт; ночной сценарий применяется только к немедленным заказам вне рабочего времени.

### Добавлено (2026-05-14)

- **P2-B / Ночные предзаказы:** `Order.kind` (`regular` | `night_preorder`, миграция `20260514_night_preorders`); в `_handle_order()` при `is_business_open=False` заказ создаётся как `night_preorder` и сразу возвращается с поясняющим ответом без запроса оплаты. ARQ cron `morning_preorders_tick` (каждые 5 мин) проверяет открытие ресторана, отправляет сводку в Telegram с кнопкой «🟢 Я на смене»; оператор активирует заказы → клиенты получают WA с кнопками ✅/❌. Алерт суперадмину если никто не нажал в течение `SHIFT_ALERT_TIMEOUT_MIN`. Сервисы: [`app/services/night_preorders.py`](app/services/night_preorders.py). Redis dedup ключи `rm:shift:sent:{org_id}:{date}`, `rm:shift:pending:{org_id}:{date}`. _Wishlist Темира #20._

- **P2-B / Авто-сбор отзывов:** `CustomerFeedback` (модель + миграция `20260514_customer_feedback`); `Organization.review_url_2gis` (поле + настройки UI). ARQ-задача `send_review_request` (deferred на `REVIEW_REQUEST_DELAY_SEC`, по умолчанию 30 мин) отправляет WA-сообщение с кнопками `review_pos`/`review_neg`. В `webhooks.py`: кнопки перехватываются до LLM → `save_customer_feedback()` + 👍 отправляет ссылку на 2GIS, 👎 → Telegram-алерт персоналу. Триггер после `SENT_TO_IIKO` в `_monolith.py`. Конфиг: `REVIEW_REQUESTS_ENABLED`, `REVIEW_REQUEST_DELAY_SEC`. _Wishlist Темира R3._

- **P2-B / Маркетинговые рассылки:** `MarketingBlast` + `MarketingBlastRecipient` (модели + миграция `20260514_marketing_loyalty`); `User.marketing_opt_out`. Сегменты: `inactive_30d`, `frequent`, `all_active`. ARQ-задача `send_blast_batch` с rate-limit `MARKETING_BLAST_RATE_PER_MIN`. API: [`app/api/admin/marketing.py`](app/api/admin/marketing.py) — `GET/POST /admin/marketing/blasts`, `POST .../send`, `DELETE`, `GET .../segment-preview/{type}`. Вкладка «Маркетинг» в сайдбаре управления с формой создания и списком рассылок.

- **P2-B / Бонусная система:** `LoyaltyBalance` + `LoyaltyTransaction` (модели + та же миграция). Сервис [`app/services/loyalty.py`](app/services/loyalty.py) — `get_balance`, `add_points`, `earn_points_for_order`, `get_loyalty_context_line`. Баланс инжектируется в system prompt через `context_engine.py` при `LOYALTY_ENABLED=true`. API: `GET /admin/loyalty/transactions`, `POST /admin/loyalty/adjust`. UI в подвкладке «Лояльность» вкладки «Маркетинг».

### Исправлено (2026-05-11)

- **Баг / intent_router:** `UnboundLocalError: cannot access local variable 'payment_url'` в `app/services/intent_router.py` — переменная объявлялась только внутри ветки `elif requires_big_order_prepay:` (с аннотацией типа), но использовалась снаружи после цепочки `if/elif`. Python интерпретировал аннотированное присвоение как объявление локальной переменной для всей функции, поэтому при пропуске ветки возникал `UnboundLocalError`. Исправление: `payment_url: str | None = None` перемещено до начала цепочки. Регресс: `tests/test_booking_preorder.py::test_confirm_order_confirms_linked_booking`.

### Добавлено (2026-05-11)

- **P4 / Latency baselines + SLA monitor:** новая модель [`PipelineLatencyLog`](app/db/models.py) (миграция [`20260513_pipeline_latency`](alembic/versions/20260513_pipeline_latency_log.py)); [`app/services/pipeline_latency.py`](app/services/pipeline_latency.py) — `schedule_log_pipeline_latency` (fire-and-forget из `webhooks.py`), `get_latency_summary` (p50/p95/max per stage, Python-side percentile), `check_sla_thresholds` (emit `SystemEvent("sla_violation")`); `GET /api/admin/intelligence/latency` (period_hours, stages, sla_violations).
- **P4 / Operator efficiency analytics:** [`app/services/operator_efficiency.py`](app/services/operator_efficiency.py) — `get_operator_efficiency`: `escalation_count`, `escalation_rate_pct`, `avg_first_response_min`, `human_mode_sessions`, `orders_confirmed_after_escalation`, `operator_recovery_rate_pct`; `GET /api/admin/intelligence/operator-efficiency`.
- **P4 / AI incident detection:** `detect_ai_incidents(db, org_id)` в [`app/services/intelligence.py`](app/services/intelligence.py) — token spike (сегодня >3× rolling 7d avg), error spike (`error_count/call_count > 15%` за 2 ч), latency spike (`p95_llm_ms > SLA * 1.5`); upsert `OperationalInsight(insight_type="ai_token_spike"|"ai_error_spike"|"ai_latency_spike")`; вызов встроен в `list_insights()` (lazy, при каждом запросе). `AiUsageLog` расширен колонками `error_count` и `p95_latency_ms` (миграция [`20260513_ai_usage_errors`](alembic/versions/20260513_ai_usage_log_errors.py)). `schedule_log_ai_error` вызывается из `webhooks.py` при `TransientAiError`.
- **P4 / AI business recommendations:** новая модель [`BusinessRecommendation`](app/db/models.py) (миграция [`20260513_biz_recommendations`](alembic/versions/20260513_business_recommendations.py)); [`app/services/recommendations.py`](app/services/recommendations.py) — `generate_recommendations` генерирует `product_boost` / `pricing_adj` / `geo_expansion` / `stoplist_impact` детерминированно (без LLM); `recommendations_daily_loop` — фоновая задача UTC 04:00; `GET /api/admin/intelligence/recommendations`, `POST /api/admin/intelligence/recommendations/refresh`, `PATCH /api/admin/intelligence/recommendations/{id}`.
- **P4 / Multi-tenant security audit:** [`tests/test_multitenant_isolation.py`](tests/test_multitenant_isolation.py) — 9 тестов: создают 2 независимые организации, проверяют изоляцию данных по `organization_id` для Order, ChatLog, MenuItem, EscalationEvent, OperationalInsight, AiUsageLog, PipelineLatencyLog, BusinessRecommendation и cross-org сценарий по номеру телефона. Отчёт: [`docs/SECURITY_AUDIT.md`](docs/SECURITY_AUDIT.md).
- **E8 / WhatsApp интерактив:** [`app/integrations/whatsapp.py`](app/integrations/whatsapp.py) — `send_interactive_buttons` (`interactive/button`, до 3 кнопок) и `send_cta_url_button` (`interactive/cta_url`). `RouteResult.interactive_buttons` и `RouteResult.cta_url` управляют выбором транспорта в `process_message`. `receive_message` раскрывает `interactive.button_reply` в `"да"` / `"нет"`.
- **E14 / Ссылка на оплату:** [`app/services/payment_providers/cloudpayments.py`](app/services/payment_providers/cloudpayments.py) — `CloudPaymentsInitiator.create_payment()` через `/payments/link/create` (Basic auth). `intent_router` при `requires_big_order_prepay` вызывает `initiate_payment` и задаёт `RouteResult.cta_url`; текст заказа не содержит URL — ссылка уезжает в отдельной CTA-кнопке.
- **Telegram оператор‑бот:** [`app/api/telegram_webhook.py`](app/api/telegram_webhook.py) — `POST /api/telegram/webhook` с верификацией `X-Telegram-Bot-Api-Secret-Token`; [`app/services/telegram_operator.py`](app/services/telegram_operator.py) — `handle_callback_query` (Redis state `tg:op:{uid}`, TTL 30 мин), `handle_operator_message` (relay → `send_customer_text` + `ChatLog`), `/dialogs` (последние 10 эскалаций). Эскалационный алерт теперь включает inline-кнопку «📩 Ответить клиенту». _Wishlist Темира #12._

### Изменено (2026-05-11)

- **E0.1 / админ-API:** все маршруты диалогов (`GET /api/admin/chats`, `GET /api/admin/chats/{phone}`, `POST …/takeover|release|ai-snooze|assign-me|snooze|close|reopen|send_message`, `POST …/messages/{id}/resend`, `GET …/state`) вынесены из [`app/api/admin/_monolith.py`](app/api/admin/_monolith.py) в [`app/api/admin/chats.py`](app/api/admin/chats.py) (`router.include_router(chats_router)`). Общий helper аудита [`admin_actor_key`](app/api/admin/deps.py) вынесен в [`deps.py`](app/api/admin/deps.py) (используется также заказами/upsell feedback). Публичный реэкспорт: [`resend_failed_chat_message`](app/api/admin/__init__.py) из `chats`. Регресс: `pytest -q` (в т.ч. [`tests/test_admin_operator_outbound.py`](tests/test_admin_operator_outbound.py), [`tests/test_admin_multitenant_ws_resend.py`](tests/test_admin_multitenant_ws_resend.py), [`tests/test_ui_u45.py`](tests/test_ui_u45.py)).

### Добавлено (2026-05-11)

- **Intelligence / speed (WhatsApp):** структурные задержки этапов в логе `restomind.pipeline` — `stage=pipeline_timing`, поле `rm_stage_ms` (`dedupe`, `preflight`, `context`, `llm`, `route`, `reply`); отключается `PIPELINE_TIMING_ENABLED=0`. Fast-path «спасибо» без LLM — `WHATSAPP_FAST_ACK_ENABLED` (по умолчанию включён). Кэш блока «сейчас у заведения» — `cached_format_org_current_time_block`; строка меню — in-process + Redis `rm:menu_ctx:{org_id}` (`RESTAURANT_MENU_CTX_REDIS_TTL_SEC`, сброс best-effort при `invalidate_menu_context_cache`). E11: `app/services/sales_strategy_engine.py` (лимит `recommendation_trace` до LLM). Регресс: `tests/test_pipeline_timing.py`, `tests/test_restaurant_context_cache.py`, `tests/test_sales_strategy_engine.py`.
- **Документация / env:** `.env.example` и `README.md` описывают новые настройки `WHATSAPP_FAST_ACK_ENABLED`, `PIPELINE_TIMING_ENABLED`, `RESTAURANT_MENU_CTX_REDIS_TTL_SEC`, а также диагностику `GET /api/admin/system/task-queue-health`.

### Добавлено (2026-05-11)

- **E0.1 / админ-API:** маршруты `GET /api/admin/customers/{phone}/summary`, `POST /api/admin/customers/{phone}/note`, `POST /api/admin/customers/{phone}/ai-pause` вынесены из [`app/api/admin/_monolith.py`](app/api/admin/_monolith.py) в новый модуль [`app/api/admin/customers.py`](app/api/admin/customers.py) (поведение и пути 1:1, подключается через `router.include_router(customers_router)`). Регресс: [`tests/test_admin_customers.py`](tests/test_admin_customers.py).
- **E5 / диагностика очереди:** новый эндпоинт `GET /api/admin/system/task-queue-health` (модуль [`app/api/admin/system.py`](app/api/admin/system.py)) — структурированный ответ `{ redis: ok|degraded|down, arq: ok|down, worker: ok|degraded|down|unknown, details, checked_at }`. Логика — в [`app/services/task_queue_health.py`](app/services/task_queue_health.py): `redis = degraded` для in-memory fallback; `arq = down` если `arq_can_run()` или `create_pool` падает; `worker` читает arq health-key `<queue>:health-check` и трактует TTL как «жив / устарел / нет данных». Контракт совпадает со стабом UI (`admin-app.js → refreshTaskQueueHealth`). Регресс: [`tests/test_admin_system_task_queue_health.py`](tests/test_admin_system_task_queue_health.py).
- **E5 / логи enqueue:** [`app/services/task_queue.py`](app/services/task_queue.py) теперь пишет структурный лог на каждый `enqueue_job` — поля `event=task_queue_enqueue`, `queue`, `job`, `outcome=enqueued|enqueue_failed|pool_unavailable`, опционально `job_id` и `error`. Лог уезжает через стандартный `logging` (`extra=…`), `TaskQueueEnqueueError` сохраняет ту же сигнатуру. Регресс: [`tests/test_task_queue_logging.py`](tests/test_task_queue_logging.py).
- **E2.3 light / billing-контракт:** в [`app/services/billing_guard.py`](app/services/billing_guard.py) добавлены константы `BILLING_SUSPENDED_DETAIL`, `BILLING_SUSPENDED_HEADER` (`X-RestoMind-Suspended-Reason: tenant_suspended`) и фабрика `billing_suspended_http_exception()`. Все точки блокировки (`POST /api/admin/auth/login`, `POST /api/admin/auth/select-org`, `_admin_auth_me_payload`, `require_admin_session_active`) используют один helper — UI и мониторинг могут роутить по заголовку без парсинга `detail`. Контракт `GET /api/admin/auth/me`: `billing_blocked: false` остаётся стабильным полем (suspended → 403, а не payload), документировано в docstring. Super-admin не блокируется. Регресс: [`tests/test_billing_suspended_contract.py`](tests/test_billing_suspended_contract.py).

### Добавлено (2026-05-12)

- **Админка / P0 lazy DOM:** вкладки «Заказы», «Диалоги» и весь блок настроек не попадают в DOM до первого открытия (`lazyTabMount` + Alpine `x-if` в [`admin.html`](app/templates/admin.html)); перед загрузкой данных — `_touchLazyTabMount()` и `$nextTick` в [`admin-app.js`](app/static/js/admin-app.js).
- **Админка / E5 (тонкий UI):** строка статуса очереди задач на дашборде (обзор) и в «Настройки → Подключения»; клиент ходит на `GET /api/admin/system/task-queue-health` и безопасно показывает «недоступно» при сетевой ошибке/403.
- **E5 / worker queue:** [`app/worker.py`](app/worker.py) задаёт `WorkerSettings.queue_name` из `ARQ_QUEUE_NAME`, чтобы worker слушал ту же очередь, куда web-процесс ставит задачи. Регресс: [`tests/test_worker_arq_config.py`](tests/test_worker_arq_config.py).
- **P1.5 / AI trust:** [`order_logic.merge_confidence_into_order_meta`](app/services/order_logic.py) — `order_meta.confidence` (`low_confidence`, `reasons`, `details.fuzzy_matches`); fuzzy-порог 0.8 по `difflib.SequenceMatcher`; доставка с адресом без `delivery_address_verified` → флаг `unverified_delivery_address`. `GET /api/admin/orders` — поля `order_confidence`, `low_confidence`. UI: `ds-order-surface--ai-confidence`, `ds-badge-warning-soft` в [`_order_card.html`](app/templates/components/_order_card.html), модалка заказа в [`_modals.html`](app/templates/screens/_modals.html).
- **P1.5 / AI snooze:** колонка `users.ai_snoozed_until` (миграция [`alembic/versions/20260512_p15_ai_snooze.py`](alembic/versions/20260512_p15_ai_snooze.py)), [`app/services/ai_snooze.py`](app/services/ai_snooze.py), `POST /api/admin/chats/{phone}/ai-snooze`; пауза LLM в [`process_message`](app/api/webhooks.py); `GET /api/admin/chats/{phone}/state` и `GET /api/admin/customers/{phone}/summary` отдают `ai_snoozed_until`. Чаты: выпадашка «ИИ: пауза», индикатор до времени.
- **Админка / подсказки:** класс `.ds-field-help` и `title` у тяжёлых полей в [`_tab_settings_restaurant.html`](app/templates/screens/_tab_settings_restaurant.html), [`_tab_settings_smart_sales.html`](app/templates/screens/_tab_settings_smart_sales.html), [`_tab_settings_connections.html`](app/templates/screens/_tab_settings_connections.html).
- **E5 / ARQ-only:** [`app/services/task_queue.py`](app/services/task_queue.py) — постановка задач только через ARQ (`TaskQueueEnqueueError` при сбое); fallback на FastAPI `BackgroundTasks` удалён. В [`app/main.py`](app/main.py) при `APP_ENV=production|staging` выполняется проверка доступности Redis+ARQ. Документация: [`README.md`](README.md), [`.env.example`](.env.example).
- **E0.1 / админ-API:** маршруты `GET /api/admin/bookings`, `PATCH /api/admin/bookings/{id}` вынесены в [`app/api/admin/bookings.py`](app/api/admin/bookings.py).
- **E2.3 / Billing (минимум):** миграция [`alembic/versions/20260512_e23_billing_minimal.py`](alembic/versions/20260512_e23_billing_minimal.py) — `tenants.plan_status`, таблица `billing_usage_daily`; rollup [`app/services/billing_rollup.py`](app/services/billing_rollup.py) + cron в [`app/worker.py`](app/worker.py); проверки [`app/services/billing_guard.py`](app/services/billing_guard.py) — блок входа (кроме superadmin) и короткое замыкание WhatsApp webhook при `plan_status=suspended`; в `GET /api/admin/auth/me` добавлено `billing_blocked: false` при успешной сессии. Тесты: [`tests/test_billing_e23.py`](tests/test_billing_e23.py).
- **Тесты:** платёжные фикстуры и отдельные сценарии патчат `dispatch_arq_or_background` (нет Redis в CI); стабилизирован `test_revenue_orders_summary_is_tenant_scoped` под скользящие окна `_period_bounds`; `test_build_order_items_json_totals` задаёт непустой `delivery_address` для ветки `unverified_delivery_address`.
- **CI / quality:** добавлена обязательная зависимость `python-multipart` для FastAPI `File/UploadFile` (`POST /api/admin/branding/logo`); полный `ruff check app tests` очищен от `F401/F841` без изменения поведения.

### Добавлено (2026-05-11)

- **E2.2 / Branding (backend):** новые поля `Tenant.brand_name`, `Tenant.brand_color_hex`, `Tenant.brand_logo_url` (миграция [`alembic/versions/20260511_e22_tenant_branding.py`](alembic/versions/20260511_e22_tenant_branding.py)) и модуль [`app/api/admin/branding.py`](app/api/admin/branding.py): `GET /api/admin/branding` (с `tenant_id`), `PATCH /api/admin/branding` (валидация HEX `#RRGGBB`, обрезка `brand_name`, очистка пустыми значениями), `POST /api/admin/branding/logo` (PNG/JPEG, ≤ 1 МБ, файл сохраняется в `app/static/uploads/branding/tenant-<id>.<ext>`, URL с cache-buster). `GET /api/admin/auth/me → branding` теперь читается из `Tenant`, а не возвращает плейсхолдер; helper [`resolve_branding_for_session`](app/services/tenant_scope.py). Тесты: [`tests/test_admin_branding.py`](tests/test_admin_branding.py).
- **E0.1 / админ-API:** база знаний (`/api/admin/knowledge*`) вынесена из [`app/api/admin/_monolith.py`](app/api/admin/_monolith.py) в отдельный модуль [`app/api/admin/knowledge.py`](app/api/admin/knowledge.py) (поведение и пути 1:1, подключается через `router.include_router(knowledge_router)`). Монолит сократился ещё на ~150 строк; `_monolith.py` стал ближе к целевому ≤ ~1500.
- **Тесты / P0 «Operator outbound»:** [`tests/test_admin_operator_outbound.py`](tests/test_admin_operator_outbound.py) — фиксируем порядок «`ChatLog(delivery_status='sending')` → commit → WhatsApp send → `finalize_outbound_delivery` → commit», и что при провале провайдера запись остаётся в БД со статусом `failed` (не теряется в откате транзакции).
- **Админка / P1.5 (контекст чата + заказы + онбординг):** компонент [`components/_chat_guest_context.html`](app/templates/components/_chat_guest_context.html) в панели «О клиенте» — профиль, активный заказ/бронь (загрузка с `GET /api/admin/orders?q=…`, `GET /api/admin/bookings?q=…`), последняя эскалация в ответе `GET /api/admin/customers/{phone}/summary` (`last_escalation`). Список заказов: поле `failed_whatsapp_near_order` (подсчёт `chat_logs` с `delivery_status=failed` в окне ±1 ч от `Order.created_at`); в карточках/модалке — бейдж и переход в чат гостя. Удаление заказа — отдельная `ds-modal-panel` с превью, чекбоксом и задержкой кнопки 1 с (`// p15:delete-modal`). Coach-marks: `?first_run=1` или первый визит (флаг в `localStorage` per staff email), шаги Inbox → Orders → Настройки (Бот/ИИ, Бренд, База знаний); стили `.ds-p15-tour-hole`.
- **Админка / P1.5 (фронт):** полоса филиала `--tenant-accent` (`admin-brand-tokens.js` → `restoMindApplyTenantAccent`, классы `ds-admin-tenant-stripe-header` / `ds-admin-tenant-stripe-sidebar`); при смене филиала гашение хрома до загрузки данных (`orgSwitchChromeDimmed`). Форматтеры `fmt.timeAgo` и `fmt.dateTime` для лент и списков (tooltip с абсолютным временем). Скелетоны на заказах, чатах, аналитике, ленте дашборда и inbox; анимация `.ds-skeleton-line`. Логин подтягивает черновик бренда через `syncBrandingDraftFromUser` после `userData`.
- **Админка / меню (bulk-stoplist):** `POST /api/admin/menu/bulk-stoplist` — массово стоп / снять стоп / смена раздела (поле `category` — строка как в `menu_items.category`); ответ `{ ok, updated, failed[] }`; реализация в [`app/api/admin/menu_bulk.py`](app/api/admin/menu_bulk.py). Вкладка «Меню»: батч вместо N× PATCH; long‑press на карточке каталога (~520 ms) для multi‑select на мобильных; в [`admin-app.js`](app/static/js/admin-app.js) помечено `// bulk-stoplist`.
- **Тесты:** `tests/test_admin_menu_bulk_stoplist.py`; регресс «вызов без org» для `load_available_menu` / `validate_order` в `tests/test_order_logic.py`.

### Изменено (2026-05-11)

- **Меню / изоляция филиалов:** [`load_available_menu`](app/services/order_logic.py) — обязательный `organization_id`, только `MenuItem.organization_id == org` (без смешения legacy NULL в этом пути); [`validate_order`](app/services/order_logic.py) и [`calculate_total_and_fees`](app/services/order_logic.py) требуют `organization_id` при загрузке меню из БД без готового списка позиций.

### Документация (2026-05-11)

- **Спринт / параллельная разработка:** `docs/sprints/2026-05-11__parallel-streams/` — пары потоков A∥C, A∥B, B→C (bulk-stoplist + черновик JSON), A∥C (dedupe + Compact Kanban), D после крупного UI; ссылки на файлы и DoD.

### Исправлено (2026-05-10)

- **Платежи / безопасность:** `freedom_pay.py` — `verify()` теперь делает реальную HMAC-SHA256 + MD5-подпись (стандарт Freedom Pay: `md5(script;sorted_params;secret)`); раньше возвращал `True` при любом запросе если env var был задан — критическая уязвимость. `kaspi.py` — HMAC-SHA256 верификация по заголовку `X-Kaspi-Signature` с поддержкой `sha256=` prefix; добавлен `FreedomPayInitiator` для создания платёжных сессий.
- **Платежи / инфраструктура:** миграция `20260509_payment_tx_config` — таблицы `payment_transactions` + `organization_payment_configs`; миграция `20260510_org_pay_cfg_json` — колонка `organizations.payment_config_json` (JSON, nullable, per-org конфиг провайдеров). Поле добавлено в ORM-модель `Organization`.
- **Платежи / SQLite-патч:** `app/main.py` `_apply_sqlite_startup_schema_patches()` — добавлен `ALTER TABLE organizations ADD COLUMN payment_config_json TEXT` для dev-окружений; отсутствие этого патча ломало login (SQLAlchemy генерирует `SELECT ... payment_config_json ...` при любом запросе к Organization).
- **Настройки / layout:** `_tab_settings_restaurant.html` — лишний `</div>` (строка 621) закрывал `flex-1` content div до секции «Платёжные провайдеры»; на десктопе `lg:flex` платёжный блок отображался как отдельная колонка рядом с формой профиля. Исправлено перемещением закрывающего тега.
- **Меню / дубли карточек:** `app/static/css/admin.css` пересобран (`npm run build:admin-css`) — классы `sm:hidden` и `hidden sm:flex` добавлены в скомпилированный CSS; dual-layout (мобильная строка + десктопная карточка) теперь работает корректно.
- **Меню / шапка-toolbar:** убрана внутренняя карточка `rounded-2xl border shadow-sm` → плоская полоса `bg-white border-b` на всю ширину (не "обрывается" и не перекрывает контент как "остров" при скролле). Счётчики «поз.» и «в стопе» перенесены inline в строку поиска — toolbar сократился с 3 строк до 2.
- **Меню / категории:** `_tab_menu.html` — удалены заголовки секций КУХНЯ/БАР и двойной x-if (>20 / ≤20); единый `flex-wrap gap-2 max-h-[120px] overflow-y-auto` с прокруткой колёсиком мыши.
- **Меню / карточки:** убрана кнопка «Изменить» (вся карточка кликабельна); добавлена метка категории под названием блюда; dual-layout (mobile list-row / desktop card) для мобильной адаптации.

### Добавлено (2026-05-10)

- **Настройки / платёжные провайдеры:** `_tab_settings_connections.html` — раздел «Платёжные провайдеры» (Freedom Pay, Kaspi Pay, CloudPayments) с toggle включения, webhook URL и инструкцией по env-переменным. API: `PATCH /organization/payment-providers` (toggle) + `GET/PUT/DELETE /organization/payment-config/{provider}` (полный CRUD в `_tab_settings_restaurant.html`).
- **Тесты / регрессии:** `tests/test_template_div_balance.py` — 3 теста: баланс `<div>` во всех screen-шаблонах, вложенность `#settings-restaurant-payment` внутри `flex-1`, целостность внешнего контейнера. `tests/test_admin_login_regression.py` — 3 теста: login с дефолтными кредами, доступность столбцов модели Organization в БД, получение ws_token после входа.
- **Конвенции:** `docs/CONVENTIONS.md` §8 — «Инварианты Jinja2/HTML-шаблонов»: §8.1 баланс `<div>` в screen-файлах (почему лишний `</div>` = layout-баг на десктопе), §8.2 синхронизация модели и миграций (чеклист, исторический пример с login-регрессией).

### Документация

- **Актуализация:** `codebase.md` — дерево `app/api/admin/` (`intelligence.py`, `schemas.py`, `__init__.py`); `docs/UI_MAP.md` — IA после P1.5.0 (`inbox`, `ai_center`, аналитика внутри дашборда, legacy-экраны); `README.md` — таблица ключевых документов в `docs/`; `CLAUDE.md` / `CODEX.md` — ссылки на UI_MAP, AI_TOOLS_SETUP, AI_OPERATIONS, EVENT_ARCHITECTURE и диапазон ROADMAP P0–P4; `docs/AI_TOOLS_SETUP.md` — расширенное дерево `docs/` и скрипт `scripts/capture_admin_mobile_review.py`.
- **ROADMAP — Wishlist Темира (2026‑05):** в [`docs/ROADMAP.md`](docs/ROADMAP.md) добавлены недостающие задачи и индексный блок «📥 Wishlist Темира (2026‑05)» с матрицей done/partial/missing по 23 пунктам обратной связи (общий список + дополнительный для RestoMind). В **P1.5** добавлены: failed‑бейдж сообщений в карточке/модалке заказа (#3), кастомная модалка удаления заказа с превью (#10), onboarding / coach‑marks внутри админки (#15). В **P2** добавлены: ночные предзаказы + Telegram «на смене» + сводка для оператора (#20), горячая рассылка по клиентам + бонусная система (#19), авто‑сбор отзывов после заказа с роутингом 2GIS / админ (R3). В **P3** добавлены: авто‑рассылка из iiko по клиентам (R1), VIP‑кейс — отдельный сайт/мини‑приложение (R2), KPI‑центр официантов из iiko (R4). В **P4** существующие SLA monitor / operator efficiency / AI incident detection помечены как часть пункта #18.

### Исправлено

- **Админка:** вкладка «Меню» — один общий корень вместо двух соседей внутри `template x-if` (Alpine требует один child); блок «Аналитика» вложен в разметку «Дашборд»; в `loadTabData` нормализация устаревшего `currentTab === 'analytics'` → дашборд + под-таб.

### Добавлено

- **Админка / диагностика:** маркеры `data-rm-tab-surface` для «Меню», аналитики дашборда и «Профиль и логистика»; после `loadTabData` вызывается `_auditActiveTabSurface` — в консоль через `adminLogger.error` (нет узла в DOM) или `adminLogger.warn` (узел есть, но не виден); успех — только на уровне `debug` (`?admin_log=debug`). Регрессия: [`tests/test_admin_tab_surface_audit.py`](tests/test_admin_tab_surface_audit.py).
- **AI Operations / Intelligence:** добавлены durable `SystemEvent`, `OperationalInsight`, `RestaurantStateSnapshot`, `IntelligenceConversation`/`IntelligenceMessage`, API `/api/admin/intelligence/*`, вкладки `AI-аналитик` и `Digital Twin`, MVP-аналитика по выручке/заказам/отменам и симулятор операторской нагрузки. Документация: [`docs/AI_OPERATIONS.md`](docs/AI_OPERATIONS.md), [`docs/EVENT_ARCHITECTURE.md`](docs/EVENT_ARCHITECTURE.md).

- **Админка / UI-документация:** добавлена карта UI-слоя [`docs/UI_MAP.md`](docs/UI_MAP.md): layout, screens, components/macros, client logic и текущие контракты для дальнейших правок.
- **Админка / доступы:** добавлены опциональные env-креды `SUPERADMIN_USERNAME`/`SUPERADMIN_PASSWORD` для legacy-входа с `is_superadmin=true` (быстрый доступ к `/superadmin` без StaffUser); подпись WS-токена завязана на `SESSION_SECRET`.

- **P0 (стабильность/платформа):** `test_bot` переведён на 3 фазы (чтение → LLM без DB-сессии → запись); Telegram-уведомления переведены в fire-and-forget; экстренное закрытие ресторана (force-close) реализовано end-to-end (модель+миграция+API+UI); добавлен счётчик токенов (AiUsageLog + `_usage` в ответе ИИ + UI «Токены сегодня»); на мобильном дашборде статус работы и бейдж «⛔ Временно закрыто» видны на всех экранах.

- **Админка / UI (Phase U7):** опубликована спецификация дизайн-системы — [`docs/UI_DESIGN_SYSTEM.md`](docs/UI_DESIGN_SYSTEM.md) (принципы, токены `:root`, каталог макросов, IA, anti-patterns, гайд новой страницы, **врезки baseline-скринов** по разделам и настройкам, приёмка a11y + ссылка на Lighthouse). Статусы задач ведём в [`docs/ROADMAP.md`](docs/ROADMAP.md).
- **Админка / Lighthouse (Phase U6):** скрипт [`scripts/run_admin_lighthouse.mjs`](scripts/run_admin_lighthouse.mjs), команда **`npm run lh:admin`**; dev-зависимости `lighthouse`, `chrome-launcher`, `playwright` в [`package.json`](package.json); инструкция [`docs/ui/lighthouse/README.md`](docs/ui/lighthouse/README.md); полные JSON-отчёты в `docs/ui/lighthouse/reports/` — в [`.gitignore`](.gitignore); тест наличия артефактов [`tests/test_lighthouse_docs.py`](tests/test_lighthouse_docs.py).
- **Админка / UI (Phase U6):** мобильные модалки `ds-modal-panel` — отступ `safe-area-inset-bottom`, визуальная «ручка» bottom-sheet, то же для `ds-drawer-panel`; сегменты и `ds-btn-sm/md` с минимальной высотой **44px**; [`admin.html`](app/templates/admin.html) — **45** интерактивных зон с `min-h-[44px]`; канбан: **`data-kanban-col`**, `role="region"`, `tabindex="0"`, обработчик **`@keydown` → `handleKanbanKeydown`**, табы вида заказов с **`role="tab"`**; [`_drawer.html`](app/templates/components/_drawer.html) — заголовок **`<h2 id="…-title">`** для **aria-labelledby**. Регрессия: `tests/test_ui_u6_a11y.py`. Пересобран [`app/static/css/admin.css`](app/static/css/admin.css) из [`src/css/admin-input.css`](src/css/admin-input.css).

### Изменено

- **Админка / IA (P1.5.0):** сайдбар **4+4** (**Операции** / **Управление**): пункт **«Требует внимания»** (`inbox`: табы от клиентов / системные), **«ИИ-аналитика»** (`ai_center`: вклад ИИ / инсайты / нагрузка), аналитика перенесена в **Дашборд** (под-таб «Аналитика»). Hash `#operator_queue`, `#incidents`, `#errors`, `#analytics`, `#ai_value`, `#intelligence`, `#digital_twin` редиректятся на новые `#inbox`, `#dashboard`, `#ai_center` с `?tab=`. Обновлены [`admin-app.js`](app/static/js/admin-app.js), [`admin.html`](app/templates/admin.html), экраны [`_tab_inbox.html`](app/templates/screens/_tab_inbox.html), [`_tab_ai_center.html`](app/templates/screens/_tab_ai_center.html); документация IA в [`docs/UI_DESIGN_SYSTEM.md`](docs/UI_DESIGN_SYSTEM.md).

- **P0 (стабильность):** OpenAI transient‑ошибки (`RateLimitError | APIConnectionError | APITimeoutError | APIError 429/5xx`) после внутренних ретраев поднимают `TransientAiError` в [`app/services/ai_engine/openai_p.py`](app/services/ai_engine/openai_p.py); диспетчер [`app/services/ai_brain.py`](app/services/ai_brain.py) пробрасывает его наверх (`raise_on_transient=True`), retry‑цикл `_enqueue_processing` в [`app/api/webhooks.py`](app/api/webhooks.py) (3 попытки, exp back‑off) делает повтор вместо «успешной» эскалации. Аналог в `gemini_p.py`.
- **P0 (UI / заказы):** `loadOrders` в [`app/static/js/admin-app.js`](app/static/js/admin-app.js) теперь устойчив к гонке REST↔WebSocket — seq‑guard (`_ordersLoadSeq` отбрасывает устаревшие ответы) + merge по `row_version` (REST не перетирает более свежие WS‑данные).
- **Админка / UI (split admin.html):** [`app/templates/admin.html`](app/templates/admin.html) сокращён до ~75 строк и собирается из 27 экранов в [`app/templates/screens/`](app/templates/screens/) через `{% include %}` (login, sidebar, header, banners, табы дашборда/заказов/брони/чатов/меню/incidents/intelligence/digital_twin/ai_value/analytics/operator_queue, 8 экранов настроек, modals, bottom_nav). Поведение Alpine/DOM не менялось; ленивый DOM (`x-if`/mount‑on‑demand) — отдельным шагом.
- **Документация / UX дорожка:** в [`docs/ROADMAP.md`](docs/ROADMAP.md) добавлен раздел **🟠 P1.5: UX Density & AI Trust** (Compact Kanban, tenant color stripe, right context panel в чатах, AI Confidence badge, AI Snooze с таймером, bulk‑actions в стоп‑листе, skeletons + relative time, переснятие baseline‑скринов); в [`docs/UI_DESIGN_SYSTEM.md`](docs/UI_DESIGN_SYSTEM.md) — две новые секции: «Density modes» (Normal vs Compact для канбана/таблиц/списков) и «AI in UI» (визуальный язык ИИ: цвет, бейдж источника, confidence, snooze, realtime pulse).

- **Админка / UI (Phase U5 — полная миграция):** дашборд (ROI, «Ценность ИИ», «Требуют внимания», быстрые действия, сегментированный выбор метрики графика), вкладка **Аналитика** (`section_header`, `segmented`, `card` для KPI и блоков), **Заказы** (переключатель канбан/таблица через `segmented`), **Вклад ИИ** (период через `segmented`, KPI через `kpi_card` / `card`), **Помощь клиентам** (KPI, фильтры `ds-input`, таблица в `card`), карточки **Бронирования** (`ds-card`), модалки заказа / тестового заказа / брони на паттерн `ds-modal-backdrop` + `ds-modal-panel`. Файл: [`app/templates/admin.html`](app/templates/admin.html).

- **Админка / UI (Phase U5, шаг 1 — дашборд):** главные KPI, график «Динамика за 7 дней», лента событий, мини-KPI «Помощь»/«iiko», блок «Последние заказы» переведены на макросы [`_kpi_card.html`](app/templates/components/_kpi_card.html) и [`_card.html`](app/templates/components/_card.html) (`ds-card` / `ds-kpi*`). Макрос KPI расширен: `value_html`, `label_class`, `value_class`, `extra_class` для вёрстки с Alpine. В [`src/css/admin-input.css`](src/css/admin-input.css) добавлены классы дизайн-системы `ds-card`, `ds-kpi`, `ds-kpi-label`, `ds-kpi-value`, `ds-text-muted`; пересобран [`app/static/css/admin.css`](app/static/css/admin.css) (`npm run build:admin-css`).

- **Админка / настройки (Phase U4):** раздел **Интеграции** — раскрывающиеся карточки и webhook WhatsApp внутри карточки; **Данные и безопасность** — группировка экспорта, ретеншна и опасных действий в аккордеоны; вкладка **Бот / ИИ** (`settingsTab: bot_test`) — лаборатория с готовностью, переходами к знаниям/допродажам/упаковке и тестовым чатом; при открытии вкладки подгружается `setupStatus`. Файлы: [`app/templates/admin.html`](app/templates/admin.html), [`app/templates/components/_settings_tabs.html`](app/templates/components/_settings_tabs.html), [`app/static/js/admin-app.js`](app/static/js/admin-app.js).

### Добавлено

- **Админка / UI Redesign Phase U4.5:** реализованы рабочие вертикали U4.5: triage диалогов (`User.meta_json.chat_triage` — активные / на мне / закрытые, Close/Reopen, Snooze, takeover/release), post-iiko статусы заказов (`in_transit`, `waiting_pickup`, `completed`) с историей `order_meta.fulfillment_events`, feedback-loop из модалки заказа (`POST /api/admin/orders/{id}/feedback/upsell-rule` — правило или анти-правило с тегом `not_upsell`), `x-ignore` для Chart.js canvas и пагинация канбана («ещё 20» по колонкам), правила упаковки с `scope` `item` / `category` / `order` (миграция Alembic [`20260507_ui_u45_packaging_scope.py`](alembic/versions/20260507_ui_u45_packaging_scope.py), ревизия `20260507_ui_u45_packaging`), SQLite-патч колонок в [`main.py`](app/main.py), модификаторы в редакторе состава заказа; регрессионные тесты [`tests/test_ui_u45.py`](tests/test_ui_u45.py).
- **Админка (клиентский лог):** объект `adminLogger` в [`admin-app.js`](app/static/js/admin-app.js) — единый префикс `[RestoMind]`, уровни `debug` / `info` / `warn` через `?admin_log=…`, `localStorage.restomind_admin_log=debug` или `window.__RESTOMIND_ADMIN_LOG_LEVEL__` (0–4); **`error` всегда пишется в консоль**; глобально `window.adminLogger` для отладки.
- **Админка / E2.2.F (UI):** вкладка настроек **«Брендинг»** — название, акцентный цвет, загрузка лого (до 1 МБ), предпросмотр шапки; сохранение через `PATCH /api/admin/branding` и `POST /api/admin/branding/logo` при наличии **E2.2.B**; до этого API корректно обрабатывает 404. Шапка использует `brand_color_hex` для фона аватара и буквы из бренда / названия филиала.

### Изменено

- **Доступность (WCAG):** у ряда кнопок с только иконкой или символом добавлены осмысленные `aria-label` и `aria-hidden` для декоративных SVG в [`admin.html`](app/templates/admin.html); ссылка скачивания payload в [`superadmin.html`](app/templates/superadmin.html).
- **Доступность (WCAG, продолжение):** универсальное окно подтверждения `uiConfirm` — `role="dialog"` на белой панели, `aria-labelledby` / условный `aria-describedby`; модалки заказа, тестового заказа и брони — связка заголовка с диалогом; чек-лист готовности и редактор графика в Super Admin — уточнённые подписи для кнопок закрытия.
- **Админка / настройки:** вкладка «Мой ресторан» — липкая панель с подвкладками настроек и быстрыми ссылками «Профиль», «База знаний», «Упаковка» (якоря с `scroll-margin`); блок упаковки перенесён в общий поток страницы сразу после профиля и базы знаний, чтобы при прокрутке липкая навигация оставалась доступной.

### Изменено

- **Документация:** введён единый трекер задач/статусов — [`docs/ROADMAP.md`](docs/ROADMAP.md); правила агента — `.cursor/rules/restomind-ai.mdc` и `.cursor/rules/restomind-zones.mdc`.
- **Документация:** актуализированы [codebase.md](codebase.md) (дерево `app/api/admin/`) и [README.md](README.md) (скрипты сборки CSS/линтинга админки из `package.json`).

- **Доступы и блокировки:** `Organization.is_active=False` теперь блокирует вход staff в админку и игнорирует входящие WhatsApp webhooks для этого ресторана (без ретраев Meta).
- **Self-serve onboarding:** legacy `POST /api/admin/auth/signup` переведён в `410 Gone`; маршрут `/onboarding` редиректит на `/request-access`.
- **Деплой / БД:** добавлен [docs/SUPABASE_MIGRATION.md](docs/SUPABASE_MIGRATION.md) (Render Postgres → Supabase, Alembic, env); [render.yaml](render.yaml) — внешний секрет `DATABASE_URL` вместо managed PostgreSQL в Blueprint.
- **AI:** чат и STT на **OpenAI** — structured output (`call_openai`, `beta.chat.completions.parse`), голос — **Whisper** (`openai_transcribe_voice`, `call_openai_with_audio`). Переменные окружения: `OPENAI_API_KEY`, опционально `OPENAI_MODEL`, `OPENAI_TRANSCRIPTION_MODEL`. Удалена зависимость `google-genai`.

### Изменено

- **E0.1 (часть 1):** монолитный `app/api/admin.py` заменён на пакет [`app/api/admin/`](app/api/admin/) — логика во временном [`_monolith.py`](app/api/admin/_monolith.py), общие проверки сессии и tenant-clause вынесены в [`deps.py`](app/api/admin/deps.py); импорт `from app.api.admin import …` без изменений для [`main.py`](app/main.py) и тестов.

### Добавлено

- **Super Admin / E1 хвост:** `GET /api/superadmin/payment-webhook-events/{id}/payload.bin` (`application/octet-stream`) — сырой payload для кнопки скачивания в [`superadmin.html`](app/templates/superadmin.html).

- **E2.1 мультифилиальность (backend):** колонка `staff_users.tenant_owner_id` (FK на `tenants`), миграция `20260504_e21_owner`; расширенный контракт `GET /api/admin/auth/me` (`tenant_owner_id`, `active_organization_id`, `available_organizations`, `tenant`, `branding`-заглушка), `POST /api/admin/auth/select-org`; защищённые маршруты админки принимают активный филиал из сессии для владельца сети; тесты `tests/test_select_org.py`, `tests/test_tenant_owner_scope.py`.

- **Админка / UX (ИИ 2):** в Super Admin добавлен read-only просмотр `payment_webhook_events` (таблица + карточка), в админке появился раздел «Вклад ИИ» с периодами 7d/30d/90d и fallback на `/api/admin/stats`, отдельный пункт «Ошибки» ведёт на существующую очередь `failed_tasks`; добавлен дефолтный текст предоплаты через константу `DEFAULT_PREPAYMENT_LEGAL_TEXT`.

- **Надёжный merge / регрессия (E4, E10):** хелпер `persist_draft_order_optimistic_update` для одного optimistic UPDATE черновика; тесты `tests/test_action_id_dedup.py`, `tests/test_atomic_merge.py`; каталог `tests/regression/` с маркером `regression` (цепочки корзины, граница бесплатной доставки, mixed payment, анти-повтор upsell); в CI отдельный шаг прогона регрессии.

- **Админка / E3:** `GET /api/admin/ai-value` (периоды `7d`/`30d`/`90d`/`custom`) — метрики допродаж, сообщений ассистента, automation (`bot_orders` / `takeover_orders`), эскалаций и `daily_series` для вкладки «Вклад ИИ»; тесты `tests/test_ai_value_metrics.py`. Ранее по ИИ 2: вкладка «Вклад ИИ», пункт «Ошибки» (тот же UI, что «Помощь клиентам»), аудит webhook в Super Admin.

- **Платежи / аудит webhook:** таблица `payment_webhook_events` (сырой body до проверки подписи), запись на каждый `POST /api/webhooks/payment*`, superadmin `GET /api/superadmin/payment-webhook-events`; адаптеры `generic_hmac`, `cloudpayments` и каркасы `kaspi`, `freedom_pay` на `/api/webhooks/payment/providers/{slug}`; переменные `CLOUDPAYMENTS_API_SECRET`, `KASPI_HMAC_SECRET`, `FREEDOM_PAY_WEBHOOK_SECRET`.
- **Админка / операторка:** у `failed_tasks` появился ручной повтор обработки (`POST /api/admin/failed-tasks/{id}/retry`) с tenant-scope проверкой; в очереди ошибок добавлена кнопка «Повторить», а Auto Setup Score в шапке стал компактным индикатором с чек-листом в модалке.
- **Super Admin Panel (foundation):** `staff_users.is_superadmin`, `organizations.is_demo`, новая таблица `registration_requests`; API `/api/superadmin/*` для управления ресторанами, заявками, блокировкой `is_active`, технастройкой и force-sync меню.
- **Approval onboarding:** `POST /api/admin/auth/request-access`, публичная страница `/request-access`, уведомление в Telegram на новый лид (`SUPERADMIN_TELEGRAM_CHAT_ID` с fallback на `TELEGRAM_ADMIN_CHAT_ID`).
- **Demo guest mode:** `POST /api/admin/auth/demo-login`, login-экран с кнопкой «Попробовать демо»; для demo-сессий включён read-only guard на небезопасные методы.
- **Служебные артефакты:** миграция `20260427_superadmin_foundation`, скрипт `scripts/grant_superadmin.py` для выдачи роли владельца.
- **RestoMind v2.0 (логистика заказа):** тарификация контейнеров и доставки (`order_logic`, настройки `PRICING_*` в `.env`); расширенный `AIBrainResponse` (тип заказа, оплата, адрес/время); после «Да» в WhatsApp заказ только подтверждается — в iiko отправляет оператор из админки; комментарий к заказу в iiko с типом и пометкой WhatsApp; тесты `tests/test_pricing.py`.
- **Демо-данные** (`demo_data.py`, `seed.py`): заказы собираются через `build_demo_order_payload` — в `items_json` есть `fee_lines`, `order_meta`, `foods_subtotal`, `total_price`; сценарии доставка/самовывоз/зал и способы оплаты; объёмные и «месячные» демо-заказы с чередованием типов.
- **Админка — заказы:** бейджи типа/оплаты, строки `fee_lines` в модалке, кнопка «ПОДТВЕРДИТЬ И ПЕЧАТЬ В IIKO», колонка «Тип» в таблице и бейдж в мобильном списке.
- **Промпт:** блок «Золотой стандарт эскалации» — при сомнении `faq` vs `escalate` выбирать `escalate`, эмоциональный фильтр, короткий эмпатичный ответ при эскалации (`app/services/prompts.py`).
- **Канбан / API:** переход **confirmed → draft** (DnD в колонку «Черновики» + `PATCH /api/admin/orders/{id}`); при **401** на защищённых запросах — однократная перезагрузка страницы (без цикла на `auth/me`); heatmap — минимальная видимость ненулевых ячеек.

- **Админка — полировка:** график аналитики рисуется один раз после загрузки (`loadAnalytics` только данные; `reloadAnalyticsForUi` / `loadTabData`); `fmt.date` / `fmt.time` — общий `_parseDateInput` (Date, пробел вместо `T`, YYYY-MM-DD); при **401** — очистка данных в памяти и уничтожение Chart.js до показа формы входа; канбан — усиленная обводка карточек с `iiko_last_error`.

- **Админка — мобильный UX:** вкладка «Диалоги» — шапка чата в две строки, кнопки ИИ/перехвата на всю ширину рядом, без перекрытия ленты; «Меню» — блок чипов категорий с ограничением высоты и вертикальной прокруткой на узких экранах, чтобы карточки позиций оставались видны ниже.

- **Админка — мобильная вёрстка (продолжение):** стек «Диалоги» (список ↔ чат ↔ выезжающая панель «О клиенте» на `<lg`), кнопка «Назад» в шапке чата; канбан заказов — горизонтальный snap-скролл колонок на узких экранах; отступы контента и липкой панели «Меню»; `inputmode` для телефонов и сумм; крупнее touch-targets в шапке.

- **`GET /health/deep`** — ручная диагностика: `SELECT 1` к БД и `redis_client.ping()`; **`GET /health`** остаётся максимально лёгким (`{"status":"ok"}`), без поля `service` и без запросов к БД (Render / UptimeRobot).
- **Бронирования:** клик по карточке открывает модальное окно с датой/временем, гостями, телефоном, именем, комментарием, статусом и временем создания; кнопка «Открыть диалог»; в API списка броней добавлено поле `user_name`.
- **Залы в бронированиях:** поле `hall` (`hall_1`, `hall_2`, `vip`); VIP — не более одной активной брони на пару дата+время (кроме отменённых); выбор/смена зала в модалке (`PATCH /api/admin/bookings/{id}`); ИИ в `booking_details.hall`; подсказка на кнопке перехвата диалога вместо «Redis» в названии.
- **Бронирования (модалка):** редактирование **статуса** (черновик / ожидает / подтверждён / отменён), кнопка **«Копировать»** номер в буфер; `PATCH` принимает `hall` и/или `status`.
- **Fallback на оператора:** таблица `escalation_events` (запись при `intent: escalate` → `HUMAN_MODE`); опциональные `TELEGRAM_BOT_TOKEN` и `TELEGRAM_ADMIN_CHAT_ID` — алерт в Telegram со ссылкой в админку (`PUBLIC_BASE_URL`); WebSocket `human_needed` дополняется `user_message` и `intent`; аналитика — блок «Эскалации» с сравнением с предыдущим периодом; усилен системный промпт для сценариев эскалации.
- **Админка — интеграции:** вкладка «Интеграции» — индикатор последней синхронизации **стоп-листа** iiko (зелёный / красный / жёлтый до первого цикла), флаг настройки **WhatsApp**, кнопка **«Синхронизировать меню и стоп-листы сейчас»** (`POST /api/admin/integrations/sync`, учётные данные из `.env`). API: `GET /api/admin/integrations/status`.
- **Ошибки iiko по заказам:** поле `orders.iiko_last_error` — при неудачной отправке подтверждённого заказа в iiko текст сохраняется; в списке/канбане/карточке заказа показывается алерт **«Ошибка iiko: …»**; событие WebSocket `order_updated` передаёт `iiko_last_error`.
- **Таблица `integration_health`:** одна строка с метками времени и результатом последних синхронизаций меню и стоп-листа; фоновый цикл в `main.py` обновляет статус стоп-листа при успехе и ошибке.
- **Деплой на Render:** `render.yaml` (Blueprint: Web Service; БД — внешний `DATABASE_URL`), `DEPLOY_RENDER.md`, `docs/VERCEL.md`; в конфиге поддержка `DATABASE_URL` и `CMD` Docker с `PORT`; миграции через `preDeployCommand: alembic upgrade head`.
- **Форма входа в админку** — полноэкранный экран до авторизации; сессия в cookie (`SessionMiddleware`), API `POST /api/admin/auth/login`, `GET /api/admin/auth/me`, `POST /api/admin/auth/logout`.
- **WebSocket-токен** — подпись `itsdangerous` (`/api/admin/auth/login` и `/auth/me` отдают `ws_token`), query `?token=` на `/api/admin/ws`.
- **Демо-данные** — кнопки «Демо-данные» / «Удалить демо» в шапке; `GET/POST/DELETE /api/admin/demo*`, префикс телефонов `demo7700…`.
- **Сессии:** `SESSION_SECRET` (опционально), cookie `restomind_admin`, зависимость `itsdangerous`.
- **Rate limiting** по номеру телефона: скользящее окно 60 с, Redis или in-memory; `RATE_LIMIT_PER_MINUTE`.
- **Логи в файлы:** `logs/restomind.log`, `logs/errors.log` с ротацией (`RotatingFileHandler`).
- **Sentry:** опционально `SENTRY_DSN` (если установлен `sentry-sdk`).
- **WhatsApp:** `send_template()` для одобренных шаблонов Meta (проактивные уведомления).
- **Бронирование:** состояние `CONFIRMING_BOOKING`, черновик → подтверждение «Да»/«Нет» → `confirmed` / `cancelled`.
- **Мультитенантность (фундамент):** модель `Organization`; `organization_id` у **User, Order, Booking, MenuItem** (nullable). У **ChatLog** отдельного поля нет — связь с организацией через пользователя.
- **Telephony (v2.0 заготовка):** `app/integrations/telephony.py` — интерфейсы STT/TTS, `TelephonyRouter`, `CallSession`.
- **Тесты:** pytest + pytest-asyncio, **25** unit-тестов в `tests/` (включая тарификацию v2 и демо-payload).
- **CI/CD:** `.github/workflows/ci.yml` (pytest + проверка импорта приложения), `deploy.yml` (SSH + docker compose, секреты `DEPLOY_*`).
- ~~**Меню без ручного seed:** встроенный каталог при пустой таблице~~ **снято:** меню только из **iiko Cloud** (`sync_menu_from_iiko` / админка). Файл `menu_bootstrap.py` удалён.
- **Редактирование меню в админке:** `POST/PATCH/DELETE /api/admin/menu` — добавление позиции, правка цены/названия/раздела/описания/URL фото/наличия, удаление; во вкладке «Меню» — кнопки «Добавить», «Изменить», переключатель «В наличии / Стоп» на карточке.
- **Демо из админки (`POST /api/admin/demo/seed`):** **10** демо-клиентов, **~48** заказов, **14** броней, **~30** сообщений в `chat_logs`; встроенное меню из каталога **не** добавляется (только iiko-синхронизация).
- **Кабина оператора (Диалоги):** правая колонка — сводка по клиенту (`GET /api/admin/customers/{phone}/summary`), быстрые ответы, заметка `operator_note` (`POST /api/admin/customers/{phone}/note`); поле `User.operator_note` в БД.
- **Звук в админке:** короткие сигналы Web Audio при входящем сообщении клиента (другой чат или вкладка в фоне) и при появлении нового заказа по WebSocket.

### Исправлено

- **Диалоги / сводка клиента:** суммы через `formatCustomerMoney` больше не в **рублях** (`Intl RUB`) — используется общий формат **тенге (₸)** как у остальной админки.
- **Аналитика:** параметр `period` нормализуется (`lower`, допустимые значения); ответ `GET /analytics` с **`Cache-Control: no-store`**, под карточками — явный **интервал UTC** и подсказка, почему день/неделя/месяц могут совпадать при заказах только «сегодня» по UTC.
- **Демо-данные (seed):** после `commit` вызывался лог/ответ с несуществующей переменной `created_orders` → **500** при успешной записи в БД; фронт показывал «Не удалось загрузить демо», хотя данные уже были. Добавлен подсчёт `created_orders = len(orders_spec)`.

### Изменено

- **Админка — UX/UI (крупное обновление):** мобильное боковое меню (гамбургер + оверлей); глобальный поиск **Ctrl+K** (`GET /api/admin/search`); канбан: таймер «N мин в статусе» (красный &gt;10 мин), drag-and-drop между колонками (`PATCH /api/admin/orders/{id}`); список заказов на узких экранах — компактные карточки; сайдбар: бейджи по ошибкам iiko и сбоям синхронизации.
- **Админка — аналитика:** метрика **автоматизации** (% завершённых без оператора в чате), **воронка** (чаты → черновики → завершённые в iiko), **тепловая карта** заказов по дню недели × часу UTC.
- **Админка — диалоги:** пустое состояние с горячими клавишами; кнопка **«Заблокировать ИИ»** (`users.ai_paused` + `POST /api/admin/customers/{phone}/ai-pause`); в ленте у ответов ИИ — серый блок «мысли» (`meta_json` в `chat_logs`).
- **Админка — интеграции:** журнал событий (`integration_events`, `GET /api/admin/integrations/events`); копирование URL вебхука WhatsApp при заданном `PUBLIC_BASE_URL`.
- **Админка — аналитика:** блок загруженного интервала оформлен карточкой (иконка календаря, даты в `ru-RU` через общий `fmt.date`, подпись с числом календарных дней UTC); убрана строка «(UTC, период: …)»; объяснение про совпадение день/неделя/месяц — в сворачиваемом `<details>`.
- **Админка — ошибки iiko:** на канбане при ошибке — акцентная карточка (градиент, левая полоса, кольцо); в списке заказов — строка с розовым фоном; блок алерта с иконкой «!» и заголовком «Ошибка iiko» / «Ошибка отправки в iiko» в модалке.
- **Демо-данные:** удаление через **`POST /api/admin/demo/delete`** (в UI вместо `DELETE`) — на части хостингов/прокси метод DELETE блокируется, из‑за чего кнопка «Удалить демо» не срабатывала; `DELETE /api/admin/demo` сохранён для совместимости.
- **Админка:** `apiFetch` при **401** сбрасывает сессию и закрывает WebSocket; единый `formatApiDetail` / `sortArrow` для ошибок API и стрелок сортировки.
- **Админка — заказы (`GET /api/admin/orders`):** в ответ добавлены `user_phone`, `user_name` (из `users.phone` / имени — тот же номер, что в WhatsApp), `items_count`; параметры `q` (поиск по № заказа, телефону, имени), `sum_min` / `sum_max` (фильтр по сумме). Во вкладке «Заказы» — поиск, фильтр суммы, сортировка колонок в списке; в канбане и в карточке заказа показывается телефон клиента.
- **Админка — аналитика:** в блоке «Разбивка по дням» сортировка по дате, числу заказов или выручке (график выше не меняется).
- **Демо-заказы (seed):** `created_at` для заказов «сегодня» (UTC) распределяется по **уже прошедшей** части суток, а не фиксированно 10:00–17:00 — иначе до полудня по UTC аналитика «Сегодня» показывала **0**. Добавлены заказы за **8–29** суток назад, чтобы сумма за **30 дней** заметно превышала сумму за **неделю** (раньше все демо-заказы лежали только в последних 7 днях).
- **Админка «Меню»:** чипы разделов — **flex-wrap** вместо горизонтального скролла; при **>20** категорий — подписи **«Кухня»** и **«Бар и напитки»**; у чипов — счётчик позиций; липкая панель поиска/фильтров (`z-10`, blur); индикатор **«N в стопе»** (клик → фильтр «Нет в наличии»); режим **«Выбрать»** с чекбоксами и панелью **«В стоп / В продажу»** для выделенных; подсказка на бейдже наличия (переключение без модалки).
- **Аналитика** (`GET /api/admin/analytics`): границы периода и «сегодня» считаются в **UTC**, как у дашборда (`/stats`), чтобы цифры не расходились между вкладками.
- **Админка (дашборд):** градиент под мини-графиком Chart.js через scriptable `backgroundColor` (корректно при первом рендере и resize); подпись «Нет данных за вчера» только после успешной загрузки `daily_series`.
- **Админка «Диалоги»:** список + чат + **инфо-панель** (lg+), двухпанельный скролл списка/ленты, премиум-баблы (клиент слева / ИИ и менеджер справа), textarea + Enter / Shift+Enter, `toggleTakeover`, автоскролл ленты, время превью в списке (`lastAt`), имя из `GET /chats/{phone}`.
- **Админка — вёрстка высоты:** колонка приложения `h-[100dvh]` + flex (`flex-1 min-h-0`); алерт «Бот просит помощи» **в потоке** под шапкой (не `fixed`), чтобы при его появлении область чата сжималась и инпут не уезжал за экран; убран `calc()` у чатов; навигация сайдбара с `overflow-y-auto`.
- `.env.example` — админка, лимиты, Sentry.
- `.dockerignore` — `logs/`, `tests/`.

### Исправлено

- **Дашборд «Динамика заказов»:** мини-график не должен пропадать через ~1 с после входа — `renderDashboardMiniChart()` больше не вызывает `destroy()` до проверки `daily_series`; при ошибке повторного `GET /api/admin/stats` не затираем уже загруженную серию (избегаем сценария «уничтожили график → пустой ответ»).
- **Chart.js + Alpine:** экземпляры графиков вынесены в модульный объект `charts` (вне `adminApp`), чтобы не попадать в реактивный Proxy; мини-график дашборда откладывается до `loadTabData` (+100 ms после данных); дублирующие `loadDashStats()` после «Демо-данные» / «Удалить демо» убраны; при уходе с вкладки «Аналитика» график уничтожается; контейнер аналитики — `min-h-[320px]`; при пустом `daily` аналитики старый график очищается.
- **Админка — чаты и DRY:** `GET /api/admin/chats` — список диалогов из `chat_logs` для боковой панели; `loadChatList()` заполняет `chatList` (сохранение `unread` при обновлении); при открытии вкладки «Диалоги» вызывается `loadTabData` → `loadChatList`. Форматирование дат/сумм — общий `adminFormat` + `fmt` в шаблоне; статусы — `statusConfig`; производные списки меню мемоизируются по сигнатуре (`menuViewRevision` + фильтры).
- **Админка — аналитика:** после `loadTabData` на вкладке «Аналитика» повторный `renderChart()` + `_attachChartLayoutFix` через 100 ms (стабильный размер canvas после показа вкладки).
- **Админка — графики (Chart.js):** гонка между `requestAnimationFrame` и `resize` по таймеру — график иногда создавался с canvas 0×0 или `resize` вызывался до создания экземпляра. `scheduleDashboardChartRender` и загрузка аналитики теперь **await** отрисовки; повторный `resize` через **150 / 400 / 800 ms** после смены вкладки.
- **Аналитика / дашборд:** демо-заказы с `days_ago` 7–21 не попадали в окно «последние 7 дней» — мини-график «Динамика» и «Сегодня» выглядели пустыми; даты демо и `seed.py` переведены на **0–6 дней UTC** от полуночи дня сида. **SQLite:** фильтры по `orders.created_at` используют **naive UTC** (`_sql_dt_for_filter`), чтобы «Сегодня» и периоды в `/analytics` не отдавали 0 строк. Подписи мини-графика — `T12:00:00Z` (как на аналитике).
- **Критично:** в `admin.html` у функции `renderChart()` был **не закрыт `try`** (без `catch`) — синтаксическая ошибка JS, из‑за неё не поднимался Alpine, страница оставалась белой (`x-cloak`). Исправлено.
- **Графики выручки (дашборд и аналитика):** дневная разбивка в `GET /api/admin/analytics` строится по **календарным дням UTC** без ошибки `range(num_days+1)`; ключ дня для заказов — дата в UTC (naive datetime из SQLite считается UTC). **Chart.js:** при `x-show` вкладка до показа имеет `display:none` — контейнер 0×0; добавлены `ResizeObserver`, повторные `resize()`/`update('none')`, вызов после `loadTabData`, проверка `res.ok` для `/stats` и `/analytics`, обмен дат в custom-периоде если «с–по» перепутаны. Сброс пользовательского периода при каждом заходе на «Аналитику» убран; на графике аналитики — выбор **«Только выручка / только заказы / вместе»**.
- **`GET /api/admin/stats` — `daily_series`:** вместо семи SQL-фильтров по суткам (на SQLite с naive `created_at` и границами UTC график часто был из нулей) — одна выборка заказов и **корзины по дням в Python** по тому же ключу UTC, что и аналитика. **Демо и `seed.py`:** `created_at` заказов пишется в **UTC** (`datetime.now(timezone.utc)`), чтобы совпадать с KPI «сегодня/вчера».
- **Админка «Заказы»:** подпись «Канбан» заменена на **«По этапам»**, второй режим — **«Список»** (понятнее, чем дважды «таблица»).
- **Админка «Меню»:** позиции без категории попадали в группу «Прочее», но она не входила в `menuCategories` — карточки не строились (пустой экран при живых данных). Исправлена сборка `menuDisplayGroups`; в фильтрах учитывается «Прочее»; при ошибке API показывается сообщение.
- **Удаление демо-данных:** явные SQL `DELETE` (чаты → брони → заказы → пользователи), очистка Redis/InMemory-ключей сессии по демо-номерам; для SQLite при каждом подключении `PRAGMA foreign_keys=ON`.
- **UI удаления демо:** модальное окно с чекбоксом и тостом вместо `confirm()`.

---

## Ядро (AI + диалог)

- AI на OpenAI (structured output по Pydantic-схеме; Whisper для голоса)
- System Prompt, RAG — меню из БД в контексте
- История в Redis (до 20 сообщений, TTL 24 ч)
- Intents: `order`, `book`, `faq`, `escalate`
- Retry до 2 попыток + fallback на `escalate`
- **Rate limiting** по телефону (Redis / in-memory)

## Заказы

- Цикл: клиент → AI → валидация → DRAFT → подтверждение → confirmed → iiko → `sent_to_iiko`
- Валидация по `MenuItem`, fuzzy matching (difflib, cutoff 0.6)
- `iiko_item_id`, модификаторы, исключения ингредиентов
- Защита от пустого заказа (все позиции неизвестны → DRAFT не создаётся)
- `OrderStatus`: DRAFT → CONFIRMED → SENT_TO_IIKO → COMPLETED / CANCELLED

## State machine

- Состояния: `CHATTING`, `CONFIRMING_ORDER`, `CONFIRMING_BOOKING`, `HUMAN_MODE`
- Pending order и pending booking в Redis (TTL 24 ч)
- Подтверждение заказа и брони словами «Да» / «Нет» (ru/en)

## Бронирование

- Парсинг даты/времени/гостей через AI
- Flow: черновик → резюме → подтверждение клиентом → confirmed / cancelled

## Human override

- Takeover / release / `send_message` от оператора
- Логи чата для user / assistant / operator

## Интеграция iiko

- Токен, номенклатура, создание доставки, стоп-листы
- Синхронизация меню bulk, фоновая синхронизация стоп-листов (~15 мин)

## Интеграция WhatsApp

- Верификация webhook, POST → BackgroundTasks
- Текстовые сообщения + **template messages** (`send_template`)
- Retry, режим разработки без токена (лог в консоль)

## Real-time (WebSocket)

- Redis Pub/Sub, in-memory fallback
- Эндпоинт `/api/admin/ws`, события: `new_message`, `order_updated`, `human_needed`, `state_changed`
- Публикация из вебхука, intent router, админских действий

## Админ-панель

- Alpine.js + Tailwind + Chart.js
- Live-обновления, канбан заказов, live-чаты, алерты `human_needed` (звук)
- Дашборд, аналитика, меню, синхронизация, test-bot
- **Вход:** форма + cookie-сессия; REST и демо-API с `credentials`; WebSocket с подписанным `ws_token`

## База данных

- SQLAlchemy 2.0 async, PostgreSQL / SQLite (`DB_MODE`)
- Модели: **Organization**, User, Order, ChatLog, Booking, MenuItem
- Мультитенантность: `organization_id` на User, Order, Booking, MenuItem
- Redis + in-memory fallback; `seed.py` с демо-данными

## Обработка ошибок

- AI / iiko / WhatsApp: retry и логирование
- Webhook: защита от каскадных 500
- `process_message`: глобальный try/except

## Логирование и мониторинг

- Консоль + ротируемые файлы + отдельный error-log
- Sentry по `SENTRY_DSN` (опционально)

## Тесты

- `tests/test_order_logic.py` — `validate_order`, меню, контекст
- `tests/test_ai_brain.py` — мок OpenAI, fallback, retry
- `tests/test_rate_limiter.py` — лимиты
- Фикстуры: SQLite in-memory, `db_with_menu`

## Инфраструктура

- Dockerfile, `docker-compose.yml`, **`docker-compose.prod.yml`** (Traefik, Let's Encrypt)
- `DEPLOY_GUIDE.md`, `.env.example`, `requirements.txt` (+ pytest)
- Маршруты `/` и `/admin`

## Голосовой AI (v2.0 — заготовка)

- Модуль `telephony.py`: контракты под Twilio / STT / TTS, без боевого эндпоинта

## Оптимизации

- Один запрос меню на цикл сообщения
- Аналитика на уровне SQL
- Bulk load при синхронизации из iiko
- Pub/Sub вместо polling в админке

