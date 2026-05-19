# Final Mile Implementation

Implemented backend MVPs for the remaining Ultimate Platform modules.

## SupplyMind

- `inventory_stock_snapshots` is the stock read model.
- `POST /api/admin/intelligence/inventory/snapshots/bulk` upserts stock data.
- `GET /api/admin/intelligence/inventory/stock-alerts` returns real SKU alerts.
- `POST /api/admin/intelligence/supplymind/drafts` creates a purchase draft from low-stock alerts.
- `GET /api/admin/intelligence/supplymind/drafts` lists purchase drafts.

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
- Import accepts parsed `author`, `rating`, and `text` payloads for 2GIS/Google review data.
- Reply drafts are persisted per review.

## Voice AI

- Twilio Media Streams MVP is guarded by `Organization.meta_json.voice_ai_enabled`.
- `GET /api/admin/intelligence/voice/status` reports readiness.
- `POST /api/admin/intelligence/voice/config` toggles voice mode.
- `voice_call_logs` records call status and transcripts.
- OpenAI Realtime remains the next connector step behind `voice_ai_mode = realtime`.

## Deployment Notes

- Run `alembic upgrade head`; current head is `20260521_final_mile`.
- Restart web and ARQ worker processes after deploy, because new routes, models, and `daily_os_digest_scheduled_tick` are loaded at import time.
- Make sure org `timezone` values are valid IANA names; Daily OS Digest uses the organization timezone window.
- Voice AI is disabled by default. Enable per organization with `POST /api/admin/intelligence/voice/config`.
- SupplyMind works with pushed stock snapshots today. Real iiko Office stock sync still needs credentials and endpoint mapping.
