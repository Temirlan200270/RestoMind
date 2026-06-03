# Final Mile — ops sign-off (Render + Supabase + Upstash)

Engineering code and UI are ready (1018+ pytest). ROADMAP `Voice [ ]` and `iiko Office live smoke` close only after the production/staging sign-off tables below are filled.


Связанные чеклисты:
- Deploy (env + smoke): [`docs/DEPLOY_RUNBOOK.md`](DEPLOY_RUNBOOK.md)
- Voice: [`docs/VOICE_STAGING_CHECKLIST.md`](VOICE_STAGING_CHECKLIST.md)
- Telegram digest: [`docs/TELEGRAM_DIGEST_STAGING.md`](TELEGRAM_DIGEST_STAGING.md)
- Browser UI: [`docs/FINAL_MILE_BROWSER_SMOKE.md`](FINAL_MILE_BROWSER_SMOKE.md)

---

## Render env (web + ARQ worker)

| Variable | Значение задано | Примечание |
|----------|-----------------|------------|
| `DATABASE_URL` | [ ] | Supabase Postgres |
| `REDIS_URL` | [ ] | Upstash `rediss://…` |
| `REDIS_ENABLED=true` | [ ] | Перебить blueprint default |
| `REDIS_MEMORY_ONLY=false` | [ ] | Иначе Upstash игнорируется |
| `ARQ_ENABLED=true` | [ ] | Worker: `python -m arq app.worker.WorkerSettings` |
| `APP_ENV=production` | [ ] | |
| `SESSION_SECRET` | [ ] | |
| `ADMIN_PASSWORD` | [ ] | Не дефолт |
| `APP_SECRETS_FERNET_KEY` | [ ] | Для iiko Office password_enc |
| `OPENAI_API_KEY` | [ ] | |
| `TELEGRAM_BOT_TOKEN` | [ ] | |
| `TELEGRAM_ADMIN_CHAT_ID` | [ ] | |
| `PUBLIC_BASE_URL` | [ ] | URL Render web service |
| `TWILIO_*` + `OPENAI_REALTIME_*` | [ ] | Только для Voice Realtime |

---

## D. Deploy — migrations + ARQ workers

Перед ops sign-off (A–C) на **staging/production**:

| Шаг | OK | Дата | Примечание |
|-----|----|------|------------|
| `alembic upgrade head` (current head: `20260603_menu_item_lifecycle`) | [ ] | | Supabase SQL or Render shell |
| Перезапуск **web** + **ARQ worker** после деплоя | [ ] | | `python -m arq app.worker.WorkerSettings` |
| Cron registered: `daily_os_digest_scheduled_tick`, `owner_digest_scheduled_tick` | [ ] | | Telegram ops/owner digest |
| Cron registered: `external_reviews_sync_scheduled_tick`, `sales_anomaly_scheduled_tick`, `insight_delivery_scheduled_tick`, `recommendation_outcomes_scheduled_tick` | [ ] | | Reviews, anomalies, deliveries, ROI measurements |
| Cron registered: `iiko_inventory_sync`, `food_cost_scheduled_tick`, `olap_sales_scheduled_tick` | [ ] | | iiko Office/OLAP sync |

**Sign-off deploy:** _________________ Дата: _______

---

## A. iiko Office live smoke → ROADMAP SupplyMind L130

1. Admin → **Настройки → Подключения → iiko Office** (admin): host, login, password, `store_id`.
2. Final Mile → **Sync iiko** → stock alerts появились.
3. Создать draft чеклиста → CSV export.

| Шаг | OK | Дата | Примечание |
|-----|----|------|------------|
| PATCH iiko-office saved | [ ] | | |
| POST sync-iiko 200 | [ ] | | |
| stock_alerts > 0 | [ ] | | |
| supply draft created | [ ] | | |

**Sign-off:** _________________ Дата: _______

---

## B. Voice Realtime staging → ROADMAP Voice L140

По [`VOICE_STAGING_CHECKLIST.md`](VOICE_STAGING_CHECKLIST.md): 3+ звонка, latency table, org `voice_ai_mode=realtime`.

| Метрика | Замер | Target |
|---------|-------|--------|
| Time to first audio | | < 4 s |
| Turn latency | | < 1.5 s |
| lookup_menu tool | | real prices |
| escalate_to_whatsapp | | WA received |

| Шаг | OK | Дата |
|-----|----|------|
| Twilio webhook → `/api/whatsapp/voice/incoming` | [ ] | |
| Realtime call completed | [ ] | |
| voice_call_logs mode=realtime | [ ] | |

**Sign-off:** _________________ Дата: _______

---

## C. Browser smoke → [`FINAL_MILE_BROWSER_SMOKE.md`](FINAL_MILE_BROWSER_SMOKE.md)

| Sign-off | Дата |
|----------|------|
| [ ] | |

---

## После всех sign-off

1. `docs/ROADMAP.md`: `[x]` на Voice L140 и убрать «хвост prod smoke» у iiko L130.
2. `CHANGELOG.md` `[Unreleased]`: даты ops sign-off.
