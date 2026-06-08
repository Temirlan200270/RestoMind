# Intelligence OS — Restory-class слой данных и AI-Аналитик

Цель: довести RestoMind до продукта, за который ресторан платит не как за «чат-бота», а как за слой операционного интеллекта: правильные ответы, объяснение причин, измеримый ROI и историю конкретного ресторана.

Продуктовая опора: [`docs/CUSTOMER.md`](CUSTOMER.md). Любой Copilot tool, инсайт и дашборд должен отвечать на реальные вопросы владельца, управляющего, сети или франшизы.

## Приоритетная дорожная карта

```text
P0 Customer Model
D0 Data Quality Layer
D1 OLAP Layer
D2 Food Cost
D3 Dashboard
A1 Anomaly Engine
C1 Copilot
C1.5 Explainability + Confidence
M1 Organization Memory
C2 Restaurant Knowledge Graph
O1 Forecasting
F1 ROI Loop
X1 Autonomy (future)
```

`X1 Autonomy` не является ближайшим selling point. Его нельзя ставить выше качества данных, объяснимости и памяти организации. На ближайших версиях автономность допускается только как внутренний черновик с human approval.

## Текущий статус реализации

На 2026-06-03 реализован полный trust/intelligence layer для текущих источников данных:

- D0: OLAP sync идет по цепочке `snapshot -> quality report -> canonical -> facts -> aggregates`; факты несут `snapshot_id` и canonical lineage, есть checksum/schema hash, quarantine sample и reconciliation report.
- P0/C1.5: Copilot получает роль пользователя и показывает role-based business questions; инсайты и Copilot возвращают confidence, evidence и baseline drilldown по revenue/orders/guests/avg check/category/dish/hour.
- P1/M1: proactive delivery имеет history/actions/settings API и AI Center inbox; memory events создаются вручную и автоматически при menu import, price/cost changes, supplier graph link, marketing campaign, resolved anomaly и measured ROI.
- C2: relational graph profiles пересобираются из menu/cost/inventory/sales facts; Menu Profit Lab читает `DishMarginProfile`, supplier exposure и seasonality доступны как Copilot tools.
- O1/F1: forecasting v2 учитывает seasonality, dirty-data weighting, memory context и confidence; ROI loop хранит baseline/measurement windows, public chain API и owner digest с realized money.
- D1.2/X1: naive iiko timestamps нормализуются через timezone организации; автономность оставлена experimental/internal без внешних iiko/закупочных мутаций без подтверждения.
- D3.1: AI Center показывает role-based quick questions, confidence/evidence/drilldown на карточках инсайтов, proactive inbox/actions/settings, ROI outcome chain и data quality status/confidence в табе продаж; отдельный lineage audit screen остается опциональным product polish.

Trust UI закрыт для текущего слоя: AI Center показывает role-based quick questions, confidence/evidence/drilldown, inbox/actions/settings, ROI outcome chain и data quality status/confidence в табе продаж. Отдельный lineage audit screen можно развивать дальше как product polish без изменения backend contracts.

## P0 — Customer Model

Система должна начинаться с вопроса «кто достанет кошелёк и за что?», а не с вопроса «какие данные мы можем показать». Основные персоны и ежедневные вопросы описаны в [`docs/CUSTOMER.md`](CUSTOMER.md).

Главное правило Copilot: отвечать не на все возможные аналитические запросы, а на повторяющиеся управленческие вопросы:

- почему упала прибыль;
- какой филиал хуже работает;
- сколько денег потеряли на списаниях;
- кто из сотрудников проседает;
- какие блюда продаются плохо;
- что заказать на завтра.

## D0 — Data Quality Layer

Это часть фундамента, а не улучшение. До `sales_fact` должен быть слой качества:

```text
source_data
  ↓
validation
  ↓
normalization
  ↓
canonical schema
  ↓
fact tables
  ↓
AI
```

Почему критично: рестораны меняют категории, удаляют блюда, переименовывают позиции, задним числом правят документы. Без canonical schema инсайты станут нестабильными.

