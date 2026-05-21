# Daily OS Digest — staging checklist (Telegram)

Операционный чеклист перед включением cron `daily_os_digest_scheduled_tick` в staging/prod.

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
- [`docs/REMAINING_UPDATES.md`](REMAINING_UPDATES.md) — общий ops backlog
