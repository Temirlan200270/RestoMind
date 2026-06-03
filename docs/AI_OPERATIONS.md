# AI Operations / Restaurant Intelligence

Актуальное описание Intelligence layer и Digital Twin.

## Разграничение: Intelligence vs Analytics

| Слой | Назначение |
|---|---|
| **Intelligence** | Decision support: инсайты, объяснения, рекомендации, симуляции |
| **Analytics** | Reporting: метрики, агрегаты, графики (`/stats`, `/analytics`, `/ai-value`) |

Intelligence не дублирует Analytics — он отвечает на вопрос «что делать», а не «что было».

---

## Owner Intelligence (продуктовый слой для владельца)

Отдельный API-пакет `/api/admin/owner-intelligence/*` — KPI, QA audit, Revenue Copilot impact, Menu Profit, Network Benchmark, Kitchen Gate, weekly digest.

| Область | Сервисы | UI |
|---|---|---|
| Summary KPI | `owner_intelligence.py` | AI Center → **Owner Intelligence** |
| QA Auto-Audit | `order_ai_audit.py` | OI + badge на **Смена** / чат (`_order_qa_audit_badge.html`) |
| Revenue Copilot | `upsell_scoring_engine.py`, `upsell_pair_mining.py`, `upsell_attribution.py` | **Настройки → Умные продажи** |
| Menu Profit | `menu_profit_lab.py`, CSV import | OI preview + вкладка **Меню** |
| Network Benchmark | `network_benchmark.py`, `network_weekly_report.py` | AI Center → **Network Benchmark** (только сети) |
| Kitchen Gate v2 | `operational_mode.py` | **Смена** + OI (`_kitchen_gate_panel.html`) |
| Weekly digest | `owner_digest_delivery.py`, `owner_weekly_digest.py` | OI preview + cron Mon 10:00 org TZ |

