# RestoMind Admin — дизайн-система

Финальная спецификация UI админки после фаз **U1–U7** ([UI_REDESIGN_PLAN.md](UI_REDESIGN_PLAN.md)). Стек: **Jinja2**, **Alpine.js**, **Tailwind CSS** (сборка в `app/static/css/admin.css`), **Chart.js**.

---

## Принципы

| Принцип | Смысл |
|--------|--------|
| **Compact density** | Плотная сетка, без лишних полей; KPI и таблицы читаются с первого экрана. |
| **Value-first** | Владелец видит вклад ИИ (бейдж в шапке, мини-метрики, ROI) без охоты по вкладкам — см. Phase U2. |
| **Mobile-friendly** | На `<sm` модалки ведут себя как **bottom-sheet** (одна колонка, safe-area, крупные таргеты **≥ 44×44 px**). |
| **Dark-ready** | Токены в `:root` как CSS-переменные; тёмная тема не реализована, но имена и структура под неё допускают расширение. |

---

## CSS-переменные (`:root`)

Задаются в [`src/css/admin-input.css`](../src/css/admin-input.css) и попадают в собранный [`app/static/css/admin.css`](../app/static/css/admin.css).

| Переменная | Назначение |
|------------|------------|
| `--color-text` | Основной текст (`#0f172a`). |
| `--color-text-muted` | Вторичный текст (`#64748b`). |
| `--color-surface` | Фон карточек/панелей (`#ffffff`). |
| `--color-surface-muted` | Приглушённый фон (`#f8fafc`). |
| `--color-border` | Границы и разделители (`#e5e7eb`). |
| `--space-2`, `--space-3`, `--space-4` | Базовые отступы (0.5 / 0.75 / 1 rem). |
| `--radius-lg`, `--radius-xl` | Радиусы (0.75 / 1 rem). |

Палитра бренда для акцентов в интерфейсе наследуется из **`Organization.brand_color_hex`** через JS ([`app/static/js/admin-brand-tokens.js`](../app/static/js/admin-brand-tokens.js), подключение в админке): производные HSL подставляются в работу чата/превью; утилитарные классы Tailwind `brand-*` в **новых** блоках избегаем — предпочтительны токены и классы `ds-*`.

**Z-index:** модальные слои используют классы `.ds-modal-backdrop` (50) / `.ds-modal-panel` (51), drawer — 60/61, тосты — 70 (см. собранный CSS).

---

## Каталог компонентов (Jinja)

Источник макросов: [`app/templates/components/`](../app/templates/components/). Живая витрина: **`GET /admin/_/components`** (доступ superadmin или `APP_DEBUG=true`) — [`_components_storybook.html`](../app/templates/_components_storybook.html).

Ниже — имена файлов и типичный вызов. Полные сигнатуры смотрите в начале каждого файла.

| Файл | Назначение |
|------|------------|
| `_button.html` | `btn(...)` — варианты primary / secondary / ghost / danger / success, размеры sm / md / lg. |
| `_card.html` | `card(...)` — обёртка секции через `caller()`. |
| `_modal.html` | `modal(id, title, size, ...)` — `role="dialog"` `aria-modal="true"`, связка `aria-labelledby` с заголовком. На `<640px` панель крепится к низу экрана (bottom-sheet). |
| `_drawer.html` | `drawer(id, title, ...)` — нижняя штора; заголовок — `<h2 id="{{ id }}-title">` для `aria-labelledby`. |
| `_tabs.html` | Вкладки с вариантами underline / pills / sidebar. |
| `_section_header.html` | Заголовок секции + действия. |
| `_kpi_card.html` | KPI-плитки (`ds-kpi`). |
| `_table.html` | Таблица с обёрткой `ds-table-wrap`. |
| `_badge.html`, `_status_badge.html` | Бейджи и статусы заказов/броней. |
| `_input.html`, `_select.html`, `_textarea.html`, `_toggle.html` | Поля форм в стиле `ds-*`. |
| `_color_picker.html`, `_file_dropzone.html` | Специализированные контролы. |
| `_empty_state.html`, `_skeleton.html` | Пустые состояния и загрузка. |
| `_segmented.html` | «Пилюли» переключения (`ds-segmented`), минимальная высота кнопок **44px**. |
| `_app_shell.html` | Каркас чата / трёхпанельный layout. |
| `_settings_tabs.html` | Навигация по группам настроек. |

**Наследие совместимости:** `ui_card`, `order_card_*`, `order_status_badge` — см. `_ui_card.html`, `_order_card.html`.

### Скриншоты (baseline) и витрина компонентов

Живые макросы: **`GET /admin/_/components`** (superadmin или `APP_DEBUG=true`). Ниже — фиксированные скрины из [`docs/ui/baseline/`](../docs/ui/baseline/) (регрессия визуала по разделам; не замена storybook, а доказательный ряд экранов в репозитории).

Дополнительно: **mobile review** со скриншотами из Playwright и приоритезированным списком улучшений — [`docs/ui/mobile-review/README.md`](ui/mobile-review/README.md).

| Дашборд | Заказы | Меню |
|--------|--------|------|
| ![Дашборд](ui/baseline/admin_dashboard.png) | ![Заказы](ui/baseline/admin_orders.png) | ![Меню](ui/baseline/admin_menu.png) |

