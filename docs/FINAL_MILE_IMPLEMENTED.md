# Final Mile Implementation

Historical Final Mile module index. Current product status lives in [`ROADMAP.md`](ROADMAP.md), deployment checks in [`DEPLOY_RUNBOOK.md`](DEPLOY_RUNBOOK.md), and Intelligence OS layers in [`INTELLIGENCE_OS_PLAN.md`](INTELLIGENCE_OS_PLAN.md).

## SupplyMind

- `inventory_stock_snapshots` is the stock read model.
- `POST /api/admin/intelligence/inventory/snapshots/bulk` upserts stock data.
- `GET /api/admin/intelligence/inventory/stock-alerts` returns real SKU alerts.
- `POST /api/admin/intelligence/supplymind/drafts` creates a purchase draft from low-stock alerts.
- `GET /api/admin/intelligence/supplymind/drafts` lists purchase drafts.
- **iiko Office sync (code ✅):** [`iiko_office_client.py`](app/integrations/iiko_office_client.py), [`iiko_inventory_sync.py`](app/services/iiko_inventory_sync.py) — `POST/GET /api/admin/inventory/sync-iiko|sync-status`, ARQ cron ~6h, UI in `aiCenterTab=final_mile`. **Gate:** live Office credentials + pilot smoke.

## StaffMind

- `staff_onboarding_sessions` stores WhatsApp-compatible onboarding state.
- `POST /api/admin/intelligence/staffmind/onboarding` starts onboarding.
- `POST /api/admin/intelligence/staffmind/onboarding/{session_id}/message` answers from `KnowledgeItem`.
- `GET /api/admin/intelligence/staffmind/onboarding` lists sessions.

## Visibility

- `os.audit` websocket push now includes `organization_id`, so org-scoped admin websocket subscribers receive AuditLog entries without refresh.
- Daily OS Digest has a preview endpoint: `GET /api/admin/intelligence/daily-os-digest/preview`.
- ARQ runs `daily_os_digest_scheduled_tick` in the 09:00 org-timezone window.

## GuestCare External

- Imported reviews are stored in `external_reviews`.
- **Auto-sync:** 2GIS via `review_url_2gis` only (parser + cron). Google URL — manual import only (Places API not in scope).
- Manual import still accepts parsed `author`, `rating`, and `text` payloads.
- Reply drafts are persisted per review.

## Voice AI

- Twilio Media Streams MVP is guarded by `Organization.meta_json.voice_ai_enabled`.
- `GET /api/admin/intelligence/voice/status` reports readiness.
- `POST /api/admin/intelligence/voice/config` toggles voice mode (`stt_fallback` | `realtime`).
- `voice_call_logs` records call status and transcripts.
- **OpenAI Realtime (code ✅):** [`voice_realtime/`](app/services/voice_realtime/) + [`twilio_routing.py`](app/services/twilio_routing.py); webhook branches on `voice_ai_mode`. **Gate:** staging call on real Twilio — [`docs/VOICE_AI_SPIKE.md`](VOICE_AI_SPIKE.md), [`docs/VOICE_STAGING_CHECKLIST.md`](VOICE_STAGING_CHECKLIST.md).

## Deployment Notes

- Run `alembic upgrade head`; current head is `20260604_iiko_last_error_text`.
- Restart web and ARQ worker processes after deploy, because new routes, models, cron ticks (`daily_os_digest_scheduled_tick`, `iiko_inventory_sync`, `external_reviews_sync`) load at import time.
- Make sure org `timezone` values are valid IANA names; Daily OS Digest uses the organization timezone window.
- Voice AI is disabled by default. Enable per organization with `POST /api/admin/intelligence/voice/config`.
- SupplyMind: bulk snapshots, iiko Office sync, or manual pushes all land in `inventory_stock_snapshots`.

## Admin UI status (historical Final Mile snapshot)

| Module | Backend | Admin UI | Gap status |
|--------|---------|----------|------------|
| OS Decision Feed + `os.audit` | ✅ | ✅ | Closed |
| GuestCare External + sync | ✅ 2GIS auto-sync | ✅ | **Google auto-sync closed (WONTFIX Places API)** — manual import only |
| Daily OS Digest | ✅ preview + cron | ✅ | Ops: [`TELEGRAM_DIGEST_STAGING.md`](TELEGRAM_DIGEST_STAGING.md) |
| SupplyMind + iiko Office | ✅ | ✅ connections + final_mile | **iiko Office RBAC UI ✅**; ops: [`FINAL_MILE_OPS_SIGNOFF.md`](FINAL_MILE_OPS_SIGNOFF.md) §A |
| StaffMind onboarding | ✅ | ✅ team settings | Closed |
| Voice AI Realtime | ✅ code | ✅ toggle | Ops: [`FINAL_MILE_OPS_SIGNOFF.md`](FINAL_MILE_OPS_SIGNOFF.md) §B, ROADMAP `[ ]` until sign-off |
| Shift G10 | ✅ | ✅ shift tab | Closed |
| Browser smoke | pytest HTTP + checklist | — | [`FINAL_MILE_BROWSER_SMOKE.md`](FINAL_MILE_BROWSER_SMOKE.md), `tests/test_final_mile_smoke.py` |
| Admin i18n ru/kk | — | ru inline | **Deferred** — ROADMAP L141, not Final Mile blocker |

## GuestCare product (2026-05)

- **Auto-sync:** only `review_url_2gis` (cron + button).
- **Google URL:** manual import in GuestCare tab only; no Places API integration planned.

## Remaining ops (not code)

1. iiko Office live sync on pilot venue — [`FINAL_MILE_OPS_SIGNOFF.md`](FINAL_MILE_OPS_SIGNOFF.md) §A
2. Voice Realtime Twilio call — §B + [`VOICE_STAGING_CHECKLIST.md`](VOICE_STAGING_CHECKLIST.md)
3. Manual browser pass — [`FINAL_MILE_BROWSER_SMOKE.md`](FINAL_MILE_BROWSER_SMOKE.md)
