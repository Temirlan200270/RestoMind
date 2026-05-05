# RestoMind UI Redesign — поэтапный план

> **Документ агностичен к инструменту.** Подходит для исполнения любым агентом — Claude Code, Cursor (Composer / Background Agent), Aider и т.д. Главное условие: агент имеет доступ к репозиторию + браузеру через MCP-сервер (Chrome DevTools или Playwright). Workflow для Cursor описан в [docs/AI_TOOLS_SETUP.md](AI_TOOLS_SETUP.md).
>
> **Зеркало и владение:** этот файл — копия рабочего плана из `~/.claude/plans/glistening-wibbling-sparkle.md` (локально у Claude Code). Изменения здесь — это и есть источник истины для команды; локальная копия может расходиться.

---

## Контекст

UI админки RestoMind собран фичами, не системой. По разведке (`app/templates/admin.html` ~4558 строк, `app/static/js/admin-app.js` ~6644 строк):

- **Нет дизайн-токенов** — палитра захардкожена в Tailwind-классах (`brand-600`, `slate-`, `emerald-`); кастомные `rm-*` классы определены в нескольких местах.
- **Нет общих компонентов** — 13+ модалок с разными `z-index` и стилями, таблицы переизобретены на каждой странице, кнопки и инпуты собираются из Tailwind-утилит inline.
- **Информационная архитектура слабая** — 12 пунктов в сайдбаре, Настройки разорваны (8 подвкладок, `whatsapp_phone_number_id` лежит в «Профиле», iiko в «Подключениях», но обе пишутся в одну таблицу `organizations`).
- **Настройки — самый плохой раздел** (подтверждено заказчиком и второй разведкой): простыня с якорями вместо группировки, график работы спрятан в модалку, iiko-онбординг дублирован с `onboarding.html`.
- **Owner Value не виден равномерно** — вкладка «Вклад ИИ» есть, на дашборде есть карточки, но в остальных разделах владелец не видит «за что он платит».

Цель: поэтапная (без big-bang) замена UI на дизайн-систему. Стек остаётся прежним — Jinja2 + Alpine.js + Tailwind CSS + Chart.js. Light-тема через CSS-переменные с заделом на dark. Vite — не сейчас. Визуальная верификация через Chrome DevTools MCP.

## Подход

Семь основных фаз **U0–U7**, каждая мерджится в `main` без слома прода. Дополнительно зафиксирована **Phase U4.5 (Workflow Loop)** — продуктовые «слепые зоны» (чаты, жизненный цикл заказа после iiko, петля обучения ИИ, производительность UI, упаковка + модификаторы): это уже не только причёсывание Tailwind, а модель данных и API. Жёстко разделено на ИИ 1 (backend-хвосты для UI) и ИИ 2 (фронт). Все новые страницы и блоки используют **только** компонентную библиотеку; старые блоки переносим раздел-за-разделом.

### Phase U0 — Подключение MCP браузера и baseline

- **Статус (админка, 2026-05):** baseline в репозитории — [`docs/ui/baseline/`](ui/baseline/) + [`docs/UI_REDESIGN_NOTES.md`](UI_REDESIGN_NOTES.md); автопрогон [`scripts/capture_admin_u0_baseline.py`](../scripts/capture_admin_u0_baseline.py). Superadmin / onboarding / мобильный 320×640 — вынесены за рамки этой итерации (см. README в baseline).
- Подключить Chrome DevTools MCP (или Playwright MCP) — для Claude Code в `~/.claude/.mcp.json`, для Cursor в `.cursor/mcp.json` (см. [docs/AI_TOOLS_SETUP.md](AI_TOOLS_SETUP.md)).
- Запустить локально `python -m uvicorn app.main:app --reload`, авторизоваться в админке.
- Сделать baseline-скрины через MCP: `docs/ui/baseline/<section>.png` для всех 12 разделов + 8 подвкладок Настроек + `superadmin.html` + `onboarding.html` + один мобильный (320×640).
- Зафиксировать в `docs/UI_REDESIGN_NOTES.md` визуальный долг по каждому разделу (что переделываем, что оставляем).

### Phase U1 — Дизайн-система и библиотека макросов

