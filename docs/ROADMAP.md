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
- [ ] **Operator outbound: отправка наружу только после фиксации ChatLog:** сначала записать `ChatLog(delivery_status='sending')` + commit, потом отправить в WhatsApp, потом обновить `provider_message_id`/статус. См. `app/api/admin/_monolith.py` (старое).
- [x] **UI: race-condition в заказах (REST vs WS):** в [`app/static/js/admin-app.js:6159-6210`](app/static/js/admin-app.js) реализован seq‑guard (`_ordersLoadSeq` отбрасывает устаревшие ответы REST) и merge по `row_version` (REST не перетирает более свежие WS‑данные).
- [ ] **Admin UI refactor (split done, lazy DOM pending):**
  - [x] Первая фаза — статичный split: [`app/templates/admin.html`](app/templates/admin.html) сократился до ~75 строк и собирается из 27 экранов в [`app/templates/screens/`](app/templates/screens/) через `{% include %}` (login, sidebar, header, banners, 11 табов, 8 экранов настроек, modals, bottom_nav).
  - [ ] Вторая фаза — «ленивый DOM»: обернуть тяжёлые табы (`_tab_chats.html`, `_tab_orders.html`, `_tab_settings_*`) в `x-if="currentTab === '...'"`/mount‑on‑demand; снять метрики через `npm run lh:admin` до/после.

## 🟡 P1: Ближайший спринт (Core SaaS)

- [ ] **E0.1: добить раскол временного `_monolith.py`** на подмодули `app/api/admin/` (цель: файлы ≤ ~1500 строк, без изменения поведения).
- [ ] **E2.2 Branding (backend):** `Tenant.brand_*` + `GET/PATCH /api/admin/branding` + `POST /api/admin/branding/logo` (UI уже готов/частично готов).
- [ ] **E2.3 Billing (минимум):** `Tenant.plan_status`, `billing_usage`, ежедневный rollup; блокировка login/вебхуков при `suspended`.
- [ ] **E5 ARQ-only:** убрать fallback на `BackgroundTasks` в `app/services/task_queue.py`, worker как обязательная часть прода.

## 🟠 P1.5: UX Density & AI Trust

> Источник: внешний UX-аудит (2026‑05). Сюда попало только то, что прошло наш фильтр «реально не сделано и осмысленно для оператора в час пик». Архитектура (Jinja + Alpine + Tailwind + WS) не меняется, на React/HTMX не переходим. Дизайн-система — `docs/UI_DESIGN_SYSTEM.md` секции «Density modes» и «AI in UI».

- [x] **P1.5.0: IA collapse + Unified «Требует внимания»**: сайдбар сжимаем до 4+4 пунктов (**Операции** / **Управление**); новый экран [`_tab_inbox.html`](app/templates/screens/_tab_inbox.html) объединяет [`operator_queue`](app/templates/screens/_tab_operator_queue.html) (таб **От клиентов**) и [`incidents`](app/templates/screens/_tab_incidents.html) (таб **Системные**); новый [`_tab_ai_center.html`](app/templates/screens/_tab_ai_center.html) объединяет [`ai_value`](app/templates/screens/_tab_ai_value.html) / [`intelligence`](app/templates/screens/_tab_intelligence.html) / [`digital_twin`](app/templates/screens/_tab_digital_twin.html); [`analytics`](app/templates/screens/_tab_analytics.html) уезжает внутрь [`dashboard`](app/templates/screens/_tab_dashboard.html) под‑табом **Главная / Аналитика**. Все старые hash-URL редиректят на новые.

