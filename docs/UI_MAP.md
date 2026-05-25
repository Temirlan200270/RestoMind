# RestoMind Admin UI Map

Карта UI-слоя нужна как быстрый навигатор для человека и ИИ-агента. Если меняешь админку, сначала найди нужный слой здесь, затем сверяй детали с `docs/UI_DESIGN_SYSTEM.md` и `.cursor/rules/ui-redesign.mdc`.

## Layout Skeleton

- `app/templates/admin.html` — входная точка админки: macro imports, `DOCTYPE`, `head`, общий authenticated shell и include всех экранов.
- `app/templates/screens/_login.html` — экран входа: **«Посмотреть демо»** (demo-login + autoplay pitch), staff login, заявка на подключение.
- `app/templates/screens/_sidebar.html` — desktop-навигация слева.
- `app/templates/screens/_header.html` — глобальная шапка: брендинг, филиал, селектор точки, readiness (скрыт в `isDemoSession`), поиск.
- `app/templates/screens/_system_banner.html` — системные статусы/готовность.
- `app/templates/screens/_alert_banner.html` — верхний баннер внимания.
- `app/templates/screens/_bottom_nav.html` — мобильный tab-bar.
- `app/templates/screens/_modals.html` — общие модалки заказов, броней, подтверждений и setup-checklist.

## Screens

Все экраны подключаются из `app/templates/admin.html` через `{% include "screens/…" %}`. Верхнеуровневые вкладки задаются в `admin-app.js` (`navItems`: `inbox`, `orders`, `chats`, `bookings`, `dashboard`, `ai_center`, `menu`, `settings`). Старые hash-URL (`#operator_queue`, `#incidents`, `#analytics`, …) редиректятся в JS на новые.

### Role-First Admin IA (G10.4+, ✅ Sprint 5)

> **Сейчас:** навигация по **роли staff** (без Mode Bar); smart landing оператора; дашборд «Обычный / Расширенный». Internal `currentMode` остаётся для Command Bar / hash sync. Контракт: [`docs/UI_DESIGN_SYSTEM.md`](UI_DESIGN_SYSTEM.md) § Role-First IA.

| Роль | Сайдбар | Стартовый экран |
|------|---------|-----------------|
| **operator** | `shift`, `inbox`, `orders`, `chats`, `bookings` | shift при `risk_kzt > 0` или `focus.id`, иначе `inbox` |
| **manager** | операции + `menu`, `dashboard`, `ai_center` | `dashboard` (normal analytics) |
| **admin** | все `navItems` | `dashboard` |

| Экран | Шаблон | Примечание |
|-------|--------|------------|
| Смена | `_tab_shift_control.html` | **Focus Deck** + **Context Dock**; **G10.8 Demo Pitch** (`rm-demo-scene`, counterfactual banner, resolve card) — см. [`docs/DEMO_PITCH.md`](DEMO_PITCH.md) |
| Операции | `_tab_inbox.html`, orders, chats, bookings, menu | Inbox: «Очередь помощи», фильтры «В работе / Закрытые» |
| Аналитика | `_tab_dashboard.html` + `_tab_analytics.html` | `analyticsDensity`: normal (Обзор) \| advanced (Подробно + **«Официанты»** KPI iiko) |

`_tab_shift_control.html` — не default для оператора в спокойной смене; smart routing в `applyRoleDefaultLanding()`.

### Операции (`section: operations`)

- `_tab_inbox.html` — **«Требует внимания»**: под-табы «От клиентов» / «Системные» (объединяет сценарии бывших отдельных экранов очереди и инцидентов, P1.5.0).
- `_tab_orders.html` — заказы: канбан / список, фильтры, DnD, модалка заказа.
- `_tab_chats.html` — диалоги: список, лента, панель клиента (mobile drawer). Шапка: бейдж FSM (`activeChatState`: «ИИ отвечает» / «Вы ведёте диалог» / «Подтверждение заказа»). Лента: `formatChatDisplayContent`, бейдж доставки `chatDeliveryBadge`, бейдж «Сбой ИИ» `chatTechnicalFallbackBadge`.
- `_tab_bookings.html` — бронирования: недельная полоса (`bookingWeekDays`), KPI на неделю, список выбранного дня, справка в `_bookings_sidebar_inner.html` (залы, режим, онбординг). API: `date_from` / `date_to` на `GET /api/admin/bookings`.

### Управление (`section: management`)

