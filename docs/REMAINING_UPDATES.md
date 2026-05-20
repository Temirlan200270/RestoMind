# Remaining Updates After Final Mile

This file tracks what is still needed outside the backend MVP that is already implemented.

## Must Update Before Production

- **Database:** run `alembic upgrade head`; expected head is `20260521_final_mile`.
- **Workers:** restart ARQ workers so `daily_os_digest_scheduled_tick` is registered.
- **Frontend (UI gaps — backend уже готов):**
  - SupplyMind: список/создание purchase drafts, просмотр stock alerts.
  - StaffMind: онбординг-сессии и Q&A — в `admin-app.js` есть `loadStaffMindOnboarding` / `startStaffMindOnboarding` / `askStaffMind`, но `_tab_settings_team.html` их не вызывает.
  - Voice AI: toggle `voice_ai_enabled` / mode через `POST /voice/config`.
  - Daily OS Digest: preview panel через `GET /daily-os-digest/preview`.
- **Permissions:** decide which staff roles can create supply drafts, start StaffMind onboarding, and toggle Voice AI.
- **Operations:** confirm every organization has a valid `timezone`, especially for the 09:00 Daily OS Digest.
- **Staging checks:** Telegram digest delivery (`TELEGRAM_BOT_TOKEN`, ops chat IDs); WebSocket `os.audit` fanout в браузере после deploy.

## External Integrations Still Needed

- **iiko Office inventory sync:** map real stock/balance endpoints into `inventory_stock_snapshots`.
- **iiko purchase order export:** decide whether `supply_purchase_drafts` should be exported to iiko as a draft invoice/order or stay as an internal checklist.
- **2GIS/Google reviews:** connect real scraper/API ingestion to the existing `external_reviews` table. The backend already accepts parsed `author`, `rating`, and `text`.
- **OpenAI Realtime Voice:** implement the native realtime connector behind `Organization.meta_json.voice_ai_mode = realtime`. Current production path is Twilio Media Streams -> STT fallback -> existing message pipeline.
- **Telegram delivery check:** verify `TELEGRAM_BOT_TOKEN` and org/global ops chat IDs in staging for Daily OS Digest.

## Docs That Should Be Cleaned Later

- `README.md` — legacy encoded section; для релиза предпочитать [`docs/FINAL_MILE_IMPLEMENTED.md`](FINAL_MILE_IMPLEMENTED.md) и [`docs/ROADMAP.md`](ROADMAP.md).
- **Admin i18n ru/kk** — не внедрён; UI остаётся русским inline ([`docs/ROADMAP.md`](ROADMAP.md) backlog).

## Suggested Next Engineering Sprint

- Wire admin UI: SupplyMind drafts, StaffMind (reuse existing JS), Voice toggle, digest preview.
- Add role-gated dependencies around the new admin endpoints.
- Staging smoke: Telegram Daily OS Digest, WebSocket `os.audit`, Twilio voice stream.
- Add real iiko inventory adapter tests with captured sample payloads.
