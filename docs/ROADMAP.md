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

- [x] **Data leak меню между организациями:** `load_available_menu` — параметр `organization_id` стал обязательным (убран дефолт `None`). Без него функция возвращала меню всех организаций. Все callers уже передавали аргумент явно.
- [x] **WhatsApp inbound dedupe durable handoff:** убран ранний Redis‑preclaim (`_dedupe_whatsapp_message` на входе вебхука). Единственная идемпотентность — через `try_start_whatsapp_inbound_in_db` в `process_with_retry`. Redis SET до DB‑commit мог потерять сообщение при сбое между шагами.
- [x] **OpenAI timeout masking → retry:** transient‑ошибки (`RateLimitError | APIConnectionError | APITimeoutError | APIError 429/5xx`) превращаются в `TransientAiError` в [`app/services/ai_engine/openai_p.py:267-271`](app/services/ai_engine/openai_p.py); диспетчер [`app/services/ai_brain.py:247`](app/services/ai_brain.py) пробрасывает их (`raise_on_transient=True` по умолчанию); внешний цикл `_enqueue_processing` ([`app/api/webhooks.py:790-813`](app/api/webhooks.py), `MAX_RETRIES=3`, exp back‑off) делает повтор. Аналогично в `gemini_p.py`.
- [x] **Source of Truth для dialog state:** добавлен хелпер `_transition_state()` в `webhooks.py` — сначала пишет состояние в БД (`update_user_session_fields_in_db` + commit), затем обновляет Redis-кэш. Заменены 4 Redis-only вызова `set_user_state`: после prepay alert, вход в CONFIRMING_ORDER, и два перехода CONFIRMING→CHATTING.
- [x] **Operator outbound: ChatLog до отправки в WhatsApp:** `admin_send_message` и `resend_failed_chat_message` уже корректно сохраняют `ChatLog(delivery_status='sending')` + commit до вызова `send_message()`. Паттерн соблюдён.
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

- [ ] **Compact Kanban (high-density)**: переключатель **Normal / Compact** на канбане заказов; в Compact — карточки одной строкой (название, сумма, телефон‑last4, статус‑точка), теги типа способа доставки/оплаты — иконками, без фоновых плашек. Хранить выбор в `localStorage` пер‑пользователя. Цель: ≥ 8 заказов в колонке без скролла на 1440px против текущих 2–3. Файлы: [`_tab_orders.html`](app/templates/screens/_tab_orders.html), [`admin-app.js`](app/static/js/admin-app.js) (новый флаг `kanbanDensity`), `src/css/admin-input.css` (`ds-kanban-card--compact`).
- [ ] **Tenant color stripe**: тонкая полоса (`2–3px`) сверху хедера и/или сайдбара, цвет — `Organization.brand_color_hex`. Визуальный якорь для владельцев сети. Делается переменной `--tenant-accent` в [`admin-brand-tokens.js`](app/static/js/admin-brand-tokens.js) и `box-shadow: inset 0 2px 0 var(--tenant-accent)` на шапке. Отдельный кейс — экран‑заглушка при свитче филиала (`POST /api/admin/auth/select-org`): прежнюю шапку гасить до прихода нового брендинга, чтобы оператор не отправил заказ «не в тот ресторан».
- [ ] **Right Context Panel в чатах**: третья колонка справа от переписки в [`_tab_chats.html`](app/templates/screens/_tab_chats.html) — профиль гостя (имя, телефон, кол‑во заказов, средний чек/LTV), активный черновик/pending‑заказ, активная бронь, последняя эскалация. Данные уже доступны через существующие эндпоинты `/api/admin/orders`, `/api/admin/bookings` + `User.meta_json`; на фронте — секция в `_app_shell` без отдельного API. На `<lg` — выезжает как drawer.
- [ ] **AI Confidence на заказе**: если `validate_order` нашёл позиции через fuzzy (`difflib < 0.8`) или адрес не верифицирован — карточка/строка заказа подсвечивается жёлтым бордером + бейдж `AI сомневается, проверьте`. Для этого вернуть наружу из [`order_logic.py`](app/services/order_logic.py) флаг `low_confidence` (или массив проблемных полей) и сохранять его в `order_meta.confidence`. UI — новый `_status_badge` вариант `warning-soft`.
- [ ] **AI Snooze with timer**: в чате/диалоге заменить голую кнопку «оператор» на меню «Отключить ИИ на 30 мин / 2 ч / до завтра / навсегда». Backend — поле `User.ai_snoozed_until: datetime | null` (миграция, фильтр в `intent_router`); UI — выпадашка из шапки чата + индикатор «🟣 ИИ выключен до 19:30». По истечении — авто‑возврат к ИИ без действия оператора.
- [ ] **Bulk‑actions в стоп‑листе**: чекбоксы у позиций + sticky‑панель действий внизу таблицы (`В стоп / Снять со стопа / Сменить категорию`). На мобильном — long‑press → multi‑select. Текущая API уже умеет `PATCH /api/admin/menu/{id}` поштучно — добавить батч `POST /api/admin/menu/bulk-stoplist`. Файл UI: [`_tab_menu.html`](app/templates/screens/_tab_menu.html).
- [ ] **Skeletons + relative time**: заменить прогресс‑полоски на skeleton‑строки на тяжёлых вкладках (заказы/аналитика/чаты) через существующий [`_skeleton.html`](app/templates/components/_skeleton.html); добавить `fmt.timeAgo(date)` («3 мин назад») и применить в живых лентах (заказы, чаты, инциденты), оставив абсолютное время в tooltip.

