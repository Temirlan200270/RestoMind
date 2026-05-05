# Changelog

Заметные изменения проекта **RestoMind**. Формат близок к [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/).

---

## [Unreleased] — 2026-03-20

### Добавлено

- **Админка / UI (Phase U7):** опубликована спецификация дизайн-системы — [`docs/UI_DESIGN_SYSTEM.md`](docs/UI_DESIGN_SYSTEM.md) (принципы, токены `:root`, каталог макросов, IA, anti-patterns, гайд новой страницы, **врезки baseline-скринов** по разделам и настройкам, приёмка a11y + ссылка на Lighthouse); в [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md) добавлен эпик **E-UI / Phases U1–U7**; в [`PARALLEL_AI_PLAN.md`](PARALLEL_AI_PLAN.md) — таблица **«Зоны UI»**; статусы U6/U7 в [`docs/UI_REDESIGN_PLAN.md`](docs/UI_REDESIGN_PLAN.md).
- **Админка / Lighthouse (Phase U6):** скрипт [`scripts/run_admin_lighthouse.mjs`](scripts/run_admin_lighthouse.mjs), команда **`npm run lh:admin`**; dev-зависимости `lighthouse`, `chrome-launcher`, `playwright` в [`package.json`](package.json); инструкция [`docs/ui/lighthouse/README.md`](docs/ui/lighthouse/README.md); полные JSON-отчёты в `docs/ui/lighthouse/reports/` — в [`.gitignore`](.gitignore); тест наличия артефактов [`tests/test_lighthouse_docs.py`](tests/test_lighthouse_docs.py).
- **Админка / UI (Phase U6):** мобильные модалки `ds-modal-panel` — отступ `safe-area-inset-bottom`, визуальная «ручка» bottom-sheet, то же для `ds-drawer-panel`; сегменты и `ds-btn-sm/md` с минимальной высотой **44px**; [`admin.html`](app/templates/admin.html) — **45** интерактивных зон с `min-h-[44px]`; канбан: **`data-kanban-col`**, `role="region"`, `tabindex="0"`, обработчик **`@keydown` → `handleKanbanKeydown`**, табы вида заказов с **`role="tab"`**; [`_drawer.html`](app/templates/components/_drawer.html) — заголовок **`<h2 id="…-title">`** для **aria-labelledby**. Регрессия: `tests/test_ui_u6_a11y.py`. Пересобран [`app/static/css/admin.css`](app/static/css/admin.css) из [`src/css/admin-input.css`](src/css/admin-input.css).

### Изменено

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

- **Документация:** [PARALLEL_AI_PLAN.md](PARALLEL_AI_PLAN.md) — таблица **sync-точек** (после PR E0.1, после E2.2.B / E2.3.B / E3 хвоста) и явные **запреты** до завершения E0.1; [docs/AI2_PARALLEL_PROMPT.md](docs/AI2_PARALLEL_PROMPT.md) — уточнение зоны `app/api/admin/`.
- **Документация (ранее):** добавлен эпик **[§E0](IMPLEMENTATION_PLAN.md)** (техдолг: раскол админ-API, E0.1–E0.7); §11 и спринт A: приоритет **E0.1**, правила для пакета `app/api/admin/`.
- **Документация (ранее):** [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md), [PARALLEL_AI_PLAN.md](PARALLEL_AI_PLAN.md), [docs/AI2_PARALLEL_PROMPT.md](docs/AI2_PARALLEL_PROMPT.md) — актуализация: E2.1.F и E2 (частично), E1 payload.bin, E3/E16; очередь E2.2.B → E2.2.F.
- **Документация (ранее):** [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) (статусы E1/E3/E17, §E3 и `/ai-value`, порядок спринтов, §S2 CI), [plan.md](plan.md) (AI Value), [codebase.md](codebase.md), [README.md](README.md) — Super Admin аудит webhook, вкладка «Вклад ИИ», навигация «Ошибки»; ссылка на AI2 для параллельных агентов.

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