- **Статус (2026-05):** в репозитории — токены `ds-*` в [`src/css/admin-input.css`](../src/css/admin-input.css), макросы в [`app/templates/components/`](../app/templates/components/), живая витрина `GET /admin/_/components` ([`app/templates/_components_storybook.html`](../app/templates/_components_storybook.html)); [`app/templates/admin.html`](../app/templates/admin.html) не менялся.

**Файлы создаются:**
- `app/static/css/admin.css` (расширение существующего): добавить блок `:root { --color-* / --space-* / --radius-* / --shadow-* / --z-* / --motion-* }` через CSS-переменные. Палитра брендинга (`--color-brand-50..900`) пересчитывается из `Organization.brand_color_hex` через JS-хелпер при загрузке (HSL-производные).
- `app/templates/components/` — каталог Jinja2-макросов:
  - `_button.html` — `btn(variant='primary|secondary|ghost|danger|success', size='sm|md|lg', icon, label, type, attrs)`
  - `_card.html` — `card(title, subtitle, actions, padding)` через `caller()`
  - `_modal.html` — `modal(id, title, size='sm|md|lg|xl', footer=true)` со слотами через `caller()`, единый `z-index` стек
  - `_drawer.html` — bottom-sheet (мобильный) / side-drawer
  - `_tabs.html` — `tabs(items=[{key,label,icon,badge}], active, variant='underline|pills|sidebar')`
  - `_section_header.html`
  - `_kpi_card.html` — `kpi(label, value, trend, sparkline, accent, size)`
  - `_table.html` — `data_table(columns, rows, empty_text, pagination)`
  - `_badge.html` + `_status_badge.html` (объединить `statusConfig` для orders/bookings/incidents в один helper)
  - `_input.html`, `_select.html`, `_textarea.html`, `_toggle.html`, `_color_picker.html`, `_file_dropzone.html`
  - `_empty_state.html` — `empty(icon, title, description, actions)`
  - `_skeleton.html` — `skeleton(kind='line|card|table', rows)`
  - `_segmented.html` — переключатель «день/неделя/месяц»
  - `_app_shell.html` — каркас sidebar + header + content
- `app/templates/_components_storybook.html` + роут `GET /admin/_/components` (доступ только `is_superadmin` или `APP_DEBUG=true`) — живая демонстрация всех макросов: палитра, типографика, отступы, состояния (loading/empty/error). Используется обоими ИИ для регрессии и заказчиком — для приёмки.

**Существующие макросы переиспользуем:** `ui_card`, `order_card_*`, `order_status_badge` — мигрируют в новую библиотеку с сохранением имён, чтобы ничего не сломать.

**DoD:** `/admin/_/components` рендерит все компоненты без ошибок; `admin.html` не тронут; `pytest` зелёный.

### Phase U2 — AI Value Visibility (сквозная фишка для владельца)

- **Статус (2026-05):** в репозитории — бейдж в шапке + ROI-тост + welcome-баннер на дашборде + чипы разделов ([`admin.html`](../app/templates/admin.html), [`admin-app.js`](../app/static/js/admin-app.js)); API: расширенные поля в [`GET /api/admin/stats`](../app/api/admin/_monolith.py), блок `rolling_week` в [`GET /api/admin/roi/today`](../app/api/admin/_monolith.py).

Цель — владелец на любом экране видит «за что он платит», без переключения вкладок.

- **Постоянный бейдж в шапке** (`admin.html` header): `{{ ai_today_revenue_kzt }} ₸ · +{{ delta_pct }}%`, кликабелен → `Вклад ИИ`. Данные из существующего `GET /api/admin/stats` (поле `upsell_revenue_today` + расширение если потребуется).
- **Welcome banner на Дашборде**: «Бот за эту неделю заменил X часов оператора и принёс Y ₸». Источник — `app/services/owner_roi.py` (уже есть `aggregate_org_window`, `build_today_narrative_ru`, `build_achievements_week`). Toggle через `sessionStorage` — закрыл, не показываем сегодня.
- **Mini-metric chip на 4 разделах**:
  - Заказы: «N/M оформил бот сегодня»
  - Диалоги: «N ответов без оператора»
  - Меню: «N/M позиций имеют AI-теги» (готовится под E9.1)
  - Бронирования: «N подтверждений автоматически»
