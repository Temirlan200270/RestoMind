# G10.2 — Failure Simulation Playbook

> Сценарии «как система ломается в проде» и ожидаемое поведение **после G10 Simplification Map**.
> Контракт: [`G10_SEMANTIC_CONTRACT.md`](G10_SEMANTIC_CONTRACT.md) · Карта: [`G10_SIMPLIFICATION.md`](G10_SIMPLIFICATION.md) · Код: [`shift_state_engine.py`](../app/services/shift_state_engine.py), [`chat_serializer.py`](../app/services/chat_serializer.py)

Автотесты: [`tests/test_shift_failure_simulation.py`](../tests/test_shift_failure_simulation.py)

```bash
pytest tests/test_shift_failure_simulation.py tests/test_shift_state_engine.py tests/test_chat_serializer.py tests/test_healing_realtime.py -q
```

---

## Сценарии

### FS-1 — Focus jitter (FM-1)

| Шаг | Действие | Ожидание |
|-----|----------|----------|
| 1 | GET `/shift/state` ×3 за 10s без actions | `focus.id` стабилен, `focus.reason=active_focus_lease` |
| 2 | POST `next` | focus меняется, lease перезаписан или сброшен |

**Fail если:** focus прыгает без action.

Redis: `shift:active_focus:{org}:{operator}`.

---

### FS-2 — Dual operator complete

| Шаг | Действие | Ожидание |
|-----|----------|----------|
| 1 | Op A: `complete` focus X | 1× `shift.focus_completed` |
| 2 | Op B: `complete` focus X | idempotent, 0 новых events |

**Fail если:** дубли event.

---

### FS-3 — Skip spam

| Шаг | Действие | Ожидание |
|-----|----------|----------|
| 1 | 5× `skip` подряд | `state` не падает в S0 «случайно» |
| 2 | `presentation.projection_gap` может быть true | UI не показывает «всё ок» |

---

### FS-4 — S1 false spike / hysteresis

| Шаг | Действие | Ожидание |
|-----|----------|----------|
| 1 | risk > 10k → S1 | latch set |
| 2 | risk 8k (без red) | остаётся S1 (`s1_hysteresis_latched`) |
| 3 | risk < 7k, no red, drafts calm | S3/S0, latch cleared |

**Пороги:** enter risk > 10k; exit risk < 7k + no red + drafts < 6k.

---

### FS-5 — Projection gap (S1 + empty focus)

| Шаг | Действие | Ожидание |
|-----|----------|----------|
| 1 | Red chats в G5–G8, все items skipped | `state=S1`, `focus=null` |
| 2 | UI | amber «Расхождение», не зелёный calm |

---

### FS-6 — API degraded

| Шаг | Действие | Ожидание |
|-----|----------|----------|
| 1 | `/shift/state` 500 / network error | stale `shiftState` + `shiftStateDegraded` banner |
| 2 | retry success | banner скрыт |

---

### FS-7 — High traffic org (manual)

| Параметр | Значение |
|----------|----------|
| queue items | 26+ |
| state | S5 |
| focus | один на оператора, lease TTL 45s |
| Redis | SET prune, bounded scan leases |

**Нагрузочный чек:** p95 GET `/shift/state` < 500ms при queue ≤ 30.

---

## Матрица «сломалось → смотри»

| Симптом | Лог / поле | Вероятная причина |
|---------|------------|-------------------|
| «Почему S1, но пусто?» | `projection_gap`, `state_reason` | Все items filtered |
| Focus прыгает | `focus_reason` ≠ `active_focus_lease` без action | lease TTL / нет heartbeat |
| Два complete | `duplicate=true` в логе | OK (idempotent) |
| Вечный S1 | `s1_latched=true` | hysteresis, risk не ниже 7k |
| Ghost skip | `excluded_skip` растёт | SET prune должен чистить |
| Два оператора — один focus | один draft в org | ожидаемо: второй `focus=null` до второго item |

---

## Ручной smoke (15 мин)

1. Логин оператор → вкладка «Смена».
2. «Другое дело» → focus сменился, state тот же.
3. «Не сейчас» → projection_gap при S4.
4. «Готово» → focus исчез / следующий.
5. DevTools → throttle Offline → баннер degraded → Online → refresh.

---

### FS-8 — WhatsApp double-text race (lock + queue)

| Шаг | Действие | Ожидание |
|-----|----------|----------|
| 1 | 3 webhook на один phone < 1s | 1× `lock_acquired`, остальные `queued` (или lock уже занят → только enqueue) |
| 2 | Логи | `process_message` **последовательно** в drain-loop владельца lock |

Автотест: [`tests/test_chat_serializer.py`](../tests/test_chat_serializer.py).

Нет `pipeline_claimed` / `pipeline_reentry_rejected` — только `chat:lock` + `chat:queue`.

---

### FS-8b — Lock TTL expiry mid-drain

| Шаг | Действие | Ожидание |
|-----|----------|----------|
| 1 | Worker A держит lock, TTL истёк до конца drain | другой worker может `SET lock NX` и продолжить очередь |
| 2 | Сообщение не потеряно | остаётся в queue или re-queue при failed renew |

---

### FS-9 — Stale focus lease / multi-tab UX

| Шаг | Действие | Ожидание |
|-----|----------|----------|
| 1 | Оператор A: focus + heartbeat каждые ~7s | `ownership=mine`, lease TTL 45s |
| 2 | Закрыть вкладку / stop heartbeat | через ≤45s lease истекает, B может взять задачу |
| 3 | Вкладка A без renew, B владеет focus | heartbeat A → `renewed=false` → UI `loadShiftState(true)` |

`owner_token` в body опционален (legacy); engine сравнивает только `focus_id` в lease.

---

### FS-10 — Healing realtime mute

| Шаг | Действие | Ожидание |
|-----|----------|----------|
| 1 | ≥3 `payment.failed` за час | 1× `OperationalInsight` + `heal:mute:{org}:payment_failed_spike` 30m |
| 2 | Ещё failures в том же 30m | без нового insight (mute) |
| 3 | Cron tick в том же окне | **не** создаёт второй spike-insight (cron cold only) |

Автотест: [`tests/test_healing_realtime.py`](../tests/test_healing_realtime.py).

---

## Следующие симуляции (v2)

- 10 операторов / 1 org concurrent GET+POST
- Redis down → API 503 + degraded UI only
- Location switch mid-shift → state rebuild scoped