Минимальная схема D0:

- `source_data_snapshots` — сырой payload источника, checksum, source, org, date range.
- `canonical_products` — стабильная сущность блюда/товара независимо от переименований.
- `canonical_sales_orders` / `canonical_sales_items` — нормализованные строки до fact tables.
- `data_quality_reports` — результат проверки источника.

`data_quality_reports` должен хранить:

- `missing_fields`;
- `duplicates`;
- `invalid_values`;
- `sync_failures`;
- `normalization_warnings`;
- `confidence_score`;
- `blocking`/`non_blocking`;
- `sample_rows`;
- `source`, `organization_id`, `date_from`, `date_to`.

Правило: AI и инсайты не должны использовать данные с низким confidence без явной пометки в ответе.

## D1 — OLAP Layer

Уже есть базовый слой:

- `app/integrations/iiko_client.py` — Cloud OLAP SALES + product expenses/STOCK fallback.
- `app/integrations/iiko_server_client.py` — iiko Server OLAP v2.
- `app/services/iiko_sales_factory.py` — выбор Cloud/Server per organization.
- `app/services/iiko_olap_sales_sync.py` — ETL в `sales_fact_orders`, `sales_fact_items`, `sales_daily_agg`, `sales_hourly_daily(source=iiko_olap)`.

Статус: D0 вставлен между fetch и fact tables: OLAP sync пишет raw snapshot, validation report, canonical rows и затем обновляет fact/aggregate слой.

## D2 — Food Cost

Уже есть базовый sync `app/services/iiko_food_cost_sync.py`, который обновляет `MenuItem.cost_price` и `SalesFactItem.cost`. Следующий шаг — перевести себестоимость в canonical ingredient/product model, иначе переименования блюда будут ломать маржу.

## D3 — Dashboard

Уже есть AI Center → «Продажи» и API `/analytics/sales/*`. Следующая итерация: отображать data quality status рядом с графиками, чтобы менеджер видел «данные свежие/частичные/низкая уверенность».

## A1 — Anomaly Engine

Уже есть `sales_anomaly_engine.py` поверх `sales_daily_agg`. Следующая итерация: каждый инсайт должен иметь confidence, evidence и drill-down path.

## C1 — Copilot

Уже есть safe tool-based Copilot (`app/services/copilot/`) и подключение к `/api/admin/intelligence/query`. Это правильнее, чем NL→SQL, потому что org_id и набор разрешённых инструментов контролирует сервер.

Ограничение текущей версии: Copilot реактивный. Следующий продуктовый шаг — first-class delivery:

- Telegram owner alerts;
- WhatsApp/SOS для управляющего;
- weekly/daily digest;
- push на вкладку «Требует внимания».

## C1.5 — Explainability + Confidence

Каждый инсайт и ответ Copilot должен иметь структуру:

```json
{
  "insight": "Вероятная причина падения выручки — снижение продаж напитков.",
  "confidence": 0.82,
  "evidence": [
    "Напитки просели на 22%",
    "Кофе просел на 31%",
    "Общий поток гостей снизился на 4%"
  ],
  "drilldown": [
    {"level": "revenue", "delta_pct": -15},
    {"level": "category", "name": "Напитки", "delta_pct": -22},
    {"level": "dish", "name": "Кофе", "delta_pct": -31}
  ]
}
```

Минимальные таблицы/поля:

- `OperationalInsight.confidence_score`;
- `OperationalInsight.evidence_json`;
- `OperationalInsight.drilldown_json`.

Ответы должны говорить «вероятная причина» и «уверенность», а не утверждать причинность как факт.

## OperationalInsight — контракт данных

Единый источник правды по модели инсайтов (ops-endpoints — в [`AI_OPERATIONS.md`](AI_OPERATIONS.md)).

### Поля ORM

```python
id, organization_id
insight_type: str      # см. таблицу типов ниже
severity: str          # info | warning | critical
title, summary: str
status: str            # new | seen | resolved | dismissed
was_useful: bool|None
notes: str|None
payload_json: dict
confidence_score, evidence_json, drilldown_json
created_at, resolved_at
```

