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

- `conversation_state_changed`

Payload fields:

- `phone`
- `from_state`
- `to_state`
- `reason`
- `context`
