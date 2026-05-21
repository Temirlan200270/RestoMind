# Remaining Updates After Final Mile

This file tracks what is still needed outside the backend MVP that is already implemented.

## Must Update Before Production

- **Database:** run `alembic upgrade head`; expected head is `20260522_iiko_office_inventory` (chain: `20260521_final_mile` → inventory migration).
- **Workers:** restart ARQ workers so `daily_os_digest_scheduled_tick`, `iiko_inventory_sync`, and `external_reviews_sync_scheduled_tick` are registered.
- **Frontend (wired):** `aiCenterTab=final_mile` — Daily OS Digest preview, SupplyMind stock alerts/drafts, Voice AI enable/mode. `_tab_settings_team.html` — StaffMind onboarding.
- **Permissions:** decide which staff roles can create supply drafts and change checklist status; StaffMind POST onboarding — `require_staff_manager_or_admin`; Voice toggle — `require_staff_admin` on `POST /voice/config`.
- **Operations:** confirm every organization has a valid `timezone`, especially for the 09:00 Daily OS Digest.
- **Staging checks (ops gate):** Telegram digest delivery (`TELEGRAM_BOT_TOKEN`, ops chat IDs); WebSocket `os.audit` fanout; Twilio voice stream — STT path + **Realtime** manual call on real Twilio Media Stream (code ✅, see [`docs/VOICE_STAGING_CHECKLIST.md`](VOICE_STAGING_CHECKLIST.md)).

## Integration Epics (2026-05) — status

| Epic | Code in repo | Production gate |
|------|--------------|-----------------|
| **iiko Office inventory sync** | ✅ `iiko_office_client`, `iiko_inventory_sync`, `inventory_sync` router, ARQ cron, Final Mile status/manual sync UI, [`tests/test_iiko_inventory_sync.py`](tests/test_iiko_inventory_sync.py) | Per-org `integration_config_json.iiko_office` + smoke against **live** iiko Office |
| **SupplyMind checklist** | ✅ lifecycle API + CSV + UI «Чеклисты закупки»; tests in `test_ultimate_platform_sprint.py` | Role gates + operator smoke in AI Center |
| **Voice Realtime** | ✅ `voice_realtime/*`, `twilio_routing`, webhook branch; tests `test_voice_realtime.py`, `test_twilio_routing.py`, `test_voice_staging.py` | **Staging gate:** manual call (`mode=realtime`) on real Twilio + latency/cost notes ([`docs/VOICE_AI_SPIKE.md`](VOICE_AI_SPIKE.md)) |

### iiko Office inventory sync

- **Not iiko Cloud:** menu/stop-list stay on existing [`IikoClient`](app/integrations/iiko_client.py); warehouse balances need **iiko Office** REST (or documented equivalent).
- **Read model:** upsert [`inventory_stock_snapshots`](app/db/models.py) with `source="iiko_office"`, scoped by `organization_id` (+ optional `location_id`).
- **Ops:** manual `POST /api/admin/inventory/sync-iiko`; status `GET /api/admin/inventory/sync-status`; background sync every ~6 hours.
- **Config per org:** `integration_config_json["iiko_office"]` = `{ host, login, password_enc, store_id, department_id }` (secrets encrypted like other iiko credentials).
- **Rollout:** start with one friendly venue because Office may require VPN/static IP/port forwarding; keep manual/bulk snapshots as the fallback path.

### SupplyMind — internal checklist (no iiko PO export)

- **Product decision:** `supply_purchase_drafts` = operator **checklist**, not a purchase order in iiko. Export = **CSV for supplier/kitchen**, not `POST` into iiko Office.
- **API:** `GET/PATCH /supplymind/drafts/{id}`, `GET /supplymind/drafts/{id}/export?format=csv` — implemented.
- **UI copy:** «Чеклист закупки», not «Заказ в iiko».

### Voice — OpenAI Realtime (code ✅; staging gate)

- **`stt_fallback` (default prod):** Twilio μ-law buffer → Whisper → `process_message` → Twilio Say.
- **`realtime` (implemented):** bidirectional bridge Twilio Media Streams ↔ OpenAI Realtime ([`voice_realtime/`](app/services/voice_realtime/), [`twilio_routing`](app/services/twilio_routing.py)); tools `lookup_menu`, `escalate_to_whatsapp`; org from `To` via `Organization.meta_json.twilio_voice_number`.
- **Enable:** `POST /api/admin/intelligence/voice/config` with `mode=realtime` (`require_staff_admin`); env: `OPENAI_REALTIME_MODEL`, `OPENAI_REALTIME_VOICE`, `VOICE_REALTIME_MAX_SESSION_SEC`.
- **Fallback:** if Realtime session fails → log + TwiML Say + hangup; STT path unchanged.
- **Economics / gate:** keep `stt_fallback` as default for mass-market until staging measures cost-per-minute; Realtime promotion = **ops checklist**, not missing backend.

## External Integrations — status

- **GuestCare 2GIS:** ✅ auto-sync — [`guestcare_parser.py`](app/services/guestcare_parser.py), [`external_reviews_sync.py`](app/services/external_reviews_sync.py), `POST /reviews/external/sync`, ARQ cron, UI «Синхронизировать» in `aiCenterTab=guestcare`.
- **GuestCare Google:** best-effort via `meta_json.review_url_google` (static HTML often empty without Places API); production Google → official Places API.
- **Telegram delivery check:** verify `TELEGRAM_BOT_TOKEN` and ops chat IDs for Daily OS Digest in staging ([`docs/TELEGRAM_DIGEST_STAGING.md`](TELEGRAM_DIGEST_STAGING.md)).

## Docs That Should Be Cleaned Later

- `README.md` — legacy encoded section; для релиза предпочитать [`docs/FINAL_MILE_IMPLEMENTED.md`](FINAL_MILE_IMPLEMENTED.md) и [`docs/ROADMAP.md`](ROADMAP.md).
- **Admin i18n ru/kk** — не внедрён; UI остаётся русским inline ([`docs/ROADMAP.md`](ROADMAP.md) backlog).

## Suggested Next Engineering Sprint

- Run `alembic upgrade head` (`20260522_iiko_office_inventory`); restart workers.
- Harden Final Mile admin UI permissions and browser smoke (SupplyMind checklist, StaffMind, Voice toggle, digest preview).
- Staging smoke: Telegram Daily OS Digest, WebSocket `os.audit`, Twilio voice (STT + Realtime report).
- iiko Office pilot with live credentials; captured fixtures in `tests/fixtures/iiko_office/`.
