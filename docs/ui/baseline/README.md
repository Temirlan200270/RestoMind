# Baseline скриншоты админки (Phase U0)

| Поле | Значение |
|------|----------|
| Дата | 2026-05-21 (после Role-first IA Sprint 5 — переснимите baseline) |
| Коммит | working tree P5 Role-first pivot |
| Viewport | 1440×900 (Playwright Chromium, headless) |
| Вход | **«Посмотреть демо»** на `/admin` (demo-login; для baseline вкладок — после Esc из pitch или сразу explore) |
| Автоматизация | [`scripts/capture_admin_u0_baseline.py`](../../scripts/capture_admin_u0_baseline.py) — Playwright Chromium headless; тот же маршрут и hash, что в Phase U0 для MCP (Chrome DevTools / Playwright MCP можно использовать вручную для пересъёмки). После запуска скрипта в той же оболочке задайте `APP_DEBUG=true` перед `pytest`, если в окружении было `APP_DEBUG=false` (иначе `SessionMiddleware` с `https_only` ломает cookie в тестах по `http://test`). |

## Файлы и deep-link (`location.hash`)

| Файл | Hash |
|------|------|
| `admin_dashboard.png` | `#dashboard` |
| `admin_settings_bot_test.png` | `#settings/bot_test` |
| `admin_settings_technical.png` | `#settings/technical` |
| `admin_settings_health.png` | `#settings/health` |
| `admin_settings_team.png` | `#settings/team` |
| `admin_settings_smart_sales.png` | `#settings/smart_sales` |
| `admin_settings_connections.png` | `#settings/connections` |
| `admin_settings_branding.png` | `#settings/branding` |
| `admin_settings_restaurant.png` | `#settings/restaurant` |
| `admin_errors.png` | `#errors` |
| `admin_incidents.png` | `#incidents` |
| `admin_operator_queue.png` | `#operator_queue` |
| `admin_bookings.png` | `#bookings` |
| `admin_chats.png` | `#chats` |
| `admin_menu.png` | `#menu` |
| `admin_stoplist.png` | `#stoplist` |
| `admin_orders.png` | `#orders` |
| `admin_analytics.png` | `#analytics` |
| `admin_ai_value.png` | `#ai_value` |
| `admin_ai_center_final_mile.png` | `#ai_center?tab=final_mile` |

Тот же обход можно повторить вручную через Chrome DevTools MCP / Playwright MCP (см. [`docs/UI_DESIGN_SYSTEM.md`](../../UI_DESIGN_SYSTEM.md) и `scripts/run_admin_lighthouse.mjs`).
