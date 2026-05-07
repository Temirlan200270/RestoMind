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

Все основные вкладки живут в `app/templates/screens/` и обычно переключаются через `currentTab`.

- `_tab_dashboard.html` — главная: KPI, быстрые переходы, ROI, график, события, последние заказы.
- `_tab_analytics.html` — аналитика: период, KPI, графики, menu engineering, география, топы и дневная разбивка.
- `_tab_ai_value.html` — вклад ИИ: вклад в выручку, экономия времени, качество подсказок.
- `_tab_orders.html` — заказы: канбан, таблица, фильтры, bulk/drag сценарии.
- `_tab_operator_queue.html` — очередь оператора/помощь клиентам.
- `_tab_incidents.html` — инциденты и задачи, требующие внимания.
- `_tab_bookings.html` — бронирования.
- `_tab_chats.html` — живые переписки через `chat_shell_layout`.
- `_tab_menu.html` — каталог товаров, стоп-лист, фильтры, категории, карточки позиций, modal edit.
- `_tab_settings_connections.html` — интеграции: iiko, WhatsApp, Telegram, sync/status.
- `_tab_settings_branding.html` — бренд в панели: название, цвет, логотип, preview.
- `_tab_settings_restaurant.html` — профиль ресторана, график, база знаний, force-close, упаковка.
- `_tab_settings_smart_sales.html` — правила допродаж.
- `_tab_settings_health.html` — проверки окружения и готовность сервиса.
- `_tab_settings_technical.html` — экспорт, retention, опасные зоны, технические действия.
- `_tab_settings_bot_test.html` — лаборатория бота/ИИ.
- `_tab_settings_team.html` — команда и роли.
- `_modal_packaging_create.html` — модалка создания/редактирования правила упаковки.

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