- `_tab_dashboard.html` — дашборд: KPI, ROI, график; под-таб **`dashboardTab`** `overview` | `analytics`; на overview — блок **«Живая ОС»** (`dashLiveFeed`, обновляется из WebSocket). Бейдж **«данные ОС»** при `event_driven_stats.source === 'event_driven'`. При выбранной точке loaders добавляют `location_id`, а API возвращает `location_scope.source=sql_location`. Аналитика — `{% include "screens/_tab_analytics.html" %}`.
- `_tab_ai_center.html` — **«ИИ-аналитика»**: под-табы `aiCenterTab`:
  - `value` — вклад ИИ (ROI, метрики);
  - `insights` — operational insights + рекомендации;
  - `load` — Digital Twin / нагрузка;
  - `os` — **Автопилот** (`GET /intelligence/os-dashboard`, лента решений `loadAuditLog()`, bulk pricing);
  - `guestcare` — **Отзывы** (внешние 2GIS/Google: `GET/POST /reviews/external*`).
  - `final_mile` — **Финал:** предпросмотр ежедневной сводки ОС, Закупки (предупреждения по запасам, черновики, чеклисты), голосовой ИИ + **журнал звонков** с playback (`loadVoiceCallLogs` → `GET /voice/calls`, пагинация, `location_id`).
  Legacy-файлы `_tab_ai_value.html`, `_tab_intelligence.html`, `_tab_digital_twin.html` остаются для редиректов hash.
- `_tab_menu.html` — меню и стоп-лист.

### Настройки (`currentTab === 'settings'` + `settingsTab`)

- `_tab_settings_restaurant.html` — профиль, расписание, база знаний, force-close, упаковка.
- `_tab_settings_branding.html` — брендинг шапки.
- `_tab_settings_connections.html` — интеграции (iiko, WhatsApp, Telegram). **E5:** бейджи очереди задач (`taskQueueHealth` ← `GET /api/admin/system/task-queue-health`; вызов `refreshTaskQueueHealth()` при входе на dashboard и при открытии connections).
- `_tab_settings_smart_sales.html` — правила допродаж.
- `_tab_settings_team.html` — команда и роли. **StaffMind:** onboarding-сессии, Q&A (`loadStaffMindOnboarding`, …) и **step tracker** (`staffMindTrackerMeta()` — часть метрик на эвристиках до backend).
- `_tab_settings_health.html` — проверки окружения (`loadReadiness()` → `GET /api/admin/settings/environment` или readiness API). FAQ cache metrics **здесь не выводятся** — только API `GET /api/admin/system/faq-cache-metrics`.
- `_tab_settings_technical.html` — экспорт, retention, опасные действия.
- `_tab_settings_bot_test.html` — лаборатория бота / тестовый чат.

### Прочее

- `_tab_operator_queue.html`, `_tab_incidents.html` — оставлены в шаблоне для плавных редиректов со старых hash; основной UX — `_tab_inbox.html`.
- `_modal_packaging_create.html` — модалка правила упаковки.

## Components And Macros

Переиспользуемые Jinja-макросы живут в `app/templates/components/`.

- `_button.html` — `btn(...)`, платформенные кнопки.
- `_card.html` — `card(...)`, секции и панели в дизайн-системе.
- `_kpi_card.html` — KPI-плитки.
- `_table.html` — table frame/wrapper.
- `_section_header.html` — заголовок секции внутри экрана; не использовать для дубля глобального `_header.html`.
- `_segmented.html`, `_tabs.html`, `_settings_tabs.html` — переключатели и вкладки.
- `_modal.html`, `_drawer.html` — оверлеи и мобильные bottom-sheet/drawer паттерны.
- `_order_card.html` — карточки заказов: row/mobile/kanban + status badge.
- `_input.html`, `_select.html`, `_textarea.html`, `_toggle.html` — form controls.
- `_badge.html`, `_status_badge.html` — бейджи и статусы.
- `_empty_state.html`, `_skeleton.html` — пустые состояния и загрузка.
- `_app_shell.html` — shell для чатов.
- `_ui_*` компоненты — legacy/compat слой; новые блоки по возможности вести через `ds-*`.

## Client Logic

- `app/static/js/admin-app.js` — большой Alpine `adminApp()` и миксины. Следующая крупная цель для раскола.
- Location UI state: `userData.available_locations`, `selectedLocationId` / `activeLocationId`, `locationQueryParams()`, `onLocationFilterChanged()`. Прокидывается в `loadOrders`, `loadChatList`, dashboard loaders, Intelligence/Digital Twin/OS Dashboard.
- **System diagnostics (JS):** `refreshTaskQueueHealth()` → `/api/admin/system/task-queue-health`; FAQ metrics endpoint есть на бэкенде, UI-панели пока нет.
- `app/static/js/admin-brand-tokens.js` — CSS-токены бренда.
- `app/static/js/onboarding.js` — логика onboarding/request access.
- `src/css/admin-input.css` — исходник Tailwind/CSS. Не редактировать `app/static/css/admin.css` напрямую.
- `app/static/css/admin.css` — собранный CSS после `npm run build:admin-css`.
- `app/static/manifest.webmanifest` + `app/static/sw.js` — PWA (offline shell); подключение в [`admin.html`](app/templates/admin.html).

