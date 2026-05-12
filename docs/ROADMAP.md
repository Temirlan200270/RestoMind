# RestoMind — Roadmap & Single Source of Truth

Единственный файл для отслеживания статусов задач, багов и технического долга.

**Правило для ИИ:** при завершении задачи ставить галочку `[x]` здесь и делать запись в `CHANGELOG.md`. Другие “планы/трекеры” больше не обновляем.

Временные мини‑родмапы/чеклисты на 1–2 недели — в `docs/sprints/` (но статусы задач всё равно только здесь).

## 🔴 P0: Критический техдолг и баги (делать сейчас)

> Перенесено из бывшего `problems.md` (2026‑05): потенциальные data leaks, потеря/дубли сообщений, рассинхроны state и гонки UI.

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
  - [x] Вторая фаза — «ленивый DOM»: тяжёлые табы (`_tab_chats.html`, `_tab_orders.html`, блок `_tab_settings_*`) монтируются после первого визита (`lazyTabMount` + `template x-if` в [`admin.html`](app/templates/admin.html), [`admin-app.js`](app/static/js/admin-app.js)). Метрики Lighthouse: опционально `npm run lh:admin` до/после.

## 🟡 P1: Ближайший спринт (Core SaaS)

- [x] **E0.1: раскол `_monolith.py` завершён.** Все роуты вынесены в подмодули: [`orders.py`](app/api/admin/orders.py), [`menu.py`](app/api/admin/menu.py), [`organization.py`](app/api/admin/organization.py), [`rules.py`](app/api/admin/rules.py), [`analytics.py`](app/api/admin/analytics.py) + ранее: `menu_bulk`, `knowledge`, `branding`, `bookings`, `customers`, `chats`, `system`, `intelligence`, `marketing`. Дублирующиеся роуты удалены из `_monolith.py` (5908 → **1199 строк**); split-роутеры зарегистрированы в `main.py` напрямую. `app/api/admin/__init__.py` переключён на импорты из split-модулей. Монолит содержит только: auth, WS, demo, settings/export, `_check_mixed_payment_split`.
- [x] **E2.2 Branding (backend):** [`Tenant.brand_name`/`brand_color_hex`/`brand_logo_url`](app/db/models.py) + миграция [`20260511_e22_tenant_branding`](alembic/versions/20260511_e22_tenant_branding.py); модуль [`app/api/admin/branding.py`](app/api/admin/branding.py) — `GET /api/admin/branding`, `PATCH /api/admin/branding` (HEX-валидация, тримминг имени), `POST /api/admin/branding/logo` (PNG/JPEG ≤ 1 МБ, сохранение в `app/static/uploads/branding/tenant-<id>.<ext>`, cache-buster в URL). `GET /api/admin/auth/me → branding` читает данные из `Tenant` (контракт совместим с UI). Регресс: [`tests/test_admin_branding.py`](tests/test_admin_branding.py).
- [x] **E2.3 Billing (минимум):** `Tenant.plan_status`, таблица `billing_usage_daily`, ежедневный rollup (ARQ cron в [`app/worker.py`](app/worker.py)); блокировка login/`auth`/select-org и ранний выход WhatsApp webhook при `plan_status=suspended`; опциональное поле `billing_blocked` в `GET /auth/me`. Миграция [`20260512_e23_billing_minimal`](alembic/versions/20260512_e23_billing_minimal.py). Полноценный Stripe/лимиты по тарифу — вне scope.
- [x] **E5 ARQ-only:** убран fallback на `BackgroundTasks` в [`app/services/task_queue.py`](app/services/task_queue.py); в `APP_ENV=production|staging` старт web-процесса проверяет Redis+ARQ; worker обязателен в проде. Web enqueue и [`WorkerSettings`](app/worker.py) используют один `ARQ_QUEUE_NAME` (`restomind` по умолчанию).
- [x] **E5 диагностика очереди (light):** `GET /api/admin/system/task-queue-health` ([`app/api/admin/system.py`](app/api/admin/system.py)) + хелпер [`app/services/task_queue_health.py`](app/services/task_queue_health.py) — структурированный статус Redis/ARQ/worker (heartbeat по `<queue>:health-check`). Структурный лог `event=task_queue_enqueue` на каждый enqueue в [`app/services/task_queue.py`](app/services/task_queue.py).