- **Daily ROI Toast**: первый вход за календарный день → тост «Доброе утро. Вчера: N заказов, M ₸, K допродаж принято». `localStorage.lastRoiToastDate`.

**Backend-хвост (ИИ 1, маленький):** убедиться, что `/api/admin/stats` отдаёт сегодняшнюю выручку ИИ + дельту относительно вчера. Если нет — расширить (узкий diff в `_monolith.py` или, если уже распилен, в `app/api/admin/analytics.py`).

**DoD:** на любом экране админки бейдж в шапке кликабелен, ROI-тост появляется не чаще раза в сутки, mini-metric честно отражает сегодняшние цифры.

### Phase U3 — Информационная архитектура (sidebar + URL)

- **Статус (2026-05):** в репозитории — новая группировка сайдбара, `dashboardView` / `menuView` / `customerHelpSub`, маппинг старых hash во [`admin-app.js`](../app/static/js/admin-app.js) и объединённые блоки в [`admin.html`](../app/templates/admin.html).

**Старая структура (12 пунктов в 3 группах):**
```
Обзор:        Дашборд • Аналитика • Вклад ИИ
Операции:     Требует внимания • Заказы • Помощь клиентам • Бронирования • Диалоги • Меню • Стоп-лист
Настройки:    Настройки (с 8 подвкладками)
```

**Новая (8 пунктов в 4 группах):**
```
Обзор:        Дашборд (включает блок Аналитики и Вклад ИИ как табы внутри)
              Вклад ИИ (полная страница BI — оставляем как есть)
Операции:     Заказы (канбан/таблица/manual — табы)
              Диалоги
              Бронирования
              Помощь клиентам (объединяет «Требует внимания» + «Ошибки»/failed_tasks)
Каталог:      Меню (со встроенным табом «Стоп-лист»)
Настройки:    единственная кнопка → внутренняя структура из 8 групп (Phase U4)
```

- Меню + Стоп-лист сливаются в один раздел `currentTab === 'menu'` с внутренними табами `menuView === 'catalog' | 'stoplist'`.
- Аналитика мигрирует в Дашборд как `dashboardView === 'overview' | 'analytics'`.
- «Требует внимания» + «Ошибки» → «Помощь клиентам» с табами `inbox = 'incidents' | 'failed_tasks'`.
- Mapping старых `currentTab` → новые сохраняется в `admin-app.js` для обратной совместимости старых deep-links.

**DoD:** все старые ссылки и якоря работают (mapping); шапка адаптируется под новый набор; e2e через MCP — переход по каждому пункту без 404.

### Phase U4 — Полный редизайн раздела «Настройки»

- **Статус (2026-05):** ключи `settingsTab` прежние; подписи вкладок в шапке и в [`_settings_tabs.html`](../app/templates/components/_settings_tabs.html) согласованы с терминологией (в т.ч. **«Бот / ИИ»** для `bot_test`). **DoD U4 по UX настройках закрыт в коде:** inline-график и упаковка — как ранее; **интеграции** — карточки с раскрытием (`Настроить`), webhook WhatsApp внутри карточки; **«Данные и безопасность»** — аккордеоны экспорт / ретеншн / опасные блоки; **«Бот / ИИ»** — единая лаборатория (готовность, быстрые переходы, промпт-контекст, тестовый чат), при открытии вкладки вызывается `loadSetupStatus()` из [`loadTabData`](../app/static/js/admin-app.js). **Вне объёма U4 (оставить на U5–U6):** перенос блоков на макросы дизайн-системы и **Lighthouse Accessibility ≥ 90** по всем группам.

**Новая структура (sidebar внутри Настроек, slim):**

