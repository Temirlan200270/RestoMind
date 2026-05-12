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

## Границы и эволюция

**Сейчас (Phase 2):** observational + decision support — инсайты с причинами и рекомендациями.

**Следующий уровень (Phase 3):**
- Hourly baselines (нужен накопленный датасет)
- Causal graph (корреляции между метриками)
- Feedback calibration (автокалибровка порогов по `was_useful`)
- Scenario simulation с constraint-моделью кухни
