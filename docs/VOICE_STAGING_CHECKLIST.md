# Voice AI — Twilio Media Stream Staging Checklist

Manual steps to validate **OpenAI Realtime** (`voice_ai_mode=realtime`) on a real Twilio number before promoting the mode beyond pilot restaurants.

Related: [`VOICE_AI_SPIKE.md`](VOICE_AI_SPIKE.md), [`app/services/voice_realtime/`](app/services/voice_realtime/).

---

## 1. Environment variables

| Variable | Required | Notes |
|----------|----------|-------|
| `PUBLIC_BASE_URL` | yes | HTTPS URL reachable by Twilio (e.g. `https://your-app.onrender.com`). Used in TwiML `<Stream url="wss://…/api/whatsapp/voice/stream">`. |
| `TWILIO_AUTH_TOKEN` | prod yes | Validates `POST /api/whatsapp/voice/incoming` signature. Can be empty locally for unit tests only. |
| `TWILIO_ACCOUNT_SID` | yes | For REST Say/Hangup fallback. |
| `OPENAI_API_KEY` | yes (realtime) | Realtime WSS auth. |
| `OPENAI_REALTIME_MODEL` | optional | Default from config (`gpt-4o-realtime-preview` or project default). |
| `OPENAI_REALTIME_VOICE` | optional | e.g. `alloy`, `verse`. |
| `VOICE_REALTIME_MAX_SESSION_SEC` | optional | Session cap (default in config). Prevents runaway billing. |
| `WHATSAPP_API_TOKEN` | recommended | For `escalate_to_whatsapp` tool — sends handoff message to caller's number. |
| `MENU_PUBLIC_URL` | optional | Appended to `lookup_menu` when many hits. |

Per organization (`Organization.meta_json`):

| Key | Example | Purpose |
|-----|---------|---------|
| `voice_ai_enabled` | `true` | Accept calls; otherwise TwiML `<Hangup/>`. |
| `voice_ai_mode` | `realtime` | Branch in `webhooks.py` → Realtime bridge (else STT fallback). |
| `twilio_voice_number` | `+77771234567` | E.164 of Twilio number that receives calls; resolved via [`twilio_routing.py`](../app/services/twilio_routing.py). |

---

## 2. Pre-flight (admin API)

1. Log in as **admin** (operators cannot `POST /voice/config`).
2. `GET /api/admin/intelligence/voice/status` — expect:
   - `enabled: true`
   - `mode: "realtime"`
   - `realtime_ready: true` (needs API key + public URL + enabled + mode)
   - `twilio_configured: true`
3. If not enabled: `POST /api/admin/intelligence/voice/config` with `{ "enabled": true, "mode": "realtime" }`.
4. Confirm org `twilio_voice_number` matches the Twilio «To» number on the voice webhook.

---

## 3. Twilio console setup

1. **Phone number** → Voice & Fax → **A call comes in**: Webhook `POST`, URL  
   `{PUBLIC_BASE_URL}/api/whatsapp/voice/incoming`
2. Enable **Media Streams** (default for `<Connect><Stream>` TwiML).
3. Optional: status callback URL for debugging (not required for MVP).
4. Caller ID / geo: use a test mobile you control (same country as WhatsApp allow list if testing escalation).

---

## 4. Test call flow

| Step | Action | Expected |
|------|--------|----------|
| 1 | Call the Twilio number from mobile | Call connects; no immediate hangup |
| 2 | Wait for greeting (Realtime TTS) | Short Russian greeting from assistant |
| 3 | Ask menu: «Сколько стоит плов?» | Tool `lookup_menu` → price from DB (not generic stub) |
| 4 | Ask order: «Хочу заказ на доставку» | Tool `escalate_to_whatsapp` → WA message on your phone (if token set) + voice confirmation |
| 5 | Hang up | Row in `voice_call_logs` with `mode=realtime`, transcript fragments if logged |
| 6 | Break test: unset `OPENAI_API_KEY`, call again | Graceful Say + Hangup (fallback message about WhatsApp) |

**STT fallback control:** set `mode: "stt_fallback"`, repeat call — should use Whisper + `process_message` path (higher latency, lower Realtime cost).

---

## 5. Latency notes (what to measure)

Record on 3+ calls (same network conditions):

| Metric | How | Target (pilot) |
|--------|-----|----------------|
| Time to first audio | Stopwatch from answer to first assistant speech | &lt; 2 s ideal, &lt; 4 s acceptable |
| Turn latency | End of user phrase → start of reply | &lt; 1.5 s with server VAD |
| Menu tool | Ask obscure dish → response | &lt; 3 s including tool round-trip |
| WA escalation | Ask complex order → WA ping | &lt; 5 s + delivery to phone |

Log sources: app logs (`Realtime connected`, `voice realtime escalate`), Twilio call debugger, OpenAI usage dashboard.

---

## 6. Cost notes (order of magnitude)

Costs are **per minute of connected Media Stream** plus **OpenAI Realtime audio tokens**. Rough planning figures (verify against current Twilio/OpenAI pricing):

| Component | Typical |
|-----------|---------|
| Twilio voice + Media Stream | ~$0.02–0.05 / min (region-dependent) |
| OpenAI Realtime (audio in/out) | Often **several ×** STT+chat; monitor usage dashboard |
| STT fallback path | Whisper per utterance + chat completion — usually cheaper for short IVR-style calls |

**Recommendation:** keep `stt_fallback` as default for high-volume restaurants; use `realtime` for premium pilot until you have **measured cost per completed call** (not just per minute).

Session cap: `VOICE_REALTIME_MAX_SESSION_SEC` limits worst-case bill on stuck calls.

---

## 7. Sign-off checklist

- [ ] Inbound webhook returns `<Stream>` TwiML (not `<Hangup/>`)
- [ ] Realtime audio duplex works both directions
- [ ] `lookup_menu` returns real org menu prices
- [ ] `escalate_to_whatsapp` delivers WA message (or logs dev-mode send)
- [ ] Fallback on Realtime failure (Say + Hangup)
- [ ] `voice_call_logs` row with correct `org_id`, `phone`, `mode`
- [ ] Latency table filled (3 calls)
- [ ] Cost estimate documented for owner (KZT/min or KZT/call)
- [ ] Only **admin** can change config in production UI

---

## 8. Rollback

1. `POST /voice/config` → `{ "enabled": false }` or `{ "mode": "stt_fallback" }`
2. Or disable voice webhook in Twilio console temporarily
3. No DB migration rollback required (flags in `meta_json`)
