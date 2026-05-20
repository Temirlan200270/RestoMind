# G10 — Simplification Map (implemented)

**Variant A + structural simplify.** Один слой на смысл, без Kafka-lite.

## 5 инвариантов

1. **Idempotency:** `whatsapp_inbound_dedupe` (DB) + queue dedupe по `message_id`
2. **Chat serialization:** `chat:lock` (lease 15s) + FIFO `chat:queue` — один drain-loop на телефон
3. **Focus stability:** `shift:active_focus:{org}:{operator}` = `focus_id`, TTL 45s (heartbeat)
4. **Multi-operator isolation:** busy focus_ids из SCAN leases — не показываем занятые коллегам
5. **Healing mute:** `heal:mute:{org}:{insight_type}` 30 min — не чаще одного outreach на тип

## Chat (`chat_serializer.py`)

| Было | Стало |
|------|--------|
| lock + pipeline + epoch | **lock + queue** |
| worker_id в lock | значение `active` |
| отдельный heartbeat task | `expire` перед каждым сообщением в drain |

Поток: `enqueue` → `SET lock NX` → `LPOP` loop → `DEL lock`.

## Shift (`shift_state_engine.py`)

| Было | Стало |
|------|--------|
| `focus_lock` + `focus_claim` + `owner_token` | **`shift:active_focus:{org}:{op}`** |
| shadow, grace keys | убраны |

Heartbeat: продлевает lease если `GET key == focus_id`.

## Healing

| Было | Стало |
|------|--------|
| realtime + cron spikes + `heal:fp` | **realtime** (payment, escalation) + **cron cold** (7d cancel, ai drop) + **`heal:mute`** |

Cron **не** детектирует payment/escalation spikes (только realtime).

## Freeze

Новые Redis-примитивы без инцидента в проде — **запрещены**. Только bugfix / logs / UI.

Полный продуктовый контракт: [`G10_SEMANTIC_CONTRACT.md`](G10_SEMANTIC_CONTRACT.md) (§1–§12).
