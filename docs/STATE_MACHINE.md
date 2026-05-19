# Conversation State Machine

Current persisted states:

- `chatting`
- `awaiting_order_payment`
- `confirming_order`
- `confirming_booking`
- `human_mode`

Allowed transitions:

- `chatting` -> `chatting`, `awaiting_order_payment`, `confirming_order`, `confirming_booking`, `human_mode`
- `awaiting_order_payment` -> `awaiting_order_payment`, `chatting`, `confirming_order`, `human_mode`
- `confirming_order` -> `confirming_order`, `chatting`, `awaiting_order_payment`, `human_mode`
- `confirming_booking` -> `confirming_booking`, `chatting`, `human_mode`
- `human_mode` -> `human_mode`, `chatting`

Rules:

- `human_mode` is an operator-owned state. It must not jump straight into confirmation or payment states.
- retries and duplicate webhooks may repeat the same state; self-transitions are valid.
- Redis is cache/acceleration only. Durable state lives in `users.current_state`.

Durable event emitted on change:

- `conversation.state_changed` (аудит/аналитика в БД)

Payload fields:

- `phone`
- `from_state`
- `to_state`
- `reason`
- `context`

## Redis vs PostgreSQL

- **Источник истины:** `users.current_state` (обновляется через `update_user_session_fields_in_db` из `webhooks.py`, `intent_router`, admin takeover/release).
- **Redis:** кэш FSM и pending-полей для быстрого чтения в hot path. При входящем WhatsApp, если в Redis `chatting`, а в БД уже `human_mode`, webhook подтягивает БД (`db_human_mode`) и не вызывает LLM.
- **`clear_pending_order`** сбрасывает черновик и по умолчанию возвращает `CHATTING`, но **не** сбрасывает `HUMAN_MODE` — эскалация не откатывается при очистке корзины.

## WhatsApp: пути без LLM и с эскалацией

| Путь | Условие | Поведение |
|------|---------|-----------|
| `operator_only` | `human_mode` / `db_human_mode` / `ai_paused` / `ai_snooze` | LLM не вызывается; клиенту — шаблон «менеджер ответит»; в `chat_logs.meta` — `operator_only: true` |
| Эскалация | `route_intent` → `new_state == human_mode` | Ответ бота (в т.ч. fallback «технические сложности»), запись `EscalationEvent`, Telegram-алерт при настройке |

После эскалации или при `operator_only` в `human_mode` webhook публикует в админку **`state_changed`** с `state: human_mode` (см. `publish_state_event` в `app/services/trace_context.py`). Раньше шёл только `human_needed` — из‑за этого в UI оставался бейдж «ИИ отвечает».

## Синхронизация с админкой (WebSocket)

| Событие | Когда | Эффект в UI (`admin-app.js`) |
|---------|--------|------------------------------|
| `state_changed` | Эскалация из WhatsApp, `operator_only` в human_mode, takeover/release в `chats.py` | `onStateChanged` → `activeChatState`, список чатов |
| `human_needed` | Эскалация (после `publish_human_event`) | Алерт + звук; `onHumanNeeded` дублирует `human_mode` для активного чата |
| `new_message` | Новая строка в `chat_logs` | `onNewMessage`; в payload может быть `meta` (см. ниже) |

Шапка чата (`_tab_chats.html`): «ИИ отвечает» при `activeChatState !== 'human_mode'`. Поле ввода оператора блокируется, пока `chatIsBotActive()` (бот «ведёт» диалог).

## Meta исходящих в `chat_logs`

| Ключ | Значение |
|------|----------|
| `operator_only` | Сообщение в режиме «ИИ молчит»; в ленте показывается «ИИ не отвечает (ожидает оператора)», не сырой `[OPERATOR_ONLY …]` |
| `technical_fallback` | Текст совпал с запасным ответом при сбое LLM (`is_openai_fallback_escalation_reply` в `ai_brain.py`); бейдж «Сбой ИИ» |
| `intent`, `monologue` | Отладка для оператора (интент, пояснение) |