| Группа | Что внутри | Откуда мигрирует |
|--------|------------|------------------|
| **Профиль** | Название, ТЗ, валюта, **график работы inline** (без модалки) | `restaurant.profile` |
| **Бренд** | Логотип, цвет, имя бренда, превью шапки + превью чека | существующая `branding` (E2.2.F) |
| **Команда** | Staff CRUD, инвайты | `team` |
| **Интеграции** | Карточки iiko / WhatsApp / Telegram (раскрывающиеся), статус сверху | `connections` + поля `whatsapp_phone_number_id` / `telegram_ops_chat_id` из `restaurant.profile` |
| **Логистика** | Упаковка (live-калькулятор слева + правила справа, синхронизация в реальном времени) + Предоплата (порог + auto-iiko-print) | `restaurant.packaging` + `prepayment_enforced`/`auto_send_to_iiko_after_payment` |
| **Бот / ИИ** | База знаний + Правила upsell + Промпт-настройки (задел под Strategy Engine E11) + **Тест бота** как нижний таб | `restaurant.knowledge` + `smart_sales` + `bot_test` + `prepayment_legal_text` |
| **Данные** | Экспорт (CSV) + Ретеншн chat_logs + Опасная зона (purge/wipe) — три аккордеона | `technical` (часть) |
| **Состояние** | Диагностика iiko/WhatsApp/Redis/DB + последние события синхронизации + версия | `health` + `technical` (env) |

**Ключевые UX-улучшения:**
- **Inline-график работы** на Профиле (компактная сетка 7×4) с кнопкой «Открыть в полный размер» (текущая модалка остаётся как fallback).
- **Упаковка**: один экран с двумя колонками — слева live-калькулятор чека, справа таблица правил с inline-редактированием. Изменение правила → preview обновляется автоматически.
- **Интеграции**: каждая карточка имеет статус-чип (зелёный/жёлтый/красный) и кнопку «Настроить» — раскрывается inline без скролла на отдельную страницу.
- **Бот / ИИ**: единая «лаборатория» — База знаний и Правила upsell не разорваны по разным вкладкам, как сейчас (`smart_sales` отдельно от `restaurant.knowledge`).
- **Данные / Опасная зона**: модалка подтверждения с countdown 5 секунд + явный чекбокс «понимаю последствия» + текстовое слово, как сейчас.

**Backend не трогаем** (поля `Organization` и эндпоинты `/organization/profile`, `/organization/prefs`, `/integrations/*`, `/staff`, `/upsell-rules`, `/packaging-rules`, `/knowledge` остаются), но UI собирает их в новую группировку.

**DoD:** все существующие настройки сохраняются и применяются; редактирование графика работы доступно inline и в модалке; live-калькулятор Упаковки реагирует на изменение правил; Опасная зона требует явного подтверждения; Lighthouse Accessibility для всех 8 групп ≥ 90.

### Phase U4.5 — Workflow Loop (замыкание процессов, B2B SaaS)

> **Зачем в плане:** текущие фазы U0–U7 закрывают дизайн-систему, IA, настройки и косметическую миграцию разделов. Ниже — продуктовые пробелы, без которых операционная нагрузка (реальные продажи, много чатов/заказов) упирается в потолок. Это **не** дублирует U5 по смыслу: U5 = перенос на макросы; U4.5 = **новая бизнес-логика + контракты API + частично архитектура фронта**.

**Принцип приоритизации:** можно вести параллельно с U5/U6, но отдельными PR; каждый подпункт имеет собственный DoD. Часть блоков зависит от вебхуков iiko / шаблонов Meta — закладывать fallback (ручной статус, без авто-WhatsApp), чтобы не блокировать UI.