Deploy smoke: [`docs/DEPLOY_RUNBOOK.md`](DEPLOY_RUNBOOK.md) §8, `scripts/verify_owner_intel_schema.py`.  
Alembic head: `20260603_menu_item_lifecycle`.

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
GET  /api/admin/system/task-queue-health
GET  /api/admin/system/faq-cache-metrics?days=7
```

**System (не Intelligence, но ops для WhatsApp hot path):**

| Endpoint | Scope | UI |
|---|---|---|
| `GET /api/admin/system/task-queue-health` | Процесс (Redis / ARQ / worker) | **Настройки → Подключения** — бейджи «Хранилище / Очередь / Воркер» (`refreshTaskQueueHealth`, `taskQueueStatusClass`) |
| `GET /api/admin/system/faq-cache-metrics` | **Только текущий `organization_id` сессии** | Пока **нет** отдельной панели — prod smoke через curl / скрипт |

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
Применяются через:
- `POST /api/admin/intelligence/apply-pricing/{rec_id}` — одна рекомендация;
- `POST /api/admin/intelligence/apply-pricing/bulk` — все `autopilot_pricing` со статусом `new` за org (один aggregate `system.pricing_adjusted`).

---

## Self-Healing Actions (Phase 5 OS)

Два канала (см. [`G10_SEMANTIC_CONTRACT.md`](G10_SEMANTIC_CONTRACT.md) §12, [`G10_SIMPLIFICATION.md`](G10_SIMPLIFICATION.md)):

### Realtime (event bus)

[`app/services/healing_realtime.py`](../app/services/healing_realtime.py) — подписчик на `BusinessEvent` при emit.

| Событие | Порог (за текущий час) | Insight type | Дедуп |
|---------|------------------------|--------------|-------|
| `payment.failed` | ≥3 | `payment_failed_spike` | `heal:mute:{org}:{type}` 30 min |
| `ai.escalated` | ≥5 | `escalation_spike` | то же |

Счётчик: `heal:counter:{org}:{event_type}:{YYYYMMDDHH}`. Cron **не** дублирует эти spikes.

### Cron (hourly cold)

[`app/services/healing_actions.py`](../app/services/healing_actions.py) — `ai_incidents_hourly_tick` в worker.

| Детектор | Порог | Действие |
|----------|-------|---------|
| **Cancellation surge** | ≥25% отмен за 7 дней | `OperationalInsight` + auto-trigger `generate_recommendations` |
| **AI message drop** | −70% AI-ответов vs предыдущая неделя | `OperationalInsight` severity=critical |
| **Payment failed → WA nudge** | ≥3 failed за сегодня | Шаблонное WhatsApp гостям с `prepayment_status=pending` (≤5/час/org), `system.healing_wa_sent` |

Дедупликация: `heal:mute` 30 min на тип + `_create_insight_if_new` (нет аналога за N часов в БД).

---

## Audit Trail (Phase 5 OS)

[`app/services/audit_consumer.py`](../app/services/audit_consumer.py) + таблица `audit_log`.

- Вызывается из `emit_event()` для бизнес-событий (кроме `ai.response.generated`, `conversation.state_changed`).
- После записи публикует WebSocket **`os.audit`** (`org_id`, `actor`, `action`, `title`) — лента в админке без polling.
- Интеграционные события: `integration.iiko.failed`, `integration.whatsapp.failed` (см. [`integration_events.py`](../app/services/integration_events.py), [`chat_delivery.py`](../app/services/chat_delivery.py)).
- Диалоги: `ai.dialog.started` — [`dialog_events.py`](../app/services/dialog_events.py) (один раз на org+phone+день).

**Daily OS Digest:** `GET /api/admin/intelligence/daily-os-digest/preview`; отправка — `daily_os_digest_scheduled_tick` в ARQ (окно 09:00 по timezone org).

```http
GET /api/admin/intelligence/audit-log?limit=50&action=order.confirmed&actor=ai
```

`get_audit_log()` объединяет два источника:
1. **`AuditLog`** — события через `emit_event` (primary)
2. **`SystemEvent`** — legacy/system события не через emit_event (secondary)

---

## OS Dashboard — `GET /os-dashboard`

Единый endpoint для «Автопилот» вкладки. По умолчанию читает event-driven `DailyOrgStats`. Если передан `location_id` или staff ограничен `assigned_location_ids`, endpoint не использует org-wide `DailyOrgStats` как точный источник и возвращает `source=sql_location` для метрик по точке.

```json
{
  "source": "event_driven",
  "location_scope": { "location_id": null, "source": "event_driven" },
  "today": { "orders_confirmed": 12, "revenue_kzt": 34500, "escalations": 1 },
  "week_forecast": { "forecast_revenue": 245000, "confidence": "high" },
  "demand_forecast": { "forecast_orders": 87, "daily_avg_orders": 14.5 },
  "cancellation_risk": { "risk_level": "low", "cancellation_rate_pct": 5.2 },
  "overload_risk": { "risk_level": "medium", "current_pace": 18.5 },
  "autopilot_pricing": { "tactic": "demand_up", "price_adj_pct": 7 },
  "stock_alerts": [{ "ingredient": "...", "days_until_runout": 7, "message": "..." }],
  "incidents": [...],
  "top_recommendations": [...]
}
```

`stock_alerts`: сначала из `inventory_stock_snapshots` ([`build_stock_alerts_from_inventory`](../app/services/owner_dashboard.py)), иначе прокси [`build_stock_alerts_stub`](../app/services/owner_dashboard.py) по `DailyOrgStats`. При `location_id` inventory snapshots фильтруются по точке.

### Location Enterprise Metrics

Location scope поддержан в:

- Dashboard: `GET /api/admin/stats`, `/funnel`, `/analytics`, `/activity`, `/incidents`, `/roi/today`.
- Intelligence: `/overview`, `/digital-twin`, `/latency`, `/os-dashboard`, `/revenue-leak`, `/inventory/stock-alerts`.
- UI: селектор точки в шапке (`available_locations` из `/auth/me`) прокидывается в dashboard, AI Center, chats и orders.

Важный инвариант: `DailyOrgStats` пока агрегируется по `organization_id`. Поэтому location-scoped запросы возвращают `location_scope.source=sql_location` / `org_level_latency_logs`, а полноценный `daily_location_stats` остаётся отдельным hardening-этапом.

### AI Context Snapshot (Phase 3)

Канонический контракт в [`intelligence.py`](../app/api/admin/intelligence.py) (не путать с `POST /inventory/snapshots/bulk` — SupplyMind):

| Endpoint | Назначение |
|----------|------------|
| `GET /snapshots` | список `AIContextSnapshot` org (фильтр `phone`, `limit`) |
| `GET /snapshots/{id}` | полный снимок для аудита |
| `POST /snapshots/{id}/replay?user_text=...` | воспроизведение без отправки гостю |

Дубликат `GET /snapshots` в файле убран — остаётся одна регистрация маршрута вместе с `{id}` и `replay`.

**Replay:** frozen `menu_context_text` + **`chat_history_slice`** из `customer_state`; fallback — `menu_prices_snapshot` → synthetic menu context.

### GuestCare External

- `GET /api/admin/intelligence/reviews/external` — список + `sync_meta` (последний cron/ручной sync)
- `POST /api/admin/intelligence/reviews/external/sync` — fetch страницы 2GIS (`Organization.review_url_2gis`) и опционально Google (`meta_json.review_url_google`), upsert в `external_reviews`
- `POST /api/admin/intelligence/reviews/external/import` — ручной URL или полный payload (`author`, `rating`, `text`)
- `POST /api/admin/intelligence/reviews/external/{review_id}/reply-draft`

Хранение: таблица `external_reviews` (не `Organization.meta_json` для самих отзывов; метаданные sync — `meta_json.guestcare_sync`).

**Парсинг (ограничения):** без headless browser; 2GIS — JSON-LD + embedded `__INITIAL_STATE__` при наличии в HTML. Google Maps без **Places API** key обычно не отдаёт отзывы в статическом HTML (ToS/conservative).

**Продуктовое решение (2026-05):** GuestCare **100% = 2GIS auto-sync**; Google Places API **не в scope** — Google URL опционален (best-effort + ручной import по URL).

---

## Final Mile — backend MVP

?????? ?????? ? ??????????: [`docs/FINAL_MILE_IMPLEMENTED.md`](FINAL_MILE_IMPLEMENTED.md). **Ops/staging gate**: [`docs/FINAL_MILE_OPS_SIGNOFF.md`](FINAL_MILE_OPS_SIGNOFF.md).

### SupplyMind

| Endpoint | Назначение |
|----------|------------|
| `POST /inventory/snapshots/bulk` | upsert `inventory_stock_snapshots` |
| `GET /inventory/stock-alerts` | алерты по SKU (location-aware) |
| `POST /supplymind/drafts` | черновик закупки из low-stock |
| `GET /supplymind/drafts` | список черновиков |

Сервис: [`app/services/supplymind.py`](../app/services/supplymind.py).

### StaffMind

| Endpoint | Назначение |
|----------|------------|
| `POST /staffmind/onboarding` | старт сессии онбординга |
| `POST /staffmind/onboarding/{id}/message` | Q&A из `KnowledgeItem` |
| `GET /staffmind/onboarding` | список сессий |

Сервис: [`app/services/staffmind.py`](../app/services/staffmind.py). UI: [`_tab_settings_team.html`](../app/templates/screens/_tab_settings_team.html) + `loadStaffMindOnboarding` / `startStaffMindOnboarding` в `admin-app.js`. RBAC: `require_staff_manager_or_admin` на `POST …/onboarding` и `POST …/message`; `GET …/onboarding` — любой авторизованный staff org.

### Voice AI

| Endpoint | Назначение |
|----------|------------|
| `GET /voice/status` | readiness per org |
| `POST /voice/config` | `voice_ai_enabled`, `voice_ai_mode` в `Organization.meta_json` |

Сервис: [`app/services/voice_ai.py`](../app/services/voice_ai.py) + [`voice_realtime/`](../app/services/voice_realtime/). Twilio + Realtime bridge: [`docs/VOICE_AI_SPIKE.md`](VOICE_AI_SPIKE.md) (`stt_fallback` | `realtime` реализованы). **Хвост:** staging call на реальном Twilio Media Stream.

### Daily OS Digest

- Preview: `GET /daily-os-digest/preview`
- Cron: `daily_os_digest_scheduled_tick` в [`app/worker.py`](../app/worker.py) (09:00 по timezone org)
- Сервис: [`app/services/daily_os_digest.py`](../app/services/daily_os_digest.py)

---

## Границы и эволюция

**Сейчас (Phase 5 + Final Mile backend):** observational + predictive + self-healing; SupplyMind/StaffMind/Voice/Digest API готовы; Decision Feed и `os.audit` в UI работают.

**Phase 6 — Visibility (следующий слой):**
- [x] Audit Log Feed в UI (лента решений ОС, `loadAuditLog`, `dashLiveFeed`)
- [x] Websocket push `os.audit` при новых AuditLog entries
- [x] Daily OS Digest backend (Telegram preview endpoint + cron)
- [x] Admin UI для SupplyMind drafts, StaffMind onboarding, Voice toggle, digest preview
- [ ] Staging smoke (ops gate): Telegram digest delivery, Twilio voice (STT + Realtime latency/cost)
- [ ] Hourly baselines (нужен накопленный датасет)
- [ ] Causal graph (корреляции между метриками)
- [ ] Feedback calibration (автокалибровка порогов по `was_useful`)

---

## WhatsApp Performance Pack (hot path)

| Компонент | Файл | Env |
|---|---|---|
| Quick replies (bypass LLM) | `app/services/quick_replies.py` | `QUICK_REPLIES_ENABLED=true` (default) |
| FAQ cache | `app/services/faq_cache.py` | `FAQ_CACHE_ENABLED`, `FAQ_CACHE_TTL_SEC` |
| FAQ cache metrics (prod smoke) | `GET /api/admin/system/faq-cache-metrics?days=1..31` | Redis keys `rm:metrics:faq_cache:{hit\|miss\|save}:{org_id}:{date}` |
| Prompt budget | `app/services/prompt_metrics.py` | `PROMPT_MAX_TOKENS_SOFT`, `PROMPT_HISTORY_MIN_KEEP` |
| Queue wait (latency diag) | `app/services/wa_queue_metrics.py` | `queue_wait_ms` в `rm_stage_ms`; см. `scripts/diag_whatsapp_latency.py` |

**Мультитенантность FAQ (важно):** кеш **строго org-scoped**. У каждого заведения (`organization_id`):

- свой Redis-ключ ответа: `rm:faq_cache:{org_id}:{hash_вопроса}`;
- свои метрики hit/miss/save: `rm:metrics:faq_cache:*:{org_id}:{date}`;
- своя **база знаний** в промпте → `kb_fingerprint` (SHA256 KB-текста org). Если оператор меняет KB в **Настройки → Мой ресторан**, старые записи кеша не матчятся (`kb_fp` mismatch → miss → новый LLM-ответ).

Один и тот же текст вопроса у **разных** org может дать **разные** ответы — это ожидаемо. Между филиалами одной сети (разные `organization_id`) кеш **не** шарится.

**Redis keys (org-scoped):**
- `rm:faq_cache:{org_id}:{hash16}` — кеш FAQ-ответа (TTL 24h по умолчанию)
- `rm:metrics:faq_cache:{hit|miss|save}:{org_id}:{YYYY-MM-DD}`
- `rm:metrics:quick_reply:{org_id}:{template_id}:{YYYY-MM-DD}`

**FAQ cache:** сохраняется только при `intent=faq`, без draft/items/upsell, reply ≤600 символов, вопрос 5–100 символов после нормализации. Инвалидация по `kb_fingerprint` (hash KB-текста в промпте).

**Пример smoke (текущий филиал сессии):**

```bash
curl -sS -b cookies.txt "https://<host>/api/admin/system/faq-cache-metrics?days=7"
# → enabled, organization_id, today.{hit,miss,save,hit_rate_pct}, totals, daily[]
```

**Quick replies** (`quick_replies.py`) тоже учитывают org: шаблоны «меню» / «статус заказа» подставляют данные текущей организации (меню, активный заказ гостя).

**Kitchen-gate:** в `_handle_order` при `not is_kitchen_open` (и без `is_preorder`) заказ → `kind='night_preorder'`.