### payload_json (ключевые блоки)

- `baseline_type`, `weekday_baseline` — duration-match и same-weekday сравнение
- `cause_hypotheses`, `recommended_actions`
- `current` / `previous` / `changes`, `top_items`, `lost_revenue_estimate`

### Типы инсайтов

| insight_type | severity | Условие (эвристика) |
|---|---|---|
| `revenue_drop` | warning | Выручка ≤ −15% vs предыдущий период |
| `orders_drop` | warning | Заказы ≤ −15% |
| `cancellations_up` | critical | Доля отмен +5 п.п. и выше |
| `sales_stable` | info | Аномалий нет |
| `ai_token_spike` | warning | Токены > 3× rolling-7d |
| `ai_error_spike` | critical | errors/calls > 15% |
| `ai_latency_spike` | warning | p95 > 1.5× SLA |

### Causal attribution (v1)

| Гипотеза | Условие |
|---|---|
| `high_cancellation_rate` | cancel_rate > 15% |
| `kitchen_overload` | kitchen_load > 80% |
| `stoplist_growth` | stoplist_count > prev × 1.3 |
| `ai_escalation_spike` | escalation_rate > 20% |

## M1 — Organization Memory

Самый недооценённый слой. Это память организации, не память пользователя.

Примеры:

```text
2026-04 — повысили цену на кофе на 10%
2026-05 — сменили поставщика молока
2026-06 — запустили летнее меню
```

Зачем нужно:

- объяснять изменения через историю ресторана;
- связывать решения менеджера с последующими метриками;
- делать Copilot похожим на систему, которая понимает конкретный ресторан, а не просто читает таблицы.

Минимальная модель:

- `organization_memory_events`;
- `event_date`;
- `event_type` (`price_change`, `supplier_change`, `menu_change`, `campaign`, `staff_change`, `manual_note`);
- `entity_type` / `entity_id`;
- `summary`;
- `payload_json`;
- `source` (`operator`, `system`, `import`);
- `confidence_score`.

Copilot должен использовать memory при объяснении изменений: «маржа изменилась после перехода на нового поставщика».

## C2 — Restaurant Knowledge Graph

Не нужен Neo4j или отдельная инфраструктура. Для первой версии достаточно relational graph:

```text
dish
category
ingredient
supplier
margin
seasonality
```

Минимальные таблицы связей:

- `dish_ingredients`;
- `ingredient_suppliers`;
- `dish_margin_profile`;
- `dish_seasonality_profile`;
- `dish_substitution_links`.

Вопросы, которые C2 должен закрывать:

- какие блюда стоит убрать;
- какие блюда дают выручку, но убивают маржу;
- что произойдёт, если поднять цену на 5%;
- какие блюда зависят от дорогого поставщика;
- какие позиции сезонно проседают.

## O1 — Forecasting

Прогноз спроса должен идти после D0, C1.5 и M1, иначе модель будет строить красивые прогнозы на грязных данных и без знания истории ресторана.

## F1 — ROI Loop

Остаётся сильнейшим moat:

```text
совет → применили → измерили → показали деньги
```

ROI нельзя считать без D0/C1.5/M1, иначе невозможно доказать, что результат связан с действием, а не с грязными данными или внешним событием.

## X1 — Autonomy (future)

Автономность отложена. Ближайший продуктовый фокус: правильные ответы, explainability, confidence/evidence, измеримый ROI.

### Что разрешено (Human-in-the-loop, 2026-06)

| Действие | Статус | Поведение |
|---|---|---|
| Изменения **внутри RestoMind** (upsell rules, force-close, org meta) | ✅ MVP | `agent_action_proposals` → propose → **confirm** оператором → apply |
| **Staged** iiko write (цены/меню) | ✅ Staged only | Черновик в RestoMind + подтверждение; **без** автономного вызова iiko API |
| Автономная запись в iiko / закупки без человека | ❌ Freeze | Запрещено до guardrails X1 и audit trail |