| # | Тема | Суть проблемы | UI / продукт | Зависимости (ИИ 1) |
|---|------|---------------|--------------|-------------------|
| 1 | **Chat triage («Inbox Zero»)** | Список диалогов растёт бесконечно по `lastAt`; нет завершения потока для оператора. | Фильтры: активные (бот) / на мне (takeover) / завершённые; действия **Закрыть диалог** (архив/Done); **Snooze** («напомнить через N мин»). | Модель статуса/архива чата, индексы, API списка с фильтрами; опционально WS-события. |
| 2 | **Post-iiko fulfillment** | Канбан заканчивается на «отправлено в iiko»; нет единого окна для «в пути / выдан». | Колонки или стадии **В доставке / ожидает выдачи** и **Завершён**; при отсутствии вебхуков — **ручной** перенос карточки. UX-опция: при переходе в «в пути» — подтверждение «отправить гостю шаблон WhatsApp?» (если есть интеграция шаблонов). | Стадии заказа в БД или маппинг статусов; эндпоинт смены стадии; связка с Meta templates (отдельный эпик, см. roadmap продукта). |
| 3 | **AI feedback loop** | `recommendation_trace` в заказе read-only; менеджер не может исправить поведение из контекста. | В Sales Insight — **«Исправить логику»** (thumbs down) → drawer с предложением анти-правила / правила upsell; сохранение в `upsell_rules` (или черновик на модерацию). | API создания правила из шаблона (валидация, tenant); аудит. |
| 4 | **Alpine / «god object»** | Один `x-data="adminApp()"`, большие массивы — риск лагов на слабых ПК при WS. | `:key` везде где `x-for`; `x-ignore` на тяжёлых Chart.js-блоках; **пагинация / «ещё 20»** в канбане; поэтапный вынос в `$store` и мелкие `x-data` (чаты, канбан). | Минимально; скорее эпик **E-JS-Refactor** (см. раздел «Не входит») — здесь только критерии приёмки для админки. |
| 5 | **Упаковка и модификаторы** | Правила по keywords не покрывают iiko-модификаторы и «один пакет на заказ». | В редакторе состава заказа — отображение **вложенных модификаторов**; в настройках упаковки — правила уровня **заказ / категория** (порог суммы, один контейнер на заказ). | Контракт позиций корзины (модификаторы в API); расширение модели правил упаковки при необходимости. |

**DoD (фаза U4.5 считается «запущенной», когда выполнено):** для каждого принятого в работу ряда таблицы — отдельный мердж с тестами и без регрессии существующих сценариев; в `CHANGELOG` и при необходимости в `IMPLEMENTATION_PLAN.md` — эпик со ссылкой на эту секцию.

#### Статус реализации U4.5 (2026-05) — **готово**

Вертикали закрыты в коде; см. запись в [`CHANGELOG.md`](../CHANGELOG.md) и тесты [`tests/test_ui_u45.py`](../tests/test_ui_u45.py) (pytest: 4 теста).

| # | Что сделано | Где в коде |
|---|-------------|------------|
| 1 **Chat triage** | Фильтры `mode=active \| mine \| closed` (+ `snoozed`, `all` в API), takeover/release, Snooze, Close/Reopen; состояние в **`User.meta_json.chat_triage`** (state, assignee, snooze, closed_at). | [`app/api/admin/_monolith.py`](../app/api/admin/_monolith.py) (`list_chats_sidebar`, `_save_chat_triage`, `…/close`, `…/reopen`, `…/snooze`), [`admin-app.js`](../app/static/js/admin-app.js) (`chatTriageMode`, `postChatTriageAction`), [`admin.html`](../app/templates/admin.html) (табы triage). |
| 2 **Post-iiko fulfillment** | Статусы **`in_transit`**, **`waiting_pickup`**, **`completed`**; ручной перенос в канбане; при смене статуса — **`order_meta.fulfillment_events`** (последние события). | [`app/db/models.py`](../app/db/models.py) (`OrderStatus`), [`_monolith.py`](../app/api/admin/_monolith.py) (`patch_order_status` + `fulfillment_allowed`), [`admin.html`](../app/templates/admin.html) / [`admin-app.js`](../app/static/js/admin-app.js) (колонки канбана, `kanbanDrop`). |
| 3 **AI feedback loop** | Из модалки заказа — создание upsell-правила или анти-правила (**`not_upsell`** на позиции меню при режиме forbid). | `POST /api/admin/orders/{order_id}/feedback/upsell-rule`, [`create_upsell_rule_from_order_feedback`](../app/api/admin/_monolith.py); UI: `createUpsellFeedback` в [`admin-app.js`](../app/static/js/admin-app.js). |
| 4 **Alpine / perf** | **`x-ignore`** на canvas Chart.js; **`kanbanVisible`** и кнопки **«ещё 20»** по каждой колонке. | [`admin.html`](../app/templates/admin.html) (`x-ignore` на графиках), [`admin-app.js`](../app/static/js/admin-app.js) (`kanbanShowMore`, `kanbanVisible`). |
| 5 **Упаковка / модификаторы** | **`PackagingRule.scope`**: `item` / `category` / `order`; учёт в [`compute_fee_lines`](../app/services/order_logic.py); миграция **`20260507_ui_u45_packaging`**, SQLite startup — колонки `scope`, `category_match` в [`main.py`](../app/main.py); в редакторе заказа — поле модификаторов и отображение. | Миграция [`alembic/versions/20260507_ui_u45_packaging_scope.py`](../alembic/versions/20260507_ui_u45_packaging_scope.py), модель [`PackagingRule`](../app/db/models.py), [`admin.html`](../app/templates/admin.html) (строка позиции / модификаторы). |

