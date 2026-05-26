# Прочие записи архива

### Добавлено (2026-06-03) — Wave 4 foundation (TG + POS)

- **Telegram customer channel:** миграция `20260603_telegram_customer` (`users.telegram_user_id`, `chat_logs.channel`); `app/services/telegram_customer.py`; shared inbound `process_inbound_message` в `webhooks.py`; ответы через Telegram Bot API; badge канала (WA/TG) в списке диалогов.
- **POS adapter Phase 1:** миграция `20260603_pos_provider` (`organizations.pos_provider`); `app/services/pos/adapters/` (`POSAdapter`, `IikoPOSAdapter`, registry); `iiko_sync_tasks` и admin menu sync через адаптер.

### Добавлено (2026-05-22) — P3 Growth: KPI официантов из iiko

- **ETL:** [`iiko_waiter_kpi_sync.py`](app/services/iiko_waiter_kpi_sync.py) — iiko Cloud deliveries + iiko Office waiter report → `waiter_registry`, `waiter_kpi_daily`, audit `iiko_sync_runs`; миграция [`20260523_p3_waiter_kpi.py`](alembic/versions/20260523_p3_waiter_kpi.py).
- **Cron:** `waiter_kpi_sync_scheduled_tick` (ежедневно 22:30 UTC) в [`worker.py`](app/worker.py).
- **Admin API:** `POST/GET /api/admin/analytics/waiter-kpi/*` — sync, рейтинг, CSV, sync-status ([`waiter_kpi.py`](app/api/admin/waiter_kpi.py)).
- **UI:** блок «Официанты» на вкладке расширенной аналитики ([`_tab_analytics.html`](app/templates/screens/_tab_analytics.html), [`admin-app.js`](app/static/js/admin-app.js)).
- **Spike:** [`docs/IIKO_WAITER_KPI_SPIKE.md`](docs/IIKO_WAITER_KPI_SPIKE.md) + fixtures Cloud/Office.
- **Тесты:** [`test_iiko_waiter_kpi_sync.py`](tests/test_iiko_waiter_kpi_sync.py), [`test_waiter_kpi_api.py`](tests/test_waiter_kpi_api.py).

### Добавлено (2026-05-20) — Location Enterprise Metrics

- **Location-aware dashboard metrics:** `/stats`, `/funnel`, `/analytics`, `/activity`, `/incidents`, `/roi/today` принимают `location_id`; при location scope не используют org-wide `DailyOrgStats` как точный источник и возвращают `location_scope`.
- **Location-aware Intelligence:** `/overview`, `/digital-twin`, `/latency`, `/os-dashboard`, `/revenue-leak`, `/inventory/stock-alerts` проверяют `assigned_location_ids`; `os-dashboard` для точки использует SQL/inventory fallback.
- **Admin UI filter:** селектор точки в шапке берёт `available_locations` из `/auth/me`, сбрасывает активный чат/заказ при смене точки и прокидывает `location_id` в dashboard, AI Center, chats и orders.
- **Тесты:** добавлены проверки location metrics и UI surface (`tests/test_location_scope.py`, `tests/test_location_ui_surface.py`).

### Добавлено (2026-05-19) — Фундамент к пилоту Фазы 5

- **Tenant / RBAC — Manager + `assigned_org_ids`:** роль `manager` в [`StaffRole`](app/db/models.py); колонка `staff_users.meta_json` (миграция [`20260519_staff_meta_json.py`](alembic/versions/20260519_staff_meta_json.py)). [`tenant_scope.py`](app/services/tenant_scope.py) — `staff_assigned_org_ids`, фильтрация `available_organizations_for_admin_session` для manager/operator. `POST /staff` принимает `assigned_org_ids` для manager.
- **`location_id` на шине:** [`emit_event`](app/services/system_events.py) всегда пишет `_location_id` (явный `location_id` или `org_id` филиала).
- **Event System — прогноз и totals:** `week_forecast.source` = `event_driven` при ≥3 днях `revenue_kzt` в `DailyOrgStats` ([`owner_dashboard.py`](app/services/owner_dashboard.py), [`analytics.py`](app/api/admin/analytics.py)). `GET /intelligence/event-stats` — полные totals (`bookings_created`, payments, `revenue_kzt`). `payment.expired` на `emit_event` в [`payment_expiry.py`](app/services/payment_expiry.py).
- **Тесты:** [`tests/test_phase5_foundation.py`](tests/test_phase5_foundation.py).

### Стратегия (2026-05-18)

- **RestoMind OS:** репозиторий официально переходит на концепцию AI Operating System. Позиционирование изменено с «AI-оператор для ресторана» на «AI-операционная система для ресторанного бизнеса». Обновлены [`README.md`](README.md) (разделы «Архитектура ядра» и «Модули»), [`codebase.md`](codebase.md) (суть проекта), [`docs/CONVENTIONS.md`](docs/CONVENTIONS.md) (Rules 9–11: Tenant Isolation, Event-First, AI Context через ContextBuilder).
- **Утверждён `OS_TRANSITION_PLAN` (5 фаз):** [`docs/OS_TRANSITION_PLAN.md`](docs/OS_TRANSITION_PLAN.md) — честная оценка текущего состояния по каждой фазе, конкретные схемы реализации, антипаттерны.
- **Запланировано — Franchise / Branch (Phase 1):** иерархия `Tenant → Organization`, флаг `Tenant.is_network`, Branch Switcher, агрегированная аналитика «Вся сеть», матрица ролей Owner/Manager/Operator. Задача в ROADMAP P1: [`docs/ROADMAP.md`](docs/ROADMAP.md).
- **Запланировано — Event System Stabilization (Phase 2) и AI Context Snapshot (Phase 3):** задачи добавлены в ROADMAP P3.

### Добавлено (2026-05-18)

- **Dialog / `is_cancel_all_message` расширение:** фраза «Отмени эти все заявки» и аналогичные натуральные формулировки (произвольный порядок слов) теперь корректно детектируются до LLM. Добавлены `_CANCEL_VERBS` + `_ALL_MARKERS` keyword-combo проверка и новые фразы в `CANCEL_ALL_PHRASES` в [`app/services/dialog_mgr.py`](app/services/dialog_mgr.py); тесты расширены в [`tests/test_dialog_session_fixes.py`](tests/test_dialog_session_fixes.py) — 16 кейсов (11 позитивных + 5 негативных, включая защиту от «отмени плов»).
- **Owner Dashboard Spec:** [`docs/OWNER_DASHBOARD_SPEC.md`](docs/OWNER_DASHBOARD_SPEC.md) — полная спецификация для реализации 4 ответов Owner Dashboard: прогноз выручки до конца недели (`_linear_week_forecast` + карточка), метрики эффективности бота на главном экране (`bot_handled_pct`, `escalations_today`), воронка потерь `GET /api/admin/funnel` (диалогов → черновиков → заказов, отток за 30 дней), рекомендации с ROI-ранжированием `top_actions` в `/api/admin/stats`.
- **OS Transition Plan:** [`docs/OS_TRANSITION_PLAN.md`](docs/OS_TRANSITION_PLAN.md) — стратегический план перехода RestoMind → AI OS по 5 фазам с честной оценкой текущего состояния (Phase 1 ~90%, Phase 2 ~40%, Phase 3 ~70% и т.д.), Strangler Pattern как основной принцип, приоритет Resource-Scope RBAC как ближайшего блокера enterprise-продаж.

