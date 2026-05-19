# AI Operations / Restaurant Intelligence

Актуальное описание Intelligence layer и Digital Twin.

## Разграничение: Intelligence vs Analytics

| Слой | Назначение |
|---|---|
| **Intelligence** | Decision support: инсайты, объяснения, рекомендации, симуляции |
| **Analytics** | Reporting: метрики, агрегаты, графики (`/stats`, `/analytics`, `/ai-value`) |

Intelligence не дублирует Analytics — он отвечает на вопрос «что делать», а не «что было».

---

## API

```http
GET  /api/admin/intelligence/overview
POST /api/admin/intelligence/query
GET  /api/admin/intelligence/insights
PATCH /api/admin/intelligence/insights/{id}
GET  /api/admin/intelligence/digital-twin
POST /api/admin/intelligence/simulate
GET  /api/admin/intelligence/latency
GET  /api/admin/intelligence/operator-efficiency
GET  /api/admin/intelligence/recommendations
POST /api/admin/intelligence/recommendations/refresh
PATCH /api/admin/intelligence/recommendations/{id}
```

---

## OperationalInsight

### Поля

```python
id, organization_id
insight_type: str      # см. типы ниже
severity: str          # info | warning | critical
title: str
summary: str
status: str            # new | seen | resolved | dismissed
was_useful: bool|None  # оператор отметил полезным / бесполезным
notes: str|None        # заметка оператора при закрытии
payload_json: dict     # см. структуру ниже
created_at, resolved_at
```

### payload_json — полная структура

```json
{
  "baseline_type": "duration_match",
  "weekday_baseline": {
    "baseline_type": "same_weekday_7d",
    "comparison_label": "vs прошлый Monday",
    "current_revenue": 45000,
    "baseline_revenue": 52000,
    "pct_change": -13.5
  },
  "cause_hypotheses": ["high_cancellation_rate", "kitchen_overload"],
  "recommended_actions": [
    "Проверить стоп-лист: 7 позиций",
    "Снизить долю отмен — сейчас 18%, потери ~12000 ₸"
  ],
  "current": { "revenue": ..., "orders": ..., "cancel_rate_pct": ... },
  "previous": { ... },
  "changes": { "revenue_pct": ..., "orders_pct": ..., "cancel_rate_pp": ... },
  "top_items": [...],
  "lost_revenue_estimate": 12000
}
```

### Типы инсайтов

| insight_type | severity | Условие |
|---|---|---|
| `revenue_drop` | warning | Выручка ≤ −15% vs предыдущий период |
| `orders_drop` | warning | Кол-во заказов ≤ −15% |
| `cancellations_up` | critical | Доля отмен выросла ≥ +5 п.п. |
| `sales_stable` | info | Аномалий нет |
| `ai_token_spike` | warning | Токены сегодня > 3× rolling-7d avg |
| `ai_error_spike` | critical | error_count/call_count > 15% |
| `ai_latency_spike` | warning | p95 latency > 1.5× SLA |

### Temporal baseline model

Два уровня сравнения:

1. **Duration-match** (исходный): сегодня vs вчера или та же длительность назад.
2. **Same-weekday baseline** (новый): сегодня vs тот же день прошлой недели.
   - Хранится в `payload_json["weekday_baseline"]`.
   - Полезен для ресторанов с выраженной недельной сезонностью (пятница vs пятница).

### Causal attribution (v1, эвристика)

При генерации `revenue_drop` и `orders_drop` система проверяет коррелирующие сигналы:

| Гипотеза | Условие |
|---|---|
| `high_cancellation_rate` | cancel_rate > 15% |
| `kitchen_overload` | kitchen_load > 80% |
| `stoplist_growth` | stoplist_count > prev × 1.3 |
| `ai_escalation_spike` | escalation_rate > 20% |

Результат — `payload_json["cause_hypotheses"]` — список строк, отображаемый в UI.

### Feedback loop

```http
PATCH /api/admin/intelligence/insights/{id}
{
  "status": "resolved",
  "was_useful": true,
  "notes": "Стоп-лист обновили, выручка восстановилась"
}
```

Данные накапливаются для будущей калибровки порогов.

---

## Restaurant Intelligence MVP

```text
manager question
  → lightweight intent parser (parse_revenue_orders_intent)
  → Python analytics (revenue_orders_summary)
  → deterministic explanation (build_intelligence_answer)
  → saved IntelligenceConversation / IntelligenceMessage
```

**Правило:** числовые вычисления всегда на Python, LLM — только для объяснений (future).

---

## Auto Insights pipeline

```text
GET /insights
  → detect_ai_incidents() [lazy, при каждом запросе]
  → generate_revenue_order_insights() [уже в БД или генерация по запросу]
  → return list[OperationalInsight]
```

---

## Digital Twin

### `RestaurantStateSnapshot` — поля

```python
active_orders, draft_orders, confirmed_orders, cancelled_today
revenue_today, avg_check_today
queue_size, operator_load, kitchen_load  # 0–100%
stoplist_count
payload_json, created_at
```

### Симуляция (детерминированная, без ML)

```text
orders_per_hour + operators + avg_check + base_cancel_rate
  → load_percent
  → avg_wait_min
  → cancellation_risk_pct
  → lost_revenue_kzt
```

---

## AI Business Recommendations

`BusinessRecommendation` — детерминированные рекомендации без LLM:

| recommendation_type | Условие |
|---|---|
| `product_boost` | conversion ≥ 40%, offers ≥ 8 |
| `pricing_adj` | conversion < 15%, offers ≥ 16 |
| `geo_expansion` | orders_per_customer ≥ 1.8 в сегменте |
| `stoplist_impact` | позиция добавлялась в стоп ≥ 3 раз |