| Настройки: ресторан | Бренд | Интеграции |
|---------------------|-------|------------|
| ![Профиль](ui/baseline/admin_settings_restaurant.png) | ![Бренд](ui/baseline/admin_settings_branding.png) | ![Интеграции](ui/baseline/admin_settings_connections.png) |

| Команда | Здоровье | Техническое | Бот / ИИ |
|---------|----------|-------------|----------|
| ![Команда](ui/baseline/admin_settings_team.png) | ![Health](ui/baseline/admin_settings_health.png) | ![Technical](ui/baseline/admin_settings_technical.png) | ![Бот](ui/baseline/admin_settings_bot_test.png) |

Дополнительно в baseline: аналитика, чаты, брони, стоп-лист, «Вклад ИИ», очередь оператора и др. — см. [README в baseline](ui/baseline/README.md).

---

## Информационная архитектура (IA)

Старая → новая структура (меню сайдбара), см. [UI_REDESIGN_PLAN § Phase U3](UI_REDESIGN_PLAN.md).

| Было | Стало |
|------|--------|
| Дашборд, Аналитика, Вклад ИИ отдельно | Дашборд с табами `overview` / `analytics`; отдельная страница «Вклад ИИ» сохранена |
| Меню + Стоп-лист отдельно | Один раздел «Меню», табы `catalog` / `stoplist` |
| Требует внимания + Ошибки отдельно | «Помощь клиентам»: табы `incidents` / `failed_tasks` |
| Заказы: разрозненные режимы | Табы канбан / таблица внутри «Заказы» |

**Deep-links:** маппинг старых hash/`currentTab` в [`admin-app.js`](../app/static/js/admin-app.js).

---

## Anti-patterns

Не добавлять в новых блоках:

- «Сырые» карточки вида `rounded-2xl bg-white shadow-sm` без макроса `_card` / классов `ds-card`.
- Произвольный `z-index` на модалках — только стек `ds-modal-*` / `ds-drawer-*`.
- Inline full-screen оверлеи вместо `_modal.html` / согласованного паттерна `ds-modal-backdrop` + `ds-modal-panel`.
- Иконки-кнопки без `aria-label` / без `aria-hidden` на декоративном SVG.

---

## Миграция: новая страница / блок

1. Проверить [`app/templates/components/`](../app/templates/components/) — есть ли готовый макрос.  
2. Если нет — расширить макрос (параметры, слот `caller()`), а не копировать разметку.  
3. Стили только через **`ds-*`** и CSS-переменные из `:root`; новые цвета бренда — через токены, не через `bg-brand-600` в новых секциях ([restomind-zones](mdc:.cursor/rules/restomind-zones.mdc) / [ui-redesign](mdc:.cursor/rules/ui-redesign.mdc)).  
4. Модалка на мобильном автоматически получает нижнюю панель и отступ `safe-area`; при отдельном сценарии — `_drawer.html`.  
5. Клавиатура: глобальный **Escape** закрывает верхний слой оверлея ([`handleGlobalKeydown`](../app/static/js/admin-app.js)); для канбана — **стрелки** между колонками с `[data-kanban-col]`.  
6. После правок: `npm run build:admin-css` если меняли [`src/css/admin-input.css`](../src/css/admin-input.css); `pytest`; смоук в браузере.

---

## Доступность и мобильная приёмка (Phase U6)

- **Touch:** цели ≥ **44×44 px** — классы `ds-segmented`, `ds-btn-*`, массовое `min-h-[44px]` для компактных кнопок в [`admin.html`](../app/templates/admin.html).  
- **Модалки:** `role="dialog"` `aria-modal="true"`; нижняя «ручка» и `padding-bottom: max(1rem, env(safe-area-inset-bottom))` на узком экране.  
- **Lighthouse (mobile), авторизованная сессия:** скрипт [`scripts/run_admin_lighthouse.mjs`](../scripts/run_admin_lighthouse.mjs) + `npm run lh:admin` (нужен запущенный `uvicorn`). Сохраняет **`docs/ui/lighthouse/summary.json`**, таблицу в **`docs/ui/lighthouse/README.md`** и полные отчёты в `docs/ui/lighthouse/reports/` (каталог в `.gitignore`). Целевые пороги из [UI_REDESIGN_PLAN.md](UI_REDESIGN_PLAN.md): **Accessibility ≥ 90**, **Performance ≥ 80**, **Best practices ≥ 90** на дашборде, заказах, меню и настройках (в скрипт включены все 8 подвкладок настроек).  
- Первый запуск Playwright: `npx playwright install chromium`.

---

## Связанные документы

- [UI_REDESIGN_PLAN.md](UI_REDESIGN_PLAN.md) — фазы U0–U7 и DoD.  
- [UI_REDESIGN_NOTES.md](UI_REDESIGN_NOTES.md) — визуальный долг / baseline.  
- [IMPLEMENTATION_PLAN.md](../IMPLEMENTATION_PLAN.md) — эпик **E-UI**.  
- [PARALLEL_AI_PLAN.md](../PARALLEL_AI_PLAN.md) — зоны ответственности UI.
