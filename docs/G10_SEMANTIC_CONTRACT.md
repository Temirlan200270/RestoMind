# G10 Semantic Contract

> **Продуктовая семантика** смены (state / focus / actions / UI) и **§12 — freeze concurrency** после Simplification Map.
> Карта Redis-примитивов: [`G10_SIMPLIFICATION.md`](G10_SIMPLIFICATION.md) · Операции: [`G10_SHIFT_CONTROL_PLANE.md`](G10_SHIFT_CONTROL_PLANE.md).

---

## §1 — Два слоя смысла

| Слой | Источник | Роль |
|------|----------|------|
| **`state` (S0–S5)** | G5–G8 `all_items`, pure engine | **System truth** — реальность смены |
| **`focus` + `queue`** | Engine после Redis skip/done + `active_focus` lease | **Operational projection** — одно действие сейчас |

Расхождение допустимо: `state=S1` и `focus=null` → `presentation.projection_gap=true`, UI не показывает «всё спокойно».

---

## §2 — State machine

- S0–S5 считаются **только** из сигналов G5–G8 (`resolve_state`).
- Redis **не** меняет `state`.
- S1 hysteresis: enter `risk_kzt > 10_000`, exit `risk_kzt < 7_000` + no red + drafts calm (`shift:s1_latch`).

---

## §3 — Focus

- Один `focus` на оператора; preview `queue` ≤ 5 в API.
- Выбор: `priority_score` по G5–G8, minus skip/done, minus focus_id занятых коллегами (`shift:active_focus:*`).
- Стабильность: если lease оператора жив и задача ещё actionable → тот же `focus_id`, `reason=active_focus_lease`.
- Heartbeat: `POST /api/admin/shift/heartbeat` продлевает `shift:active_focus:{org}:{operator}` (TTL **45s**). `DELETE` — release при уходе с вкладки.

---

## §4 — Actions

| Subtype | Семантика | Побочные эффекты |
|---------|-----------|------------------|
| `next` | Смена указателя (rotation) | `shift:next:*` + SET index |
| `skip` | Явный отказ | `shift:skip:*` TTL 600s |
| `complete` | Задача закрыта | SETNX `shift:done:*` + `shift.focus_completed` once |

`complete` **не** меняет order/chat в БД — только operational closure + event.

---

## §5 — UI renderer

- Alpine **не** пересчитывает `state`, не сортирует queue, не выбирает focus.
- Баннеры: `presentation.projection_gap`, `presentation.empty_focus_reason`, `shiftStateDegraded`.
- `metrics.active_risk_kzt` — UX-only; state из полной очереди G5–G8.

---

## §6 — Logging

Structured: `shift_state_built`, `shift_action_applied` с `focus_id`, `focus_reason`, `ownership`, `queue_size`, `state`, `s1_latched`.

---

## §7 — Presentation

- `state_reason` — почему этот S*.
- `projection_gap` — S1/S2/S4 при пустом focus, но риск в очереди.
- `empty_focus_reason` — `calm_no_action` | `all_reviewed` | `filtered_out` и т.д.
- UI calm empty только если `state ∈ {S0,S3}` и `empty_focus_reason=calm_no_action`.

---

## §8 — Multi-operator

- Lease `shift:active_focus:{org}:{operator}` → `focus_id`; занятые id исключаются при выборе у других.
- `focus.ownership`: `mine` | `other` | `unowned` (для кнопок).
- Два оператора на **разных** draft — разные focus (см. FS в [`G10_FAILURE_SIMULATION.md`](G10_FAILURE_SIMULATION.md)).

---

## §9 — Guarantees

1. GET `/shift/state` read-only (кроме scan/prune exclusions).
2. Идемпотентный `complete` org-wide.
3. Skip не понижает state «в ноль» без сигналов G5–G8.
4. Order row conflicts — optimistic lock, без auto-merge.

---

## §10 — Ownership (post-simplify)

**Один lease на оператора** — без `focus_lock` + `focus_claim` + `owner_token` в engine.

- Ключ: `shift:active_focus:{org}:{operator_id}` = `focus_id`
- Renew: heartbeat если stored id совпадает с текущим focus
- Release: skip/complete/next, DELETE heartbeat, TTL expiry
- При `renewed=false` → `loadShiftState(true)` (polling/heartbeat fallback)

---

## §11 — Chat pipeline

WhatsApp webhook → [`chat_serializer.py`](../app/services/chat_serializer.py):

1. DB dedupe (`whatsapp_inbound_dedupe`) + queue dedupe по `message_id`
2. FIFO `chat:queue:{org}:{phone}` (max 20)
3. Lease `chat:lock` 15s, drain loop, `expire` перед каждым сообщением

Нет: `pipeline_id`, `epoch`, `shadow_owner`, `chat:active_pipeline`.

---

## §12 — Concurrency (minimal, frozen)

| Слой | Инварианты |
|------|------------|
| **CHAT** | DB dedupe + `chat:lock` + `chat:queue` FIFO |
| **SHIFT** | `shift:active_focus:{org}:{operator}` TTL 45s |
| **HEALING** | realtime counters + `heal:mute` 30m; cron = cold analytics only |

### HEALING split

| Канал | Что детектирует |
|-------|-----------------|
| **Realtime** (`healing_realtime.py`) | `payment.failed`, `ai.escalated` — порог за час + `heal:mute` |
| **Cron** (`healing_actions.py`) | `cancellation_surge` (7d), `ai_message_drop` (week), WA nudge при pending prepay |

Cron **не** дублирует payment/escalation spikes (убрано в Simplification Map).

**Freeze:** новые Redis consistency-примитивы без prod-инцидента — запрещены. См. [`G10_SIMPLIFICATION.md`](G10_SIMPLIFICATION.md).