- [x] **Compact Kanban (high-density)**: переключатель **Normal / Compact** на канбане заказов; в Compact — карточки одной строкой (название, сумма, телефон‑last4, статус‑точка), теги типа способа доставки/оплаты — иконками, без фоновых плашек. Хранить выбор в `localStorage` пер‑пользователя. Цель: ≥ 8 заказов в колонке без скролла на 1440px против текущих 2–3. Файлы: [`_tab_orders.html`](app/templates/screens/_tab_orders.html), [`admin-app.js`](app/static/js/admin-app.js) (флаг `kanbanDensity`), `src/css/admin-input.css` (`ds-kanban-card--compact`).
- [x] **Tenant color stripe**: тонкая полоса (`2–3px`) сверху хедера и/или сайдбара, цвет — `Organization.brand_color_hex`. Визуальный якорь для владельцев сети. Переменная `--tenant-accent` в [`admin-brand-tokens.js`](app/static/js/admin-brand-tokens.js) (`restoMindApplyTenantAccent`), `box-shadow: inset 0 2px 0` на шапке и сайдбаре; подключение токенов в [`admin.html`](app/templates/admin.html). При свитче филиала (`POST /api/admin/auth/select-org`) хром гасится (`orgSwitchChromeDimmed` + `rm-chrome-org-switch`) до завершения перезагрузки профиля и данных вкладок.
- [ ] **Right Context Panel в чатах**: третья колонка справа от переписки в [`_tab_chats.html`](app/templates/screens/_tab_chats.html) — профиль гостя (имя, телефон, кол‑во заказов, средний чек/LTV), активный черновик/pending‑заказ, активная бронь, последняя эскалация. Данные уже доступны через существующие эндпоинты `/api/admin/orders`, `/api/admin/bookings` + `User.meta_json`; на фронте — секция в `_app_shell` без отдельного API. На `<lg` — выезжает как drawer.
- [ ] **AI Confidence на заказе**: если `validate_order` нашёл позиции через fuzzy (`difflib < 0.8`) или адрес не верифицирован — карточка/строка заказа подсвечивается жёлтым бордером + бейдж `AI сомневается, проверьте`. Для этого вернуть наружу из [`order_logic.py`](app/services/order_logic.py) флаг `low_confidence` (или массив проблемных полей) и сохранять его в `order_meta.confidence`. UI — новый `_status_badge` вариант `warning-soft`.
- [ ] **AI Snooze with timer**: в чате/диалоге заменить голую кнопку «оператор» на меню «Отключить ИИ на 30 мин / 2 ч / до завтра / навсегда». Backend — поле `User.ai_snoozed_until: datetime | null` (миграция, фильтр в `intent_router`); UI — выпадашка из шапки чата + индикатор «🟣 ИИ выключен до 19:30». По истечении — авто‑возврат к ИИ без действия оператора.
- [x] **Bulk‑actions в стоп‑листе**: чекбоксы + sticky‑панель (`В стоп / Снять со стопа / Сменить раздел`); long‑press на карточке каталога → multi‑select; батч [`POST /api/admin/menu/bulk-stoplist`](app/api/admin/menu_bulk.py) (скоуп по сессии филиала, `failed[]` per‑item). UI: [`_tab_menu.html`](app/templates/screens/_tab_menu.html), [`admin-app.js`](app/static/js/admin-app.js) (секции `// bulk-stoplist`).
- [x] **Skeletons + relative time**: skeleton‑строки на тяжёлых вкладках (заказы, чаты, аналитика, дашборд‑лента, inbox) через [`_skeleton.html`](app/templates/components/_skeleton.html); `fmt.timeAgo` / `fmt.dateTime` в [`admin-app.js`](app/static/js/admin-app.js) — относительное время в лентах и списках (заказы, чаты, инциденты, операторская очередь, события дашборда), абсолютное в `title`. Стили `.ds-skeleton-line` в `src/css/admin-input.css`.