### WebSocket (admin)

Типы, которые обрабатывает `handleWsEvent` в [`admin-app.js`](app/static/js/admin-app.js):

- Операционные: `new_message`, `order_updated`, `message_status_updated`, `human_needed`, `state_changed`, `stoplist_updated`, `menu_updated`.
- **OS:** `os.audit` — prepend в ленту решений (`auditLog`).
- **Бизнес-события шины** (refresh дашборда / автопилота): `order.created`, `order.confirmed`, `order.cancelled`, `payment.completed`, `payment.failed`, `payment.expired`, `booking.created`, `booking.confirmed`, `booking.cancelled`.

## Current Contracts

### Вкладка «Чаты» (`_tab_chats.html` + `admin-app.js`)

- **Шапка (компактная):** один статус — `chatModeSummary()` + `chatModeToneClass()` (ИИ / вы / пауза / подтверждение заказа); одна главная кнопка «Ответить самому» / «Вернуть боту»; пауза, закрытие, назначение — в меню «⋯».
- **Состояние диалога:** `activeChatState` — из `GET /api/admin/chats/{phone}/state` при выборе чата; обновляется по WebSocket `state_changed` и при `onHumanNeeded` (эскалация).
- **Ввод оператора:** заблокирован, пока `chatIsBotActive()` — placeholder через `chatOperatorPlaceholder()`.
- **Текст в ленте:** `formatChatDisplayContent(msg)` — legacy `[OPERATOR_ONLY …]` и `meta.operator_only` → «ИИ не отвечает (ожидает оператора)»; клиенту в WhatsApp уходит отдельный шаблон из webhook.
- **Сбой LLM:** `meta.technical_fallback` на исходящих assistant → бейдж «Сбой ИИ» (`chatTechnicalFallbackBadge`). Ставится в `webhooks.py` при совпадении с fallback-текстом `ai_brain._FALLBACK_RESPONSE`.
- **Realtime:** `new_message` должен пробрасывать `meta` в объект сообщения (`onNewMessage`), иначе бейджи не появятся до перезагрузки истории.
- **E.164 / дубли номера:** `adminNormalizePhone`, `adminPhonesMatch`, `adminDedupeChatListByPhone` — legacy `7705…` и `+7705…` схлопываются в одной строке списка; полное слияние истории — `scripts/merge_duplicate_users.py`.

Подробнее про FSM и события: `docs/STATE_MACHINE.md`, `docs/EVENT_ARCHITECTURE.md`. FAQ cache и ops — `docs/AI_OPERATIONS.md` § WhatsApp Performance Pack.

- Глобальный `_header.html` отвечает за название активной вкладки. Внутри экранов не добавлять второй крупный `section_header` с тем же названием.
- `docs/ROADMAP.md` — единственный трекер задач и статусов.
- `CHANGELOG.md` — журнал значимых изменений.
- `docs/UI_DESIGN_SYSTEM.md` — UI-контракт: `ds-*`, a11y, Lighthouse, touch targets.
- `.cursor/rules/ui-redesign.mdc` — практические ограничения для `app/templates/**` и `app/static/**`.

## Known Follow-Ups

- **Финал UI:** `aiCenterTab=final_mile` — чеклисты закупок ✅, голосовой ИИ + журнал звонков ✅; обучение сотрудников — **Настройки → Команда** (StaffMind).
- **KPI официантов:** блок «Официанты» в `analyticsDensity=advanced` ✅ — [`waiter_kpi.py`](app/api/admin/waiter_kpi.py).
- **Control Plane:** `GET /trace-timeline?trace_id=` + панель «Цепочка trace_id» в AI Center → OS (`loadTraceTimeline()`).
- **Superadmin:** `/superadmin` — tech fields (`iiko_api_login`, …), журнал `GET /api/superadmin/audit` (миграция `20260521_superadmin_audit` на env).
- Разбить `admin-app.js` на небольшие доменные модули: dashboard, orders, menu, chats, settings.
- Постепенно убрать гибриды `rm-*`/raw Tailwind в экранах, когда файл всё равно открыт для правок.
- Решить, нужен ли Lazy DOM слой для тяжёлых экранов. Сейчас все include рендерятся сразу ради простоты и предсказуемости.
- Привести `superadmin.html` к общей дизайн-системе, если он станет частым рабочим экраном.
