# Focus Card Spec v1 (Shell v2 / G10.5)

Единый контракт UI ↔ API для **следующего действия оператора**. Backend не дублируется — projection берётся из `GET /api/admin/shift/state`.

См. также: [`G10_SEMANTIC_CONTRACT.md`](G10_SEMANTIC_CONTRACT.md), [`OS_TRANSITION_PLAN.md`](OS_TRANSITION_PLAN.md) § Focus Card, [`UI_DESIGN_SYSTEM.md`](UI_DESIGN_SYSTEM.md) § Execution Kernel UI.

---

## Источник истины

| Слой | Контракт |
|------|----------|
| API | `shiftState.focus` + `shiftState.presentation` + `shiftState.actions` |
| JS mapper | `adminFocusCardFromShiftState(shiftState)` в [`admin-app.js`](../app/static/js/admin-app.js) |
| UI | макрос [`_focus_card.html`](../app/templates/components/_focus_card.html) |

**Запрещено:** пересчитывать S0–S5, priority score или сортировку очереди на фронте (LAW 1).

---

## API: поле `focus`

Projection из [`shift_state_engine.py`](../app/services/shift_state_engine.py):

| Поле | Тип | UI |
|------|-----|-----|
| `id`, `kind` | string | идентификатор, бейдж типа |
| `title`, `subtitle` | string | заголовок карточки |
| `value_kzt` | number | сумма риска (в UI → `risk_kzt`) |
| `wait_minutes` | int | «N мин ожидания» |
| `pulse` | string | `red` / `amber` / … → semantics |
| `phone`, `order_id` | string / int | Context Dock |
| `actions` | array | primary CTA (≤3) |
| `reason` | string | только debug/logs |

Top-level `shiftState.actions` — вторичные кнопки (`skip`, `next`, `reset_skips`, …).

---

## UI View Model (`focusCardView`)

```javascript
{
  id, kind, title, subtitle,
  risk_kzt, wait_minutes,
  semantics,        // ds-status-danger | warn | ok | inactive
  actions,          // focus.actions
  state_actions,    // shiftState.actions
  context_route,    // { type: 'chat'|'order', id }
  ownership,        // presentation.focus_ownership
  kind_label        // человекочитаемый тип (Rule 8)
}
```

Mapper: **`focusCardFromShiftState()`** — единственное место маппинга focus → view model.

---

## Semantics map

| Условие | Класс |
|---------|--------|
| `pulse === 'red'` или slow_chat ≥5 мин | `ds-status-danger` |
| `pulse === 'amber'` или `value_kzt > 0` | `ds-status-warn` |
| иначе | `ds-status-ok` |

Inbox money-queue cards используют свой `severity`; при hero overlap с focus — тот же mapper.

---

## Action taxonomy

| Источник | subtype / type | UI |
|----------|----------------|-----|
| `focus.actions[]` | navigate, api | Primary CTA (первая кнопка) |
| `shiftState.actions[]` | skip, next, complete, reset_skips | Secondary row |
| Inbox item | через `runMoneyQueueAction` | Operator → **via shift** (`openMoneyQueueItemViaShift`) |

`reset_skips` — только при `metrics.shift_empty_focus_while_risk_positive` (FM-3 hybrid TTL + CTA).

---

## Context route & staged nav

| `kind` / pulse | Dock template | Mobile |
|----------------|---------------|--------|
| `slow_chat`, pulse red/amber | `_shift_focus_chat.html` | Staged: focus → context, «Назад к задаче» |
| `abandoned_draft`, `pending_prepay` | `_shift_focus_order.html` | то же |

Breakpoint staged nav: `<lg` (1024px). LAW 2 — sequential cognition, не vertical stack.

---

## Role-first placement

| Роль | Primary surface | Inbox |
|------|-----------------|-------|
| **operator** | вкладка **Смена** + Focus Card | «Все риски» — расширенный список, deep-link → shift |
| manager / admin | dashboard + operations tabs | полный inbox без shift-first routing |

---

## Файлы

- [`app/templates/components/_focus_card.html`](../app/templates/components/_focus_card.html)
- [`app/templates/screens/_tab_shift_control.html`](../app/templates/screens/_tab_shift_control.html)
- [`app/templates/screens/_tab_inbox.html`](../app/templates/screens/_tab_inbox.html) — hero + secondary copy