**Staged iiko write** — это не снятие freeze: система готовит структурированный запрос и ждёт явного approve; отправка во внешнюю систему — отдельный будущий этап после X1.

Разрешено также guarded internal action: черновик закупки, задача, уведомление — без внешней финансовой мутации без подтверждения человека.

## Операционный запуск текущего MVP

1. Применить миграции: `alembic upgrade head`.
2. Для Cloud OLAP выдать `api/1/reports/olap`; при отсутствии права использовать Server source.
3. Настроить `iiko_data_source=cloud|server` и server-поля в `organizations` или env fallback.
4. Выполнить первичный backfill:

```bash
python scripts/backfill_olap_sales.py --org-id 1 --since 30d
```

5. Проверить `/api/admin/analytics/sales/overview` и AI Center → «Продажи».
6. Перед расширением автономности сохранять D0/M1 как обязательные guardrails: все новые AI tools должны показывать data quality confidence и учитывать memory events.

## Gap to 100% — что нужно добить

Текущий статус: trust-layer MVP уже реализован и покрыт тестами, но это еще не полная Restaurant OS. Полная готовность означает, что цифра в Copilot не только посчитана, но и доказуема: известен источник, качество, lineage, вклад факторов, связанная память ресторана и измеренный результат действия.

### 1. Canonical-first pipeline

Реализовано: OLAP sync пишет raw snapshot, validation report и canonical rows, а fact tables строятся из canonical sales orders/items. Строгая цепочка:

```text
iiko/source -> source_data_snapshots -> data_quality_reports -> canonical_sales_* -> sales_fact_* -> aggregates -> AI
```

Definition of done (status: implemented):

- `sales_fact_orders/items` строятся только из `canonical_sales_orders/items`;
- raw rows используются только для snapshot/debug, не как источник фактов;
- все отброшенные строки сохраняются в quarantine/report sample;
- у fact rows есть `snapshot_id`/`canonical_*_id` или другой lineage key;
- тест: изменение raw duplicate не меняет fact без изменения canonical row.

### 2. Lineage и reconciliation

Нужно, чтобы любая цифра в ответе Copilot могла быть объяснена до источника.

Definition of done (status: implemented for latest snapshot/fact lineage):

- для агрегата можно получить `source_snapshot_id`, checksum и data quality report;
- `sum(sales_fact_items.revenue)` сверяется с `sales_fact_orders.revenue`;
- расхождения пишутся в `data_quality_reports` как reconciliation issue;
- Copilot tool `get_data_lineage(metric, period)` возвращает snapshot/checksum/report/fact counts;
- UI в AI Center показывает "почему этим данным можно верить".

### 3. Deep explainability/drilldown

Текущий C1.5 пишет confidence/evidence/drilldown, но drilldown пока базовый. До 100% нужен разбор вклада факторов.

Definition of done (status: implemented for revenue/category/dish/hour baseline drilldown MVP):

- revenue delta раскладывается на `orders`, `guests`, `avg_check`;
- category delta показывает топ категорий, которые внесли вклад в падение/рост;
- dish delta показывает блюда, quantity/revenue/margin contribution;
- hourly delta показывает слабые часы против baseline;
- каждый `OperationalInsight` имеет machine-readable `evidence_json` и `drilldown_json`;
- UI показывает confidence badge, evidence list и drilldown path.

### 4. Proactive delivery as product surface

Сейчас есть delivery history, cron, dedupe и Telegram guarded delivery. До 100% нужно превратить это в полноценный рабочий контур.

Definition of done (status: delivery history/actions/settings API and AI Center inbox UI implemented):

- inbox "Требует внимания" строится из `OperationalInsight`;
- есть endpoints для `read`, `dismiss`, `action_taken`;
- severity/channel rules настраиваются per org;
- daily digest и weekly digest включают top insights, ROI и data quality warnings;
- один и тот же insight не отправляется повторно без нового evidence/change;
- история доставок видна в API/UI.