**Вне текущего объёма (как в исходной таблице плана):** опциональное подтверждение **WhatsApp-шаблона** при переходе «в пути» — отдельная связка с Meta templates / продуктовый эпик; отдельный **drawer в Sales Insight** вместо кнопок в модалке заказа не требуется — эквивалентная подача из контекста заказа реализована.

### Phase U5 — Миграция остальных разделов (по приоритету)

Каждый раздел = отдельный PR, использует **только** компоненты из `app/templates/components/`. Старый код блока удаляется в том же PR.

- **Статус (2026-05):** **Phase U5 закрыта в коде по объёму плана:** дашборд (в т.ч. ROI, «Ценность ИИ», предупреждения, сегменты графика), **Аналитика**, **Заказы** (segmented + существующие `table_frame` / канбан), **Меню + стоп-лист** (`menuView`, без отдельного таба), **Диалоги** (трёхколоночный shell), **Бронирования**, **Помощь клиентам**, **Вклад ИИ**; ключевые модалки на `ds-modal-*`; остаются точечные inline-блоки в тяжёлых секциях (инциденты, часть аналитических таблиц) — не блокируют DoD миграции на компоненты.

| Порядок | Раздел | Ключевые изменения |
|---------|--------|--------------------|
| 1 | **Дашборд** | KPI-row через `_kpi_card`, графики через единый Chart.js helper, `_section_header`, AI Value banner (Phase U2) |
| 2 | **Заказы** | Канбан-карточки через `order_card_kanban` (мигрирует), таблица через `_table`, модалка через `_modal` (унификация z-index), фильтры через `_segmented` |
| 3 | **Меню (+ Стоп-лист как таб)** | Карточки через `_card`, фильтры по категориям через `_segmented`, модалка позиции через `_modal`, мобильная адаптация |
| 4 | **Диалоги** | Three-pane layout (список ↔ чат ↔ инфо) через `_app_shell` слоты, мобильная — через `_drawer` |
| 5 | **Бронирования** | Канбан/таблица переключаемые, модалка через `_modal`, селект зала через `_select` |
| 6 | **Помощь клиентам** | Объединение «Требует внимания» + failed_tasks; единый список с фильтром-табом, действия через `btn` |
| 7 | **Вклад ИИ** | Расширение существующей вкладки до уровня BI — KPI через `_kpi_card`, графики через единый helper, Empty states через `_empty_state` |

**DoD после каждого подэтапа:** `grep -E "rounded-2xl bg-white shadow-sm" app/templates/admin.html` показывает уменьшение inline-стилей; e2e через MCP по каждой странице без console-error; smoke в браузере.

### Phase U6 — Mobile + Accessibility финал

- **Статус (2026-05):** **закрыта в коде.** Модалки `ds-modal-panel` на `<640px` — bottom-sheet (как `_drawer`), safe-area `padding-bottom`, визуальная «ручка» `::before`; [`_drawer.html`](../app/templates/components/_drawer.html) — `aria-labelledby` через `<h2 id="…-title">`. Touch **≥ 44px**: `ds-segmented`, `ds-btn-sm/md`, [`admin.html`](../app/templates/admin.html) — `min-h-[44px]` для компактных кнопок; канбан — `min-height` карточек на мобильном. Клавиатура: глобальный **Esc** (`handleGlobalKeydown`), **стрелки** между колонками `[data-kanban-col]` (`handleKanbanKeydown`). Таб «По этапам / Список» — `role="tab"` / `aria-selected`. Lighthouse mobile **≥ 90** по целевым экранам — приёмка вручную (Chrome DevTools / Lighthouse).