- [ ] **Refresh `docs/ui/baseline/` и `docs/ui/mobile-review/`**: текущие PNG сделаны до Phase U5–U7 и не отражают `ds-card`/`ds-modal-panel`/Compact‑контролы. План: переснять серию через [`scripts/run_admin_lighthouse.mjs`](scripts/run_admin_lighthouse.mjs)/Playwright **или** через MCP `playwright`/`chrome-devtools`; старые скрины переложить в `docs/ui/baseline/2025_q4/` (архив с README — дата + последний коммит); в [`docs/UI_DESIGN_SYSTEM.md`](docs/UI_DESIGN_SYSTEM.md) обновить врезки. После — внешние UX‑ревью перестанут судить по устаревшему UI.

## 🟢 P2: Развитие (Growth)

- [x] **E1 хвост (платежи):** `parsed.raw` провайдера теперь сохраняется в `PaymentTransaction.provider_payload_json` — проходит через `_run_payment_webhook` → `apply_payment_webhook` → `_upsert_payment_transaction`. Верификация подписей для Kaspi (HMAC-SHA256) и Freedom Pay (MD5 + X-Freedom-Signature) реализована в адаптерах.
- [x] **E14 авто‑ссылка на оплату:** генерация ссылок в `intent_router` для предоплаты.
- [ ] **E8 WhatsApp интерактив:** кнопки Meta templates + (опционально) картинка‑чек.
- [ ] **Telegram оператор‑бот:** управление диалогами из Telegram (эскалации/ответы/уведомления).
- [x] **Экстренное закрытие ресторана:** причина + длительность паузы + корректное поведение вне рабочего времени.

## ⚪ P3: Бэклог и R&D

- [ ] **E11 Strategy Engine:** вынести upsell-логику из промпта в Python‑правила.
- [ ] **E12 RAG по меню:** семантический поиск для больших каталогов.
- [ ] **BI по iiko:** продажи по времени суток и автоподстройка upsell.


## P4: AI Operations / Intelligence

- [x] **Restaurant Intelligence MVP:** admin `AI-аналитик` tab + `POST /api/admin/intelligence/query` for revenue/orders questions.
- [x] **Unified analytics/event pipeline foundation:** durable `SystemEvent` stream and `emit_system_event()`.
- [x] **AI auto-insights MVP:** `OperationalInsight` with admin-visible revenue/order/cancellation insights.
- [x] **Restaurant state snapshots:** `RestaurantStateSnapshot` and `GET /api/admin/intelligence/digital-twin`.
- [x] **Digital Twin MVP:** separate admin tab and operator-capacity simulation engine.
- [ ] Predictive analytics: demand, cancellations, overload forecasting.
- [ ] SLA monitor: response-time degradation detection.
- [ ] Operator efficiency analytics.
- [ ] AI incident detection: abnormal spikes, failures, stop-list impact.
- [ ] AI business recommendations: upsell/menu/operator optimization.
- [ ] Voice AI operator: realtime Twilio Media Streams / OpenAI Realtime or LiveKit.
- [x] Payment links: provider abstraction for creating payment URLs, not only webhook intake.
- [ ] Multi-tenant security audit: verify `organization_id` isolation across all services/queries.
