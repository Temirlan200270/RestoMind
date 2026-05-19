# Voice AI Pilot — spike (Sprint C)

## Цель

Оценить Twilio Media Streams ↔ OpenAI Realtime для приёма заказов голосом с reuse `fetch_ai_read_context` / `save_ai_context_snapshot`.

## Архитектура (черновик)

1. **Вход:** Twilio `<Connect><Stream>` → WebSocket endpoint `app/api/voice.py`.
2. **STT/LLM:** OpenAI Realtime API (μ-law 8 kHz) или fallback: существующий `transcribe_voice` + `call_openai`.
3. **Контекст:** перед ответом — `fetch_ai_read_context(phone, org_id)`; после turn — `save_ai_context_snapshot` + `emit_event(ai.dialog.started)`.
4. **Исход:** `send_customer_text` / Twilio `<Say>` только для подтверждений; заказ — тот же `intent_router` pipeline.

## Guardrails

- Отдельный feature flag `org.meta_json.voice_ai_enabled` (default off).
- Rate limit по `phone` + org.
- LLM вне DB session (см. `test_bot.py` pattern).

## DoD spike

- [ ] Документированный sequence diagram
- [ ] Прототип WS handler + 1 тестовый звонок в staging
- [ ] Оценка latency и стоимости мин/звонок

## Статус

Spike — не в production. Реализация после merge Sprint A+B.