- Все модалки, у которых нет кастомного UX, превращаются в bottom-sheet через `_drawer` на `<sm`.
- Touch targets ≥ 44×44 px (актуально для канбана, фильтров, кнопок в таблицах).
- Keyboard navigation: Tab/Shift+Tab по всем интерактивным элементам, Esc закрывает модалки, стрелки в канбане перемещают фокус между колонками.
- ARIA labels на иконках без текста, `role="dialog" aria-modal="true"` на всех модалках.
- Контраст WCAG AA — проверяется через Chrome DevTools MCP (Accessibility panel).
- Lighthouse Mobile ≥ 90 на Дашборде, Заказах, Меню, Настройках.

### Phase U7 — Документация

- **Статус (2026-05):** **закрыта.** [`docs/UI_DESIGN_SYSTEM.md`](UI_DESIGN_SYSTEM.md) — принципы, токены `:root`, каталог компонентов и ссылка на storybook, IA, anti-patterns, миграция новой страницы, раздел U6 (a11y). [`IMPLEMENTATION_PLAN.md`](../IMPLEMENTATION_PLAN.md) — эпик **E-UI**. [`PARALLEL_AI_PLAN.md`](../PARALLEL_AI_PLAN.md) — секция **«Зоны UI»**. [`CHANGELOG.md`](../CHANGELOG.md) — записи в `[Unreleased]` для U6/U7.

- `docs/UI_DESIGN_SYSTEM.md` — финальная версия:
  - Принципы (compact density, value-first, mobile-friendly, dark-ready)
  - Каталог CSS-переменных полным списком
  - Каталог компонентов с примерами вызова и скриншотами из storybook
  - Information architecture (старая → новая, с mapping)
  - Anti-patterns (не использовать inline `bg-white rounded-2xl shadow-sm` в новых блоках)
  - Migration guide для добавления новой страницы
- `IMPLEMENTATION_PLAN.md` — добавить **Эпик E-UI** со ссылкой на этот файл и статусом по фазам.
- `PARALLEL_AI_PLAN.md` — секция «Зоны UI»: ИИ 2 владеет `app/templates/components/*`, `app/static/css/admin.css`, `_components_storybook.html`; ИИ 1 — `/admin/_/components` роут и расширения `/api/admin/stats` для AI Value.
- `CHANGELOG.md` — записи `## [Unreleased]` по каждой фазе.

## Распределение между ИИ 1 / ИИ 2

| Фаза | ИИ 1 (backend) | ИИ 2 (фронт) |
|------|----------------|---------------|
| U0 | — | подключение MCP, baseline-скрины |
| U1 | новый роут `GET /admin/_/components` (тонкий, рендерит шаблон) | вся библиотека макросов + tokens + storybook |
| U2 | расширение `/api/admin/stats` для AI Value (если нужно) | бейдж, banner, mini-metric, ROI toast |
| U3 | — (URL не меняется на бэкенде) | sidebar reorg, табы, mapping `currentTab`, объединения |
| U4 | — (поля Organization не меняются) | весь редизайн Настроек |
| U4.5 | **Готово (2026-05):** triage в `User.meta_json`, post-iiko статусы + `fulfillment_events`, `POST …/feedback/upsell-rule`, `PackagingRule.scope`, миграция + SQLite-патч | **Готово:** фильтры чатов, канбан-колонки post-iiko, кнопки feedback из модалки заказа, пагинация канбана, `x-ignore` на графиках |
| U5 | — | **Готово (2026-05):** миграция разделов на макросы + `ds-*` (см. § Phase U5 выше) |
| U6 | — | **Готово (2026-05):** bottom-sheet модалок, safe-area, touch 44px, канбан `data-kanban-col` + стрелки, Esc-стек |
| U7 | обновить `IMPLEMENTATION_PLAN.md` | **Готово (2026-05):** `docs/UI_DESIGN_SYSTEM.md`, секция «Зоны UI» в `PARALLEL_AI_PLAN.md`, `CHANGELOG` |

