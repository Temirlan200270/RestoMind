# Remaining Updates After Final Mile

This file tracks what is still needed outside the backend MVP that is already implemented.

## Must Update Before Production

- **Database:** run `alembic upgrade head`; expected head is `20260521_final_mile`.
- **Workers:** restart ARQ workers so `daily_os_digest_scheduled_tick` is registered.
- **Frontend:** add UI controls for:
  - SupplyMind stock snapshots and purchase drafts.
  - StaffMind onboarding sessions and Q&A.
  - Voice AI status/config.
  - Daily OS Digest preview.
- **Permissions:** decide which staff roles can create supply drafts, start StaffMind onboarding, and toggle Voice AI.
- **Operations:** confirm every organization has a valid `timezone`, especially for the 09:00 Daily OS Digest.

## External Integrations Still Needed

- **iiko Office inventory sync:** map real stock/balance endpoints into `inventory_stock_snapshots`.
- **iiko purchase order export:** decide whether `supply_purchase_drafts` should be exported to iiko as a draft invoice/order or stay as an internal checklist.
- **2GIS/Google reviews:** connect real scraper/API ingestion to the existing `external_reviews` table. The backend already accepts parsed `author`, `rating`, and `text`.
- **OpenAI Realtime Voice:** implement the native realtime connector behind `Organization.meta_json.voice_ai_mode = realtime`. Current production path is Twilio Media Streams -> STT fallback -> existing message pipeline.
- **Telegram delivery check:** verify `TELEGRAM_BOT_TOKEN` and org/global ops chat IDs in staging for Daily OS Digest.

## Docs That Should Be Cleaned Later

- `docs/ROADMAP.md` still has older unchecked lines for websocket audit push, daily digest, and Voice AI. The implemented status is now in `docs/FINAL_MILE_IMPLEMENTED.md`.
- `docs/OS_TRANSITION_PLAN.md` still describes Phase 6 as “next level” in a few older sections. Treat `docs/FINAL_MILE_IMPLEMENTED.md` as the current source for Final Mile status.
- `README.md` still has older module wording in a legacy encoded section. Keep it, but prefer this file and `docs/FINAL_MILE_IMPLEMENTED.md` for release notes until README is normalized to UTF-8.

## Suggested Next Engineering Sprint

- Add admin UI for SupplyMind, StaffMind, Voice AI, and digest preview.
- Add role-gated dependencies around the new admin endpoints.
- Add real iiko inventory adapter tests with captured sample payloads.
- Add staging smoke for Twilio voice incoming/stream flow.
