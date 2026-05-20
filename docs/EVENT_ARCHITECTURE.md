# Event Architecture

RestoMind now has two event layers:

1. `app/services/events.py` is realtime Pub/Sub for admin WebSocket updates and staff notifications.
2. `app/services/system_events.py` is the durable domain event store for analytics, AI operations, audit, billing, ML, and future alerts.

## Realtime admin WebSocket

Транспорт: `GET /api/admin/ws?token=...` (`app/api/admin/ws.py`) подписывается на Pub/Sub из `publish_event`.

Канонические обёртки payload (единые `trace_id` / `conversation_id`): `app/services/trace_context.py`.

| `event_type` | Типичный источник | Поля payload (ядро) |
|--------------|-------------------|---------------------|
| `new_message` | `publish_chat_event` — webhook, admin send | `phone`, `role`, `content`, `organization_id`, `id?`, `delivery_status?`, `meta?` |
| `state_changed` | `publish_state_event` — webhook (эскалация / human_mode), `chats.py` takeover/release | `phone`, `state`, `organization_id` |
| `human_needed` | `publish_human_event` — эскалация из WhatsApp | `phone`, `reason`, `user_message`, `organization_id`, `intent?` |
| `order_updated` | `publish_order_event` — заказы, iiko | `order_id`, `organization_id`, `phone?`, … |
| `os.audit` | `audit_consumer` после записи в `audit_log` | `org_id`, `actor`, `action`, `title`, `entity_type?`, `entity_id?` |
| `order.created` / `order.confirmed` / `order.cancelled` | fanout из `analytics_consumer` | refresh KPI / ленты (не путать с `order_updated`) |
| `payment.completed` / `payment.failed` / `payment.expired` | payment pipeline | |
| `booking.created` / `booking.confirmed` / `booking.cancelled` | booking pipeline | |

**Контракт FSM в UI:** при эскалации из WhatsApp обязательно шлются **оба** `human_needed` и `state_changed` (`human_mode`). Только `human_needed` недостаточно — шапка чата и `chatIsBotActive()` завязаны на `activeChatState` из `state_changed` или `GET /api/admin/chats/{phone}/state`.

Клиент: `app/static/js/admin-app.js` — `handleWsEvent`: чаты (`onNewMessage`, `onStateChanged`, `onHumanNeeded`), OS (`os.audit` → `auditLog` / `dashLiveFeed`), бизнес-события → debounced refresh дашборда и автопилота.

## Durable Stream

The durable stream is stored in `system_events`.

Core fields:

- `organization_id`: required tenant boundary.
- `event_type`: business event name, for example `order_created`, `order_confirmed`, `order_cancelled`, `payment_completed`.
- `source`: producer module, for example `intent_router` or `payment_webhook`.
- `entity_type` / `entity_id`: optional pointer to the source object.
- `idempotency_key`: optional duplicate guard.
- `payload_json`: compact event facts, not a full object dump.
- `created_at`: event time.

## Rule

Business logic stays in Python. AI receives summaries and explanations only after deterministic analytics has calculated the numbers.

## Emitting Events

Use:

```python
await emit_system_event(
    db,
    organization_id=org_id,
    event_type="order_created",
    source="intent_router",
    entity_type="order",
    entity_id=order.id,
    idempotency_key=f"order_created:{order.id}",
    payload={"order_id": order.id, "total_price": float(order.total_price or 0)},
)
```

The helper does not commit. It participates in the caller's transaction.

## Tenant Isolation

All event producers must set `organization_id` from the trusted server-side object/session, never from frontend input alone.
