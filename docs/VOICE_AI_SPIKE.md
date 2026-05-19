# Voice AI Pilot

## Goal

Accept restaurant calls through Twilio Media Streams and reuse the existing RestoMind AI pipeline: context read, decision engine, snapshot, audit, and customer response.

## Architecture

1. Incoming call: `POST /api/whatsapp/voice/incoming` returns TwiML with `<Connect><Stream>`.
2. Audio stream: `WS /api/whatsapp/voice/stream` receives Twilio mu-law 8 kHz chunks.
3. MVP processing: mu-law -> WAV -> existing `transcribe_voice` -> existing `process_message`.
4. Future Realtime mode: `Organization.meta_json.voice_ai_mode = realtime` enables the OpenAI Realtime connector path once provider credentials and staging call tests are complete.

## Guardrails

- Per-org feature flag: `Organization.meta_json.voice_ai_enabled`, default `false`.
- Admin status endpoint: `GET /api/admin/intelligence/voice/status`.
- Admin config endpoint: `POST /api/admin/intelligence/voice/config`.
- Operational audit: `voice_call_logs` records call lifecycle and transcripts.
- The LLM/STT path runs outside the request DB transaction.

## Implemented MVP

- Twilio incoming call endpoint.
- Twilio Media Streams websocket endpoint.
- Per-org Voice AI enable/disable.
- Voice readiness/status API.
- Voice call logging.

## Remaining

- Native OpenAI Realtime connector behind `voice_ai_mode = realtime`.
- Staging phone-number test with real Twilio media stream.
- Latency and cost report per minute/call.