ИИ 1 параллельно продолжает свою основную ветку: E0.1 раскол `_monolith.py` → E2.2.B → E2.3.B → E5. UI-фазы его почти не блокируют.

## Critical files

**Создаются:**
- `app/templates/components/_button.html` … (16 файлов макросов)
- `app/templates/_components_storybook.html`
- `docs/UI_DESIGN_SYSTEM.md`
- `docs/UI_REDESIGN_NOTES.md`
- `docs/ui/baseline/*.png` (скрины для ревью)

**Изменяются:**
- `app/static/css/admin.css` — добавление `:root { --... }` блока, кастомные `rm-*` классы через `@apply` или чистый CSS.
- `app/templates/admin.html` — поэтапная замена inline-блоков на вызовы макросов.
- `app/static/js/admin-app.js` — без принципиального рефакторинга в фазах U0–U7; небольшие правки для bind компонентов и mapping `currentTab`. **Phase U4.5** (п. 4 и связанные фичи) может потребовать целенаправленного рефакторинга чатов/канбана — согласовать с эпиком E-JS-Refactor.
- `app/api/admin/_monolith.py` (или соответствующий подмодуль после E0.1) — добавление роута `/admin/_/components` (~10 строк) и при необходимости расширение `/stats`.
- `app/main.py` — `include_router` для нового UI-роута, если он отдельным router'ом.
- `IMPLEMENTATION_PLAN.md`, `PARALLEL_AI_PLAN.md`, `CHANGELOG.md` — документация.

**Существующее переиспользуем:**
- `app/services/owner_roi.py` — `aggregate_org_window`, `build_today_narrative_ru`, `build_achievements_week` для ROI banner и тоста.
- `app/services/intelligence_analytics.py` — `upsell_stats_from_items_json` для бейджа в шапке.
- `app/static/js/admin-app.js` → `flashToast` для ROI Toast.
- `app/templates/admin.html` → существующие макросы `ui_card`, `order_card_*`, `order_status_badge` — переезжают в `components/` под теми же именами.

## Verification

**После каждой фазы:**
1. `pytest` — зелёный (UI-фазы Python почти не трогают, но storybook-роут проверяем).
2. Chrome DevTools MCP: открыть `/admin`, залогиниться, пройти по всем разделам — нет console-error.
3. `/admin/_/components` рендерится, все макросы видны.
4. Скрин через MCP сравнивается с baseline (визуальная регрессия).

**Финальный e2e после Phase U6:**
1. Логин → Дашборд (видим AI Value бейдж в шапке + welcome banner) → клик по бейджу → Вклад ИИ открывается.
2. Заказы → канбан → drag-and-drop карточки между колонками (mobile + desktop) → модалка заказа открывается через единый компонент.
3. Меню → редактирование позиции → переключение таб «Стоп-лист» → возврат на каталог.
4. Настройки → каждая из 8 групп открывается, поля сохраняются, live-preview Упаковки работает, график inline редактируется.
5. Logout → request-access → onboarding → возврат в админку.
6. Mobile (320×640): сайдбар через гамбургер, модалки через bottom-sheet, all touch-targets ≥ 44px.
7. Lighthouse: Performance ≥ 80, Accessibility ≥ 90, Best Practices ≥ 90 на Дашборде / Заказах / Меню / Настройках.

**Через MCP** автоматизируется как сценарий, прогоняется в конце каждой фазы.

## Не входит в этот план (отдельные эпики)

- Vite + Vue/React миграция — отдельный эпик после стабилизации редизайна, если решим переходить (см. [docs/VITE_DECISION.md](VITE_DECISION.md)).
- Dark theme — токены готовы, но дизайн не делаем; добавится отдельным эпиком при необходимости.
- Раскол `admin-app.js` (~6600 строк) на модули — отдельный эпик E-JS-Refactor; в текущем плане только мелкие правки. Критерии из **Phase U4.5** (п. 4) при согласовании приоритета переносятся в этот эпик или выполняются точечно в админке.
- Переход с Alpine.js на другую реактивную систему.
- Тяжёлая BI-аналитика (cohort, funnel, retention) — расширение Вклад ИИ в отдельный эпик после U5.
