# Event Architecture

RestoMind now has two event layers:

1. `app/services/events.py` is realtime Pub/Sub for admin WebSocket updates and staff notifications.
2. `app/services/system_events.py` is the durable domain event store for analytics, AI operations, audit, billing, ML, and future alerts.

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
