# Remaining Updates After Final Mile

This file tracks what is still needed outside the backend MVP that is already implemented.

## Must Update Before Production

- **Database:** run `alembic upgrade head`; expected head is `20260521_final_mile` (plus `20260522_iiko_office_inventory` when inventory sync PR merges).
- **Workers:** restart ARQ workers so `daily_os_digest_scheduled_tick` and (after merge) `iiko_inventory_sync` are registered.
- **Frontend (wired):** `aiCenterTab=final_mile` — Daily OS Digest preview, SupplyMind stock alerts/drafts, Voice AI enable/mode. `_tab_settings_team.html` — StaffMind onboarding.
- **Permissions:** decide which staff roles can create supply drafts, change checklist status, start StaffMind onboarding, and toggle Voice AI.
- **Operations:** confirm every organization has a valid `timezone`, especially for the 09:00 Daily OS Digest.
- **Staging checks:** Telegram digest delivery (`TELEGRAM_BOT_TOKEN`, ops chat IDs); WebSocket `os.audit` fanout; Twilio voice stream (STT and, after merge, Realtime).

## Integration Epics (2026-05) — status

| Epic | Code in repo | Production gate |
|------|--------------|-----------------|
| **iiko Office inventory sync** | ✅ `iiko_office_client`, `iiko_inventory_sync`, `inventory_sync` router, ARQ cron, Final Mile status/manual sync UI, [`tests/test_iiko_inventory_sync.py`](tests/test_iiko_inventory_sync.py) | Per-org `integration_config_json.iiko_office` + smoke against **live** iiko Office |
| **SupplyMind checklist** | ✅ lifecycle API + CSV + UI «Чеклисты закупки»; tests in `test_ultimate_platform_sprint.py` | Role gates + operator smoke in AI Center |
| **Voice Realtime** | ✅ `voice_realtime/*`, `twilio_routing`, webhook branch; tests `test_voice_realtime.py`, `test_twilio_routing.py`, `test_voice_staging.py` | Manual staging call (`mode=realtime`) on real Twilio Media Stream + latency/cost notes |

### iiko Office inventory sync

- **Not iiko Cloud:** menu/stop-list stay on existing [`IikoClient`](app/integrations/iiko_client.py); warehouse balances need **iiko Office** REST (or documented equivalent).
- **Read model:** upsert [`inventory_stock_snapshots`](app/db/models.py) with `source="iiko_office"`, scoped by `organization_id` (+ optional `location_id`).
- **Ops:** manual `POST /api/admin/inventory/sync-iiko`; status `GET /api/admin/inventory/sync-status`; background sync every ~6 hours.
- **Config per org:** `integration_config_json["iiko_office"]` = `{ host, login, password_enc, store_id, department_id }` (secrets encrypted like other iiko credentials).
- **Rollout:** start with one friendly venue because Office may require VPN/static IP/port forwarding; keep manual/bulk snapshots as the fallback path.

### SupplyMind — internal checklist (no iiko PO export)

- **Product decision:** `supply_purchase_drafts` = operator **checklist**, not a purchase order in iiko. Export = **CSV for supplier/kitchen**, not `POST` into iiko Office.
- **API (target):** `GET/PATCH /supplymind/drafts/{id}`, `GET /supplymind/drafts/{id}/export?format=csv`.
- **UI copy:** «Чеклист закупки», not «Заказ в iiko».

### Voice — OpenAI Realtime production connector

- **`stt_fallback` (today):** Twilio μ-law buffer → Whisper → `process_message` → Twilio Say.
- **`realtime` (target):** bidirectional bridge Twilio Media Streams ↔ OpenAI Realtime; minimal tools (`lookup_menu` stub, `escalate_to_whatsapp`); org resolved from `To` via [`twilio_routing`](app/services/twilio_routing.py) (`Organization.meta_json.twilio_voice_number`).
- **Enable:** `POST /api/admin/intelligence/voice/config` with `mode=realtime`; env: `OPENAI_REALTIME_MODEL`, `OPENAI_REALTIME_VOICE`, `VOICE_REALTIME_MAX_SESSION_SEC`.
- **Fallback:** if Realtime session fails → log + TwiML Say + hangup; STT path must keep working.
- **Economics:** keep `stt_fallback` as default for mass-market; Realtime is premium/experimental until cost-per-minute is measured in staging.

## External Integrations Still Needed (unchanged backlog)

- **2GIS/Google reviews:** connect real scraper/API ingestion to `external_reviews` (backend accepts parsed payloads).
- **Telegram delivery check:** verify `TELEGRAM_BOT_TOKEN` and ops chat IDs for Daily OS Digest in staging.

## Docs That Should Be Cleaned Later

- `README.md` — legacy encoded section; для релиза предпочитать [`docs/FINAL_MILE_IMPLEMENTED.md`](FINAL_MILE_IMPLEMENTED.md) и [`docs/ROADMAP.md`](ROADMAP.md).
- **Admin i18n ru/kk** — не внедрён; UI остаётся русским inline ([`docs/ROADMAP.md`](ROADMAP.md) backlog).

## Suggested Next Engineering Sprint

- Merge integration epics above; run `alembic upgrade head`; restart workers.
- Harden Final Mile admin UI permissions and browser smoke (SupplyMind checklist, StaffMind, Voice toggle, digest preview).
- Staging smoke: Telegram Daily OS Digest, WebSocket `os.audit`, Twilio voice (STT + Realtime).
- Add iiko Office inventory adapter tests with captured sample payloads (`tests/fixtures/iiko_office/`).