## 🟠 P1.5: UX Density & AI Trust

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
- [x] **Onboarding / coach‑marks внутри админки** (Wishlist Темира #15): первый вход (или `?first_run=1`) — пошаговая подсветка ключевых зон (Inbox, Orders, Settings → Бот/ИИ, Brand, Knowledge), хранение прогресса в `localStorage` пер‑пользователю. Синхронизация в `User.meta_json.tour_completed_at` и дополнительные `?`‑тултипы у тяжёлых полей не делались и остаются отдельным улучшением; новых JS‑либ нет. Публичная `onboarding.html` для регистрации остаётся как есть.

- [ ] **Refresh `docs/ui/baseline/` и `docs/ui/mobile-review/`**: текущие PNG сделаны до Phase U5–U7 и не отражают `ds-card`/`ds-modal-panel`/Compact‑контролы. План: переснять серию через [`scripts/run_admin_lighthouse.mjs`](scripts/run_admin_lighthouse.mjs)/Playwright **или** через MCP `playwright`/`chrome-devtools`; старые скрины переложить в `docs/ui/baseline/2025_q4/` (архив с README — дата + последний коммит); в [`docs/UI_DESIGN_SYSTEM.md`](docs/UI_DESIGN_SYSTEM.md) обновить врезки. После — внешние UX‑ревью перестанут судить по устаревшему UI.

## 🟢 P2: Развитие (Growth)

- [x] **E1 хвост (платежи):** HMAC-SHA256/MD5 верификация для Freedom Pay (`freedom_pay.py` — MD5 pg_sig + FreedomPayInitiator) и Kaspi Pay (`kaspi.py` — HMAC-SHA256, `sha256=` prefix); per-org `payment_config_json` (миграции `20260509_payment_tx_config` + `20260510_org_pay_cfg_json`); UI CRUD в настройках (`_tab_settings_restaurant.html`, `_tab_settings_connections.html`). Остаток: уточнить заголовки подписи по актуальным докам провайдеров + `E14` генерация ссылок на оплату.
- [x] **E14 авто‑ссылка на оплату (генерация payment URL / deep link):** `CloudPaymentsInitiator.create_payment()` генерирует ссылку через `/payments/link/create`; `intent_router` задаёт `RouteResult.cta_url` при `requires_big_order_prepay`; WhatsApp отправляет CTA-кнопку (`send_cta_url_button`) отдельно от текста заказа.
- [x] **E8 WhatsApp интерактив:** `send_interactive_buttons()` отправляет `interactive/button` (до 3 кнопок) для подтверждения/отмены заказа; `receive_message()` в `webhooks.py` раскрывает `button_reply` в `"да"` / `"нет"`. `RouteResult.interactive_buttons` управляет выбором транспорта (кнопки vs CTA vs текст).
- [x] **Telegram оператор‑бот:** `app/api/telegram_webhook.py` (`POST /api/telegram/webhook`, `X-Telegram-Bot-Api-Secret-Token`); `app/services/telegram_operator.py` — relay оператора (`reply:{phone}:{org_id}` callback, Redis TTL 30 мин, запись `ChatLog`); кнопка «📩 Ответить клиенту» в алерте эскалации + `/dialogs` команда. _Wishlist Темира #12._
- [x] **Экстренное закрытие ресторана:** причина + длительность паузы + корректное поведение вне рабочего времени.

- [x] **Ночные предзаказы + Telegram «на смене»** (Wishlist Темира #20): когда гость пишет вне рабочих часов (`time_context.py` уже умеет считать) — бот принимает заявку как **предзаказ** (не отправляя в iiko), кладёт в новую таблицу `night_preorders` (или `Order.kind='preorder'` + `scheduled_for`). Telegram оператор‑бот (см. выше) утром шлёт **сводку ночных предзаказов** в чат смены и ждёт кнопку «🟢 Я на смене» от оператора → после нажатия бот переключает все ночные предзаказы в обычный поток подтверждения. Супер‑админ получает алерт, если за N минут после открытия никто не нажал «на смене».
- [x] **Авто‑сбор отзывов после заказа** (Wishlist Темира R3): через N минут после `OrderStatus.COMPLETED` (или `SENT_TO_IIKO` + offset) — WhatsApp шаблон «Как вам всё прошло?» с кнопками 👍 / 👎. 👍 → ссылка на отзыв в **2GIS** (`Organization.review_url_2gis`), 👎 → запись `customer_feedback` + Telegram‑алерт владельцу/админу с цитатой и `phone_last4`. Никаких новых LLM‑вызовов, всё на template‑messages + `intent_router` post‑hook.
- [x] **Горячая рассылка по клиентам + бонусная система** (Wishlist Темира #19): целевая рассылка через WhatsApp template_messages по сегментам — «давно не заказывали (>30 дней)», «частые гости», «по событию» (день рождения, праздник). Отдельный экран в админке (черновик → preview → send), per‑org rate‑limit, opt‑out по `User.marketing_opt_out`, лог в `marketing_blasts` + per‑message статус доставки. Бонусная система — отдельная таблица `loyalty_balance` + начисление через webhook iiko или вручную; в WhatsApp бот умеет отвечать «у вас N баллов» через `intent: faq` enrichment. **Перед стартом** — юридическая проверка: WABA маркетинг‑правила Meta + Закон РК «О персональных данных».

## ⚪ P3: Бэклог и R&D

- [ ] **E11 Strategy Engine (продолжение):** расширить Python‑правила (ещё эвристики из промпта / приоритеты по часам); опционально дергать движок из дополнительных точек после A/B. **MVP сделан:** `app/services/sales_strategy_engine.py` (лимит `recommendation_trace`), метрики этапов `rm_stage_ms`, кэш контекста ресторана (расписание + Redis меню).
- [ ] **E12 RAG по меню:** семантический поиск для больших каталогов.
- [ ] **BI по iiko:** продажи по времени суток и автоподстройка upsell. _Wishlist Темира #16._
- [ ] **Авто‑рассылка из iiko по клиентам** (Wishlist Темира R1): забирать клиентскую базу из iiko (телефоны, история заказов, сегменты), синхронизировать в `User`/`marketing_segment`, и далее через тот же канал рассылок (см. P2 «Горячая рассылка»). Зависимость: завершённый клиент iiko + соглашения по PII.
- [ ] **VIP‑кейс: отдельный сайт/мини‑приложение для премиум‑заведений** (Wishlist Темира R2): white‑label фронт (отдельный поддомен per‑tenant) с меню, бронированием, личным кабинетом гостя. Архитектурно — отдельный Next.js/Astro фронт поверх существующего API; оценить ROI до начала реализации.
- [ ] **KPI‑центр официантов из iiko** (Wishlist Темира R4): забирать из iiko персональные данные по заказам (`waiter_id`, средний чек, кол‑во гостей, отмены, время обслуживания, продажи по позициям) → агрегировать в `waiter_kpi_daily` → экран в админке с рейтингом, фильтром по дате/смене, экспорт. Совместим с пунктом «BI по iiko» — общий ETL.


## P4: AI Operations / Intelligence

- [x] **Restaurant Intelligence MVP:** admin `AI-аналитик` tab + `POST /api/admin/intelligence/query` for revenue/orders questions.
- [x] **Unified analytics/event pipeline foundation:** durable `SystemEvent` stream and `emit_system_event()`.
- [x] **AI auto-insights MVP:** `OperationalInsight` with admin-visible revenue/order/cancellation insights.
- [x] **Restaurant state snapshots:** `RestaurantStateSnapshot` and `GET /api/admin/intelligence/digital-twin`.
- [x] **Digital Twin MVP:** separate admin tab and operator-capacity simulation engine.
- [ ] Predictive analytics: demand, cancellations, overload forecasting.
- [x] **Inbound latency baselines + SLA monitor:** `PipelineLatencyLog` (модель + миграция `20260513_pipeline_latency`); `app/services/pipeline_latency.py` — `schedule_log_pipeline_latency` (fire-and-forget), `get_latency_summary` (p50/p95/max per stage), `check_sla_thresholds` (emit `SystemEvent("sla_violation")`); `GET /api/admin/intelligence/latency`. _Wishlist Темира #18 (часть)._
- [x] **Operator efficiency analytics:** `app/services/operator_efficiency.py` — `escalation_count/rate_pct`, `avg_first_response_min`, `human_mode_sessions`, `operator_recovery_rate_pct`; `GET /api/admin/intelligence/operator-efficiency`. _Wishlist Темира #18 (часть)._
- [x] **AI incident detection:** `detect_ai_incidents(db, org_id)` в `app/services/intelligence.py` — token spike (>3× 7d avg), error spike (>15%), latency spike (>1.5× SLA); вызов в `list_insights()` (lazy). `AiUsageLog.error_count` + `p95_latency_ms` (миграция `20260513_ai_usage_errors`). _Wishlist Темира #18 (часть)._
- [x] **AI business recommendations:** `BusinessRecommendation` (модель + миграция `20260513_biz_recommendations`); `app/services/recommendations.py` — `generate_recommendations` (product_boost / pricing_adj / geo_expansion / stoplist_impact, детерминированно без LLM); фоновый цикл UTC 04:00; `GET/POST /api/admin/intelligence/recommendations`, `PATCH …/{id}`.
- [ ] Voice AI operator: realtime Twilio Media Streams / OpenAI Realtime or LiveKit.
- [x] **Multi-tenant security audit:** `tests/test_multitenant_isolation.py` — 9 тестов изоляции по `organization_id` (Order, ChatLog, MenuItem, EscalationEvent, OperationalInsight, AiUsageLog, PipelineLatencyLog, BusinessRecommendation, cross-org phone); отчёт `docs/SECURITY_AUDIT.md`.

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
| R1 | Авто‑рассылка из iiko по клиентам | ❌ | **P3** «Авто‑рассылка из iiko по клиентам» |
| R2 | VIP сайт/приложение | ❌ | **P3** «VIP‑кейс: отдельный сайт/мини‑приложение» |
| R3 | Авто‑сбор отзывов после заказа | ✅ | `CustomerFeedback` + `send_review_request` ARQ + 👍/👎 кнопки в WhatsApp (**P2** выполнено) |
| R4 | KPI‑центр официантов из iiko | ❌ | **P3** «KPI‑центр официантов из iiko» |