Статусы: `new` → `viewed` → `acted_on` / `dismissed`.
Поля `confidence_pct` и `expected_impact_kzt` для приоритизации.

---

## Latency SLA monitor

```http
GET /api/admin/intelligence/latency?hours=24
```

Агрегаты `PipelineLatencyLog`: p50/p95/max по стадиям (dedupe/context/llm/route/reply).
SLA пороги: `SLA_LLM_P95_MS` (default 4000), `SLA_TOTAL_P95_MS` (default 8000).

---

## Predictive Analytics (Phase 5 OS)

Все модели в [`app/services/owner_dashboard.py`](../app/services/owner_dashboard.py). Детерминированные — без ML.

### Demand Forecast — `build_demand_forecast(orders_by_date, *, today)`

Линейная экстраполяция объёма заказов до конца текущей недели (Пн–Вс UTC).

```
confirmed_so_far / days_elapsed → daily_avg → forecast = confirmed + avg × days_remaining
```

| Поле | Описание |
|------|----------|
| `forecast_orders` | Прогноз заказов до конца недели |
| `confirmed_so_far` | Заказов подтверждено с начала недели |
| `daily_avg_orders` | Среднедневной темп |
| `confidence` | `low` (<3 дн), `medium` (3-4 дн), `high` (≥5 дн) |

Источник: `DailyOrgStats.orders_confirmed`. Нет зависимости от `Order` таблицы.

---

### Cancellation Forecast — `build_cancellation_forecast(stats_rows, *, today)`

Риск-скоринг уровня отмен на основе истории DailyOrgStats.

```
cancel_rate = orders_cancelled / (confirmed + cancelled)
risk: low → medium → high (≥20% или >1.5× исторический avg)
```

Возвращает: `{risk_level, cancellation_rate_pct, historical_rate_pct, week_cancelled}`.

---

### Overload Risk — `build_overload_risk(stats_rows, *, today)`

Сравнение текущего дневного темпа заказов с 4-недельным историческим средним по дням недели.

```
ratio = current_pace / historical_avg
high ≥ 1.5, medium ≥ 1.2, low < 1.2
```

Возвращает: `{risk_level, current_pace, historical_avg, overload_ratio}`.

---

### Autopilot Pricing — `build_autopilot_pricing(stats_rows, *, today)`

Ценовой сигнал: сравнение текущей недели с предыдущей по revenue/orders.

| Тактика | Условие | `price_adj_pct` |
|---------|---------|-----------------|
| `demand_up` | revenue ×1.2+ и orders ×1.1+ | +7% |
| `demand_down` | revenue ×0.8- и orders ×0.85- | −10% |
| `upsell_needed` | orders +15%, avg_check −10% | 0% (upsell) |
| `avg_check_up` | revenue stable, orders −5% | 0% |
| `stable` | всё в норме | 0% |

Рекомендации создаются автоматически в UTC 04:00 через `generate_autopilot_pricing_recommendation()`.  
Применяются через `POST /api/admin/intelligence/apply-pricing/{rec_id}` (обновляет `MenuItem.price`).

---

## Self-Healing Actions (Phase 5 OS)

[`app/services/healing_actions.py`](../app/services/healing_actions.py). Вызывается из `ai_incidents_hourly_tick` каждый час.

### Детекторы

| Детектор | Порог | Действие |
|----------|-------|---------|
| **Escalation spike** | ≥5 эскалаций за сегодня | `OperationalInsight` severity=warning/critical |
| **Payment failed spike** | ≥3 failed-платежей за сегодня | `OperationalInsight` severity=critical |
| **Cancellation surge** | ≥25% отмен за 7 дней | `OperationalInsight` + auto-trigger `generate_recommendations` |
| **AI message drop** | −70% AI-ответов vs предыдущая неделя | `OperationalInsight` severity=critical |

Дедупликация: не создаёт повторный инсайт если аналогичный есть за последние N часов.

---

## Audit Trail (Phase 5 OS)

[`app/services/audit_consumer.py`](../app/services/audit_consumer.py) + `AuditLog` table.

Вызывается из `emit_event()` для **всех** событий (кроме высокочастотных технических).

```http
GET /api/admin/intelligence/audit-log?limit=50&action=order.confirmed&actor=ai
```

`get_audit_log()` объединяет два источника:
1. **`AuditLog`** — события через `emit_event` (primary)
2. **`SystemEvent`** — legacy/system события не через emit_event (secondary)

---

## OS Dashboard — `GET /os-dashboard`

Единый event-driven endpoint для «Автопилот» вкладки. Нет SQL к `Order`/`ChatLog`.

```json
{
  "source": "event_driven",
  "today": { "orders_confirmed": 12, "revenue_kzt": 34500, "escalations": 1 },
  "week_forecast": { "forecast_revenue": 245000, "confidence": "high" },
  "demand_forecast": { "forecast_orders": 87, "daily_avg_orders": 14.5 },
  "cancellation_risk": { "risk_level": "low", "cancellation_rate_pct": 5.2 },
  "overload_risk": { "risk_level": "medium", "current_pace": 18.5 },
  "autopilot_pricing": { "tactic": "demand_up", "price_adj_pct": 7 },
  "incidents": [...],
  "top_recommendations": [...]
}
```

---

## Границы и эволюция

**Сейчас (Phase 5):** observational + predictive + self-healing — система сама детектирует и эскалирует проблемы, даёт ценовые рекомендации, строит прогнозы.

**Следующий уровень (Phase 6 — Visibility):**
- Audit Log Feed в UI (лента решений ОС)
- Websocket push при новых AuditLog entries
- Digest Email/Telegram: утренняя сводка OS-действий
- Hourly baselines (нужен накопленный датасет)
- Causal graph (корреляции между метриками)
- Feedback calibration (автокалибровка порогов по `was_useful`)
