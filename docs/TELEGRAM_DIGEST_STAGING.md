# Daily OS Digest — staging checklist (Telegram)

Операционный чеклист перед включением cron `daily_os_digest_scheduled_tick` в staging/prod.

## Не путать с Owner Intelligence Digest

| Digest | Cron | Расписание | Аудитория | Канал Telegram | Содержимое |
|--------|------|------------|-----------|----------------|------------|
| **Daily OS Digest** | `daily_os_digest_scheduled_tick` | ~09:00 **org TZ** (ежедневно) | Персонал смены | **Ops chat** — `telegram_ops_chat_id` или `TELEGRAM_ADMIN_CHAT_ID` | Операционная сводка смены (очередь, инциденты) |
| **Owner Intelligence Digest** | `owner_digest_scheduled_tick` | Пн 10:00–10:44 **org TZ** (еженедельно) | Владелец / руководство | **Owner chat** — `telegram_owner_chat_id` / owner-only | Финансы, ROI, маржа — **не** в ops-чат |

**Запрет:** не направлять Owner Digest в ops-чат — риск утечки финансовых KPI линейному персоналу. Чеклист ниже — **только Daily OS Digest**.

## Env (Render / `.env`)

- [ ] `TELEGRAM_BOT_TOKEN` — бот с правом писать в ops-чат
- [ ] `TELEGRAM_ADMIN_CHAT_ID` или `SUPERADMIN_TELEGRAM_CHAT_ID` — fallback chat_id
- [ ] Per-org override: **Настройки → Мой ресторан → Telegram: чат персонала** (`telegram_ops_chat_id`) — если пусто, используется глобальный env

## Workers

- [ ] ARQ worker перезапущен после деплоя (cron регистрируется при import `app.worker`)
- [ ] В логах worker нет ошибок `daily_os_digest failed org=…`

## API smoke (admin session)

- [ ] `GET /api/admin/intelligence/daily-os-digest/preview` → `ok`, поле `text` не пустое
- [ ] UI: **AI-центр → Final Mile → Daily OS Digest** — preview совпадает с API

## Telegram delivery (staging)

- [ ] Временно сдвинуть час org timezone **или** вызвать `maybe_send_daily_os_digest_for_org` из shell с тестовой org
- [ ] Сообщение пришло в правильный chat_id (org override vs global)
- [ ] HTML не ломает Telegram (`&`, `<` экранируются в [`daily_os_digest.py`](../app/services/daily_os_digest.py))
- [ ] Redis dedupe: повторный tick в тот же день **не** шлёт дубликат (`daily_os_digest:{org_id}:{day}`)

## Rollback

- [ ] Убрать `TELEGRAM_BOT_TOKEN` → cron no-op (без падения worker)
- [ ] При спаме: очистить ключ `daily_os_digest:{org_id}:{YYYY-MM-DD}` в Redis

## Связанные файлы

- [`app/services/daily_os_digest.py`](../app/services/daily_os_digest.py)
- [`app/worker.py`](../app/worker.py) — `daily_os_digest_scheduled_tick`
- [`docs/FINAL_MILE_OPS_SIGNOFF.md`](FINAL_MILE_OPS_SIGNOFF.md) — общий ops sign-off
- Owner weekly digest: [`docs/DEPLOY_RUNBOOK.md`](DEPLOY_RUNBOOK.md) §8.5 (`owner_digest_scheduled_tick`)