### 5. Organization Memory автогенерация

Сейчас память есть, ручные notes есть, часть событий пишется автоматически. До 100% нужно, чтобы ресторанная история заполнялась сама.

Definition of done (status: implemented for menu import, manual price/cost changes, CSV cost import, supplier graph link, marketing campaign, resolved anomaly and ROI measurement):

- menu import создает `menu_change/menu_import` memory event;
- price change создает `price_change`;
- supplier/cost update создает `supplier_change` или `cost_change`;
- campaign/manual business event создает `campaign`;
- resolved major anomaly создает `major_anomaly_resolved`;
- Copilot в объяснениях явно использует релевантные memory events.

### 6. Knowledge Graph ETL и Menu Profit Lab v2

Сейчас таблицы и tools есть, но граф нужно наполнить и сделать источником маржинальности.

Definition of done (status: implemented for menu/cost/inventory/sales fact sources; future 1C/Sheets adapters feed same profiles):

- ETL из food cost/product expenses, CSV, 1C/Sheets future source наполняет `dish_ingredients` и `ingredient_suppliers`;
- `dish_margin_profile` пересчитывается по ingredient graph, а не только по `MenuItem.cost_price`;
- Menu Profit Lab читает graph profile;
- Copilot умеет отвечать "что будет при +5% цены" с учетом cost/margin/history;
- supplier exposure показывает, какие блюда и какая выручка зависят от поставщика.

### 7. Forecasting v2 production quality

Сейчас прогноз использует weekday history, confidence, memory/data quality. До 100% нужен устойчивый прогноз для операций.

Definition of done (status: implemented):

- forecast считает dish/category/day/week seasonality;
- грязные дни с низким data quality снижают вес или исключаются;
- memory events типа campaign/menu/price участвуют как факторы;
- forecast пишет confidence и basis rows;
- SupplyMind использует dish/category forecast для количества закупки;
- тесты покрывают сезонность, грязные данные и отсутствие истории.

### 8. ROI loop with product UX

Сейчас есть baseline/measurement windows и measured event. До 100% нужно сделать это продаваемым доказательством ценности.

Definition of done (status: chain API, owner digest and AI Center ROI block implemented):

- каждая рекомендация связана с `insight_id`, `action_id`, `baseline_window`, `measurement_window`;
- UI показывает цепочку "совет -> выполнено -> результат";
- Owner digest показывает realized money и confidence;
- measurement учитывает data quality и memory events;
- если причинность слабая, система пишет "correlation, not proven causality";
- есть идемпотентность measurement consumer.

### 9. Live/open orders layer

OLAP дает закрытые чеки. Для "прямо сейчас" нужен отдельный предварительный слой.

Definition of done (status: implemented as preliminary live preview API/tool):

- `live_sales_preview` или аналог считает open orders отдельно от OLAP facts;
- все live цифры помечены `preliminary=true`;
- live layer не смешивается с закрытыми фактами без явной метки;
- Copilot различает "закрытая выручка" и "ожидаемая/live выручка".

### 10. Timezone и source confidence

Definition of done:

- все OLAP close/open times нормализуются через timezone организации;
- data quality report хранит source confidence: Cloud/Server/CSV/Sheets/manual;
- schema version/fields hash сохраняется для OLAP response;
- изменение набора OLAP полей создает warning.

## Рекомендуемый порядок до 100%

1. Canonical-first pipeline + lineage/reconciliation.
2. Deep drilldown по revenue/category/dish/hour.
3. Proactive inbox + delivery action endpoints.
4. Memory autogeneration из menu/cost/price/campaign событий.
5. Future source adapters: 1C/Sheets feed the canonical/graph profiles without changing Copilot contracts.
6. Dedicated visual ROI screen on top of `/intelligence/roi-outcomes`.
7. Dedicated audit screen for lineage beyond the existing sales quality banner, insight trust blocks and Copilot `get_data_lineage` tool.
8. Live/open orders preliminary layer.
9. Только после этого расширять X1 autonomy.
