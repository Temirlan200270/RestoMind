# RestoMind Admin UI Map

Карта UI-слоя нужна как быстрый навигатор для человека и ИИ-агента. Если меняешь админку, сначала найди нужный слой здесь, затем сверяй детали с `docs/UI_DESIGN_SYSTEM.md` и `.cursor/rules/ui-redesign.mdc`.

## Layout Skeleton

- `app/templates/admin.html` — входная точка админки: macro imports, `DOCTYPE`, `head`, общий authenticated shell и include всех экранов.
- `app/templates/screens/_login.html` — экран входа, demo-login и заявка на подключение.
- `app/templates/screens/_sidebar.html` — desktop-навигация слева.
- `app/templates/screens/_header.html` — глобальная шапка: брендинг, филиал, название активной вкладки, readiness, глобальный поиск.
- `app/templates/screens/_system_banner.html` — системные статусы/готовность.
- `app/templates/screens/_alert_banner.html` — верхний баннер внимания.
- `app/templates/screens/_bottom_nav.html` — мобильный tab-bar.
- `app/templates/screens/_modals.html` — общие модалки заказов, броней, подтверждений и setup-checklist.

## Screens

Все экраны подключаются из `app/templates/admin.html` через `{% include "screens/…" %}`. Верхнеуровневые вкладки задаются в `admin-app.js` (`navItems`: `inbox`, `orders`, `chats`, `bookings`, `dashboard`, `ai_center`, `menu`, `settings`). Старые hash-URL (`#operator_queue`, `#incidents`, `#analytics`, …) редиректятся в JS на новые.

### Операции (`section: operations`)

- `_tab_inbox.html` — **«Требует внимания»**: под-табы «От клиентов» / «Системные» (объединяет сценарии бывших отдельных экранов очереди и инцидентов, P1.5.0).
- `_tab_orders.html` — заказы: канбан / список, фильтры, DnD, модалка заказа.
- `_tab_chats.html` — диалоги: список, лента, панель клиента (mobile drawer).
- `_tab_bookings.html` — бронирования.

### Управление (`section: management`)

- `_tab_dashboard.html` — дашборд: KPI, ROI, график, лента; под-таб **`dashboardTab`** `overview` | `analytics`; блок аналитики — `{% include "screens/_tab_analytics.html" %}` внутри этого файла (отдельного пункта в сайдбаре нет).
- `_tab_ai_center.html` — **«ИИ-аналитика»**: под-табы `aiCenterTab` `value` | `insights` | `load` (вклад ИИ, operational insights, Digital Twin / нагрузка). В DOM по-прежнему лежат `_tab_ai_value.html`, `_tab_intelligence.html`, `_tab_digital_twin.html` для совместимости и редиректов — новая навигация ведёт в `ai_center`.
- `_tab_menu.html` — меню и стоп-лист.

### Настройки (`currentTab === 'settings'` + `settingsTab`)

- `_tab_settings_restaurant.html` — профиль, расписание, база знаний, force-close, упаковка.
- `_tab_settings_branding.html` — брендинг шапки.
- `_tab_settings_connections.html` — интеграции (iiko, WhatsApp, Telegram).
- `_tab_settings_smart_sales.html` — правила допродаж.
- `_tab_settings_team.html` — команда и роли.
- `_tab_settings_health.html` — проверки окружения.
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
- `app/static/js/admin-brand-tokens.js` — CSS-токены бренда.
- `app/static/js/onboarding.js` — логика onboarding/request access.
- `src/css/admin-input.css` — исходник Tailwind/CSS. Не редактировать `app/static/css/admin.css` напрямую.
- `app/static/css/admin.css` — собранный CSS после `npm run build:admin-css`.

## Current Contracts

- Глобальный `_header.html` отвечает за название активной вкладки. Внутри экранов не добавлять второй крупный `section_header` с тем же названием.
- `docs/ROADMAP.md` — единственный трекер задач и статусов.
- `CHANGELOG.md` — журнал значимых изменений.
- `docs/UI_DESIGN_SYSTEM.md` — UI-контракт: `ds-*`, a11y, Lighthouse, touch targets.
- `.cursor/rules/ui-redesign.mdc` — практические ограничения для `app/templates/**` и `app/static/**`.

## Known Follow-Ups

- Разбить `admin-app.js` на небольшие доменные модули: dashboard, orders, menu, chats, settings.
- Постепенно убрать гибриды `rm-*`/raw Tailwind в экранах, когда файл всё равно открыт для правок.
- Решить, нужен ли Lazy DOM слой для тяжёлых экранов. Сейчас все include рендерятся сразу ради простоты и предсказуемости.
- Привести `superadmin.html` к общей дизайн-системе, если он станет частым рабочим экраном.