- [ ] **Failed‑бейдж сообщений в карточке/модалке заказа** (Wishlist Темира #3): сейчас `delivery_status === 'failed'` подсвечивается только в `_tab_chats.html:206`. Нужно в [`_tab_orders.html`](app/templates/screens/_tab_orders.html) (карточка/модалка заказа) показывать индикатор «N сообщений не доставлено в WhatsApp» с переходом в диалог гостя. Источник — `chat_logs.delivery_status` за пользователя, в окне ±1 час от заказа; рядом с уже существующим `iiko_last_error`.
- [ ] **Кастомная модалка удаления заказа с превью** (Wishlist Темира #10): отдельная `ds-modal-panel` для удаления — № заказа, сумма, клиент, тип/оплата, причина (опц.), кнопка с задержкой 1 c. Заменить вызов общего `uiConfirm` в `app/static/js/admin-app.js` (handlerы удаления заказа) на новую модалку. Цель — снизить шанс случайного удаления у оператора в час пик.
- [ ] **Onboarding / coach‑marks внутри админки** (Wishlist Темира #15): первый вход (или `?first_run=1`) — пошаговая подсветка ключевых зон (Inbox, Orders, Settings → Бот/ИИ, Brand, Knowledge), хранение прогресса в `localStorage` пер‑пользователю + опционально в `User.meta_json.tour_completed_at`. Подсказки‑тултипы (`?` рядом с тяжёлыми полями) на основе уже существующих макросов; никаких новых JS‑либ. Публичная `onboarding.html` для регистрации остаётся как есть.

- [ ] **Refresh `docs/ui/baseline/` и `docs/ui/mobile-review/`**: текущие PNG сделаны до Phase U5–U7 и не отражают `ds-card`/`ds-modal-panel`/Compact‑контролы. План: переснять серию через [`scripts/run_admin_lighthouse.mjs`](scripts/run_admin_lighthouse.mjs)/Playwright **или** через MCP `playwright`/`chrome-devtools`; старые скрины переложить в `docs/ui/baseline/2025_q4/` (архив с README — дата + последний коммит); в [`docs/UI_DESIGN_SYSTEM.md`](docs/UI_DESIGN_SYSTEM.md) обновить врезки. После — внешние UX‑ревью перестанут судить по устаревшему UI.

## 🟢 P2: Развитие (Growth)

- [x] **E1 хвост (платежи):** HMAC-SHA256/MD5 верификация для Freedom Pay (`freedom_pay.py` — MD5 pg_sig + FreedomPayInitiator) и Kaspi Pay (`kaspi.py` — HMAC-SHA256, `sha256=` prefix); per-org `payment_config_json` (миграции `20260509_payment_tx_config` + `20260510_org_pay_cfg_json`); UI CRUD в настройках (`_tab_settings_restaurant.html`, `_tab_settings_connections.html`). Остаток: уточнить заголовки подписи по актуальным докам провайдеров + `E14` генерация ссылок на оплату.
- [ ] **E14 авто‑ссылка на оплату:** генерация ссылок в `intent_router` для предоплаты.
- [ ] **E8 WhatsApp интерактив:** кнопки Meta templates + (опционально) картинка‑чек.
- [ ] **Telegram оператор‑бот:** управление диалогами из Telegram (эскалации/ответы/уведомления). _Wishlist Темира #12._
- [x] **Экстренное закрытие ресторана:** причина + длительность паузы + корректное поведение вне рабочего времени.

- [ ] **Ночные предзаказы + Telegram «на смене»** (Wishlist Темира #20): когда гость пишет вне рабочих часов (`time_context.py` уже умеет считать) — бот принимает заявку как **предзаказ** (не отправляя в iiko), кладёт в новую таблицу `night_preorders` (или `Order.kind='preorder'` + `scheduled_for`). Telegram оператор‑бот (см. выше) утром шлёт **сводку ночных предзаказов** в чат смены и ждёт кнопку «🟢 Я на смене» от оператора → после нажатия бот переключает все ночные предзаказы в обычный поток подтверждения. Супер‑админ получает алерт, если за N минут после открытия никто не нажал «на смене».
- [ ] **Авто‑сбор отзывов после заказа** (Wishlist Темира R3): через N минут после `OrderStatus.COMPLETED` (или `SENT_TO_IIKO` + offset) — WhatsApp шаблон «Как вам всё прошло?» с кнопками 👍 / 👎. 👍 → ссылка на отзыв в **2GIS** (`Organization.review_url_2gis`), 👎 → запись `customer_feedback` + Telegram‑алерт владельцу/админу с цитатой и `phone_last4`. Никаких новых LLM‑вызовов, всё на template‑messages + `intent_router` post‑hook.
- [ ] **Горячая рассылка по клиентам + бонусная система** (Wishlist Темира #19): целевая рассылка через WhatsApp template_messages по сегментам — «давно не заказывали (>30 дней)», «частые гости», «по событию» (день рождения, праздник). Отдельный экран в админке (черновик → preview → send), per‑org rate‑limit, opt‑out по `User.marketing_opt_out`, лог в `marketing_blasts` + per‑message статус доставки. Бонусная система — отдельная таблица `loyalty_balance` + начисление через webhook iiko или вручную; в WhatsApp бот умеет отвечать «у вас N баллов» через `intent: faq` enrichment. **Перед стартом** — юридическая проверка: WABA маркетинг‑правила Meta + Закон РК «О персональных данных».

## ⚪ P3: Бэклог и R&D

- [ ] **E11 Strategy Engine:** вынести upsell-логику из промпта в Python‑правила.
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
- [ ] SLA monitor: response-time degradation detection. _Wishlist Темира #18 (часть)._
- [ ] Operator efficiency analytics. _Wishlist Темира #18 (часть)._
- [ ] AI incident detection: abnormal spikes, failures, stop-list impact. _Wishlist Темира #18 (часть)._
- [ ] AI business recommendations: upsell/menu/operator optimization.
- [ ] Voice AI operator: realtime Twilio Media Streams / OpenAI Realtime or LiveKit.
- [ ] Payment links: provider abstraction for creating payment URLs, not only webhook intake.
- [ ] Multi-tenant security audit: verify `organization_id` isolation across all services/queries.

---

## 📥 Wishlist Темира (2026-05) — индекс

Список пожеланий из обратной связи Темира (общий список + дополнительный для RestoMind), сверенный с фактическим состоянием кода. Этот блок — **только индекс**: статусы и реальные задачи живут в P0–P4 выше, здесь просто карта «что есть / чего нет / куда класть».

Легенда: ✅ done · ⚠️ partial · ❌ missing.

| # | Пункт | Статус | Где в roadmap / коде |
|---|---|---|---|
| 1 | Меньше шума в админке | ⚠️ | IA collapse 4+4 ✅ (P1.5.0). Compact Kanban / tenant stripe / skeletons + relative time ✅ (P1.5). Right context panel / AI confidence / AI snooze / bulk‑actions — открыты в **P1.5** |
| 2 | Понятные настройки + новый функционал | ✅ | 8 экранов настроек (`_tab_settings_*`), Phase U4 |
| 3 | Видимый failed‑статус сообщений | ⚠️ | В чатах ✅ (`_tab_chats.html:206`); в заказах ❌ — задача в **P1.5** «Failed‑бейдж сообщений в карточке/модалке заказа» |
| 4 | Кнопка «Выйти» + аутентификация | ✅ | `_header.html:220`, cookie‑session + `ws_token` |
| 5 | Польза от бота для владельца | ✅ | «Вклад ИИ», AI Center, weekly digest (`owner_weekly_digest.py`) |
| 6 | Раздел «Упаковка» | ✅ | Phase U4.5: `scope` item/category/order, миграция `20260507_ui_u45_packaging` |
| 7 | Мобильная адаптация заказов | ⚠️ | snap‑scroll + 44px ✅ (Phase U6); Compact density — в **P1.5** «Compact Kanban» |
| 10 | Модалка удаления заказа | ⚠️ | Только общий `uiConfirm`; кастомная модалка с превью — в **P1.5** |
| 12 | Telegram‑бот оператора / push | ❌ | **P2** «Telegram оператор‑бот» |
| 13 | Унификация под разные заведения | ✅ | `Organization`, `tenant_owner_id`, `select-org`, branding |
| 14 | Рефакторинг админки | ⚠️ | split на `screens/` ✅; Lazy DOM и **E0.1** раскол `_monolith.py` — в **P0/P1** |
| 15 | База знаний разделена + онбординг/туториал | ⚠️ | Профиль/знания разделены ✅; coach‑marks внутри админки — в **P1.5** «Onboarding / coach‑marks» |
| 16 | AI‑анализ продаж по времени из iiko | ⚠️ | Restaurant Intelligence MVP ✅; «BI по iiko: продажи по времени суток» — в **P3** |
| 17 | Эффективные токены, кэш, счётчик | ⚠️ | Счётчик токенов ✅ (P0); semantic‑кэш и оптимизация промптов — отдельной задачей не созданы (см. **P3** «E12 RAG по меню» как смежное) |
| 18 | Анализ услуг общения | ⚠️ | AI Value метрики ✅; SLA monitor / operator efficiency / AI incident detection — в **P4** |
| 19 | Горячая рассылка по клиентам + бонусы | ❌ | **P2** «Горячая рассылка по клиентам + бонусная система» |
| 20 | Вне рабочее время + ночной предзаказ + Telegram «на смене» | ⚠️ | Force‑close ✅; полный сценарий ночных предзаказов и кнопки «на смене» — в **P2** «Ночные предзаказы + Telegram “на смене”» |
| 21 | Экстренное закрытие ресторана | ✅ | `force_closed_until/reason` end‑to‑end (P0/P2) |
| R1 | Авто‑рассылка из iiko по клиентам | ❌ | **P3** «Авто‑рассылка из iiko по клиентам» |
| R2 | VIP сайт/приложение | ❌ | **P3** «VIP‑кейс: отдельный сайт/мини‑приложение» |
| R3 | Авто‑сбор отзывов после заказа | ❌ | **P2** «Авто‑сбор отзывов после заказа» |
| R4 | KPI‑центр официантов из iiko | ❌ | **P3** «KPI‑центр официантов из iiko» |
