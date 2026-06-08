# RestoMind OS — План перехода SaaS → AI Operating System

Этот документ фиксирует **реальный engineering roadmap** перехода, привязанный к текущему состоянию кода, а не к архитектурным концепциям.

Принцип: **Strangler Pattern** — новые слои добавляются как обёртки вокруг работающей системы. Ничего не переписывается целиком. Прод не останавливается.

---

## Текущее состояние (актуально на 2026-06-08)

| Фаза | Что реализовано | Готовность | Ключевые файлы |
|------|----------------|------------|----------------|
| Phase 1: Franchise / Tenant | `Tenant.is_network`, Branch Switcher, `/network/*`, Manager `assigned_org_ids`. **Location (1.1):** `locations`, `location_id` на Order/ChatLog/Booking, RBAC, location-aware деньги/SLA/UI-фильтр | **✅ 100%** | `network.py`, `tenant_scope.py`, `analytics.py`, `_header.html` |
| Phase 2: Event System | `emit_event`, `DailyOrgStats`, backfill (+ `dialogs_count`), WS fanout, `audit_log`, `os.audit`, `integration.*`, event-first `/stats`/`/analytics`/`/funnel` | **✅ 100%** | `system_events.py`, `analytics_consumer.py`, `audit_consumer.py` |
| Phase 3: AI Context Snapshot | Frozen menu + `chat_history_slice`, replay с историей, `GET /snapshots` + `GET /snapshots/{id}` + `POST …/replay`, `menu_prices_snapshot` fallback | **~100%** ✅ | `context_engine.py`, `intelligence.py` — дубликат `GET /snapshots` убран |
| Phase 4: Decision Engine | 8 правил DE + `tenant` в `AIReadContext`, интеграция в WhatsApp pipeline | **✅ 100%** | `decision_engine.py`, `webhooks.py` |
| Phase 5: Full OS Behavior | Predictive + autopilot pricing (single + bulk), healing 2.0 WA, digest backend, GuestCare, stock alerts, Decision Feed UI | **~98%** | `owner_dashboard.py`, `healing_actions.py`, `intelligence.py` |
| Final Mile (backend) | SupplyMind + iiko Office sync, StaffMind onboarding, Voice (`stt_fallback` + Realtime code), Daily OS Digest cron, GuestCare 2GIS sync | **MVP ✅** | [`docs/FINAL_MILE_IMPLEMENTED.md`](FINAL_MILE_IMPLEMENTED.md) |
| Final Mile (UI) | SupplyMind / StaffMind / Voice toggle / digest preview / GuestCare sync ? ??????? | **wired ?** | [`docs/FINAL_MILE_OPS_SIGNOFF.md`](FINAL_MILE_OPS_SIGNOFF.md) ? **ops gates** |
| **UI Layer (Phase 6a)** | Focus-Driven Admin Shell: 3 режима, split Shift, Action Queue inbox, Command Bar | **✅ Sprint 1–4** (Strangler) | [`docs/UI_DESIGN_SYSTEM.md`](UI_DESIGN_SYSTEM.md), ROADMAP P5 «Focus-Driven OS» |
| **Executive OS (Phase 6b)** | Executive Hub поверх вкладок, агент как command surface, `agent_action_proposals` как валидируемые команды + audit events | **MVP ✅** | `executive_hub.py`, `agent_actions.py`, `intelligence.py`, ROADMAP § Phase 6 |
| **Intelligence OS (Restory-class)** | iiko OLAP fact layer, D0 data quality, explainable insights, organization memory, tool-based AI Analyst, knowledge graph, forecasting v2, ROI feedback loop, guarded experimental drafts | **Trust-layer ✅** | [`docs/INTELLIGENCE_OS_PLAN.md`](INTELLIGENCE_OS_PLAN.md), `iiko_olap_sales_sync.py`, `data_quality.py`, `copilot/` |

**Главный вывод (2026-06-08):** RestoMind OS перешёл от “visibility” к **Executive OS MVP**: Hub становится верхним слоем, вкладки — audit/drill-down, агент получает безопасные “руки” через валидируемые команды и human-in-the-loop. Следующий слой — proactive apply из Telegram и live iiko write после dry-run/owner-confirm guardrails.

### Что остаётся для 100%

| Компонент | Статус | Комментарий |
|-----------|--------|-------------|
| `audit_consumer` для `conversation.state_changed` / `ai.response.generated` | Намеренно пропущен | Высокочастотные события — шум без бизнес-пользы |
| Auto-price без подтверждения | Не реализован | Изменение цен требует явного `POST /apply-pricing` от владельца |
| `websocket_consumer` полный (org-scoped channel) | **✅ ~95%** | `publish_org_event` → `admin_events:org:{id}` + legacy global |
| `audit_consumer` для admin-действий (вне emit_event) | **✅ middleware** | `admin_action_audit_middleware` → `audit_log` + `os.audit` |
| Admin order lifecycle events (канбан cancel/confirm) | **✅** | `order_admin_events.py`, `bulk-cancel` → `order.cancelled`; confirm → `actor=operator` |
| Ops/integration events в DailyOrgStats | **✅** | `pricing_adjustments`, `sla_violations`, `healing_wa_sent`, `draft_recovery_sent`, `whatsapp_delivery_failed` |
| All-time KPI из events (без SQL к Order) | **✅ org-level** | `get_cumulative_event_totals()` в `/stats`; location-scoped — SQL fallback |
| Legacy NULL org/location backfill | **✅ API** | `tenant_backfill.py`, `GET/POST /intelligence/tenant-scope-*` |
| Per-org admin rate limit | **✅** | `admin_org_rate_limit_middleware`, `ADMIN_RATE_LIMIT_PER_MINUTE` |
| Postgres RLS | **✅ core** | `20260609_tenant_rls`; phase 2 для оставшихся таблиц — отдельный hardening |
| Admin UI i18n kk | Не реализован | Админка — русский inline; kk — отдельный эпик |
| Event-first per-location aggregate | ✅ Phase 1.2 | Rollup из `SystemEvent._location_id` в `owner_dashboard.py`; org-level `DailyOrgStats` без изменений |

---

## Phase 1 — Franchise / Branch (Иерархия Владелец → Филиалы)

**Почему это первое:** единственный шаг, который одновременно упрощает UX для одиночного кафе и открывает enterprise-продажи сетям. Не требует переписывания ядра.

### Ключевая идея

Вводим флаг `Tenant.is_network` (bool). Он управляет режимом всего интерфейса:

- `is_network = False` — обычное заведение: никаких упоминаний филиалов, максимально простой UI
- `is_network = True` — франшиза/сеть: Branch Switcher в шапке, агрегированная аналитика по всей сети, управление филиалами

Переключение флага — мгновенное. Кафе открывает вторую точку → ставим галочку → появляется весь функционал сети. Никаких миграций данных.

### Иерархия сущностей

```
Tenant (владелец аккаунта)
  └── Organization (филиал / заведение)   ← уже есть
        ├── Menu, Orders, ChatLogs, Bookings …
        └── Users (операторы этого филиала)
```

`Organization` уже есть и уже изолирована по `organization_id`. Добавляем только:
- флаг `Tenant.is_network` (миграция: одно поле)
- связь `User.assigned_org_ids: list[int]` для Manager (уже можно хранить в `User.meta_json` без миграции)

### Режим `is_network = False` (одиночное кафе)

Что скрыть в UI (Alpine `x-show`, без удаления разметки):
- выпадающий список выбора филиала в шапке
- кнопка «Все заведения» в аналитике
- раздел «Управление филиалами» в настройках
- любые сравнительные метрики «филиал vs сеть»

Флаг `is_network` передаётся в `GET /api/admin/auth/me` (уже есть `branding`, добавить рядом). Alpine хранит в `appData.isNetwork` и управляет `x-show` без серверного рендеринга.

### Режим `is_network = True` (сеть/франшиза)

**Branch Switcher в шапке:**
- текущий филиал + выпадающий список всех `Organization` этого `Tenant`
- пункт «Вся сеть» → переключает в режим агрегации

**Аналитика «Вся сеть»:**

```python
async def network_stats(tenant_id: int, db: AsyncSession) -> dict:
    """Агрегат по всем org этого тенанта."""
    org_ids = await get_tenant_org_ids(db, tenant_id)
    # SELECT SUM(revenue), SUM(orders) … WHERE organization_id = ANY(org_ids)
    ...
```

Не делать `SELECT *` — только агрегаты (SUM, AVG, COUNT). Отдельный endpoint `/api/admin/network/stats`.

**Матрица доступа:**

| Роль | Что видит | Филиалы |
|------|-----------|---------|
| Owner | Всё, включая «Вся сеть» | Все org тенанта |
| Manager | Операции + аналитика | Только `assigned_org_ids` |
| Operator | Заказы, чаты | Только текущий `organization_id` |

### Бэкенд: минимальные изменения

**1. Миграция — одно поле:**

```sql
ALTER TABLE tenants ADD COLUMN is_network BOOLEAN NOT NULL DEFAULT FALSE;
```

**2. `GET /api/admin/auth/me` — добавить поле:**

```python
# В существующий ответ auth/me добавить:
"is_network": tenant.is_network,
"network_orgs": [  # только если is_network = True
    {"id": org.id, "name": org.name}
    for org in tenant_orgs
] if tenant.is_network else []
```

**3. Новый endpoint для сети (только при `is_network = True`):**

```python
GET /api/admin/network/stats          # агрегированная аналитика по всей сети
GET /api/admin/network/orgs           # список всех филиалов тенанта
POST /api/admin/network/switch/{org_id}  # переключиться в конкретный филиал
```

**4. Guard на уровне сессии:**

```python
def require_network(request: Request):
    if not request.session.get("tenant_is_network"):
        raise HTTPException(403, "Not a network account")
```

### Фронтенд: Alpine-флаг управляет всем UI

В `loadAuthProfile()` (уже есть в `admin-app.js`):

```js
this.isNetwork = data.is_network ?? false;
this.networkOrgs = data.network_orgs ?? [];
```

В шаблонах: `x-show="isNetwork"` / `x-show="!isNetwork"` — без дублирования разметки.

Branch Switcher в шапке `admin.html` (аналог существующего `select-org` dropdown):

```html
<template x-if="isNetwork">
  <div class="relative" x-data="{ open: false }">
    <button @click="open = !open" class="...">
      <span x-text="currentOrgName"></span>
      <span class="text-gray-400">▾</span>
    </button>
    <div x-show="open" x-cloak class="ds-dropdown">
      <button @click="switchToAllNetwork()" class="...">Вся сеть</button>
      <template x-for="org in networkOrgs" :key="org.id">
        <button @click="switchOrg(org.id)" x-text="org.name" class="..."></button>
      </template>
    </div>
  </div>
</template>
```

### Что НЕ трогать в этой фазе

- event system, AI context, decision engine, prompt-логику
- существующую изоляцию по `organization_id` — она остаётся фундаментом
- схему `Organization` — новых колонок не нужно, только новый endpoint агрегации

---

## Phase 2 — Event System Stabilization

**Почему это второе:** аналитика сейчас идёт мимо событий (прямые SQL-запросы к `Order`, `ChatLog`). Это делает аналитику хрупкой и дорогой.

### Что сейчас

`emit_system_event` в [`app/services/system_events.py`](app/services/system_events.py) уже пишет в `SystemEvent`.
Но нет единого стандарта, нет consumers, нет replay.

### Единая схема события

```python
@dataclass
class BusinessEvent:
    id: str              # UUID
    org_id: int
    location_id: int | None
    type: str            # "order.created" | "booking.confirmed" | "ai.response.generated" | …
    timestamp: datetime
    actor: str           # "ai" | "operator" | "customer" | "system"
    payload: dict
    version: int         # для backward-compat при изменении схемы
```

### Единая точка записи

```python
async def emit_event(db: AsyncSession, event: BusinessEvent) -> None:
    """Единственный способ записи бизнес-событий. Прямая запись в system_events запрещена."""
    ...
```

Прямая запись в `system_events` — запрещена (добавить в CONVENTIONS.md).

### Consumers (добавляются поочерёдно)

| Consumer | Что делает |
|----------|-----------|
| `analytics_consumer` | Пересчёт агрегатов по событию, замена прямых SQL в `/api/admin/stats` |
| `websocket_consumer` | Real-time обновление UI без polling |
| `ai_context_feeder` | Добавление события в `AIContextSnapshot` (Phase 3) |
| `audit_consumer` | Иммутабельный аудит-лог действий |

### Бизнес-события первой очереди

```
order.created
order.confirmed
order.cancelled
booking.created
booking.confirmed
ai.response.generated
ai.escalated
operator.took_over
```

---

## Phase 3 — AI Context Snapshot

**Почему это третье:** сейчас контекст для LLM пересобирается каждый раз заново и нигде не сохраняется. Невозможно воспроизвести или объяснить решение AI.

### Что сейчас (актуально)

`fetch_ai_read_context` в [`app/services/context_engine.py`](app/services/context_engine.py) строит `AIReadContext` и **персистируется** в `AIContextSnapshot` перед LLM (WhatsApp, test_bot, telephony stub). Replay: `GET/POST /api/admin/intelligence/snapshots*`.

### Что добавить

**`AIContextSnapshot` таблица** (новая миграция):

```python
class AIContextSnapshot(Base):
    __tablename__ = "ai_context_snapshots"

    id: Mapped[str]             # UUID
    org_id: Mapped[int]
    phone: Mapped[str]
    created_at: Mapped[datetime]
    business_state: Mapped[dict]   # menu, stoplist, org settings
    customer_state: Mapped[dict]   # история, pending order, предпочтения
    event_slice: Mapped[dict]      # последние N событий до этого момента
```

**Привязка к LLM-вызову:**

```python
snapshot_id = await save_ai_context_snapshot(db, context)
response = await call_ai_with_context(context, snapshot_id=snapshot_id)
# snapshot_id сохраняется в ChatLog.meta_json["snapshot_id"]
```

**Replay mode:**

```python
async def replay_ai_decision(snapshot_id: str) -> AIBrainResponse:
    """Воспроизвести решение AI с тем же контекстом что был в момент X."""
    snapshot = await load_snapshot(snapshot_id)
    return await call_ai_with_context(snapshot.to_ai_context())
```

---

## Phase 4 — Decision Engine

**Почему это четвёртое:** только после AI Context Snapshot имеет смысл формальный валидационный слой.

### Seed уже есть

`validate_order` в [`app/services/intent_router.py`](app/services/intent_router.py) — тактический Decision Engine для заказов. Нужно обобщить паттерн.

### Целевая модель

```
AI → Proposal
     ↓
Decision Engine → проверяет:
  - бизнес-правила (цены, лимиты, стоп-лист)
  - RBAC (может ли этот актор выполнить действие)
  - tenant policy (ограничения конкретного ресторана)
     ↓
System → исполняет
```

### Пример

AI предлагает: «выдать скидку 50%».
Decision Engine: `max_discount_pct = 15` по policy ресторана → отклонить, вернуть `PolicyViolation`.

---

## Phase 5 — Full OS Behavior

Только после Phase 1–4. Включает:

- Аналитика строится исключительно из event stream (не из прямых SQL к `Order`)
- Predictive insights: прогноз выручки, предсказание спроса
- Auto-recommendations без ручного refresh
- Self-healing: система сама детектирует и эскалирует операционные проблемы

**Статус (2026-06-08):** ~98% — см. ROADMAP P4/P5. Backend OS-слои, **Focus-Driven Admin Shell** и **Executive Hub v2** закрыты в коде; остаются ops-gates Final Mile и автономные внешние write-guardrails (§ ниже).

---

## Executive OS — Actionable AI (Phase 6b)

> **Новая точка невозврата продукта.** RestoMind перестаёт быть “умным зеркалом” и становится AI-управляющим: верхний слой объясняет, агент предлагает действие, человек подтверждает, система исполняет и оставляет аудит.

### Контракт Phase 6b

| Слой | Назначение | Статус |
|------|------------|--------|
| Executive Hub | 4–6 narrative cards поверх вкладок, Health/Money/Quality/Ops, drill-down в доказательства | ✅ |
| Agent as SPOT | `/intelligence/query` держит контекст и создаёт pending actions вместо простых советов | ✅ MVP |
| Command layer | `agent_action_proposals` хранит валидируемые команды с `_command` metadata | ✅ MVP |
| Human-in-the-loop | Любая мутация force-close / upsell / staged iiko требует confirm | ✅ |
| Audit trail | `SystemEvent`: `agent_action.proposed`, `confirmed`, `applied`, `rejected` | ✅ |
| Learning loop | `AIContextSnapshot` feedback → `organization_memory_events` | ✅ MVP |
| Proactive apply | Telegram/digest deep-link в тот же confirm contract | ⏳ |
| iiko write live | Dry-run preview → explicit owner confirm → adapter write → audit | ⏳ |

### Command rules

LLM не пишет напрямую в доменные таблицы и не вызывает внешний iiko write. Он может только предложить команду:

```text
Agent proposes Command -> server validates payload -> owner confirms -> executor applies -> SystemEvent audit
```

Текущий registry команд:

- `ForceCloseRestaurantCommand` → `force_close`
- `CreateUpsellRuleCommand` → `upsell_rule_create`
- `StageIikoWriteCommand` → `iiko_write_staged` (без live iiko side effect)

Следующий кодовый шаг: связать proactive `InsightDelivery`/Telegram с тем же `/agent-actions/{id}/confirm`, чтобы владелец мог применять действие из push-сообщения, а не только из Hub.

---

## UI Layer — Focus-Driven Admin Shell (Phase 6a, Strangler)

> **Не новая бизнес-логика.** Перенос «центра тяжести» с SaaS-вкладок на **трёхрежимную операционную оболочку** (Admin Shell), связанную с G10 Shift Control Plane. Прод не останавливается: старые hash-URL и сайдбар P1.5.0 живут параллельно до Sprint 4.

### Три закона Execution OS

| Закон | Смысл |
|-------|--------|
| **LAW 1 — Single Focus** | В SHIFT MODE ровно один `shiftState.focus`; UI не сортирует очередь и не считает S0–S5 (см. [`G10_SEMANTIC_CONTRACT.md`](G10_SEMANTIC_CONTRACT.md) §5). |
| **LAW 2 — Sequential Mobile Cognition** | На `<lg` смена = два экрана (Focus → Context), не две колонки. |
| **LAW 3 — Locality of Operations** | Операционные сигналы (риск, чаты, звонки, стоп-листы) scoped по `location_id` шапки; org-wide aggregate — только Intelligence / owner summary. |

### Режимы (целевая матрица)

| Режим | Аудитория | Вкладки (allowed `currentTab`) | Сайдбар | Селектор точки |
|-------|-----------|----------------------------------|---------|----------------|
| **SHIFT** 🟢 | Оператор | `shift_control` | Скрыт | Виден (locality) |
| **CONTROL** 🟡 | Менеджер | `inbox`, `orders`, `chats`, `bookings`, `menu` | Виден (Операции) | Виден |
| **INTELLIGENCE** 🔵 | Владелец | `dashboard`, `ai_center`, `settings` | Виден (Управление) | Опционально «вся сеть» / без фильтра |

**Текущее состояние кода (Sprint 1–5 ✅):** **Role-first IA** — сайдбар по роли staff (`isTabVisibleForRole`); Mode Bar убран из UI; smart landing оператора; mobile bottom nav по роли; `analyticsDensity` normal/advanced на дашборде. Internal `currentMode` (`shift|control|intelligence`) — для Command Bar и hash sync. Shift split, inbox Action Queue, Command Bar Ctrl+K сохранены.

### Engineering plan (5 спринтов)

```text
Sprint 1: Mode Engine + ds-status-* tokens (internal modes)
    ↓
Sprint 2: _shift_focus_chat / _shift_focus_order + mobile staged nav
    ↓
Sprint 3: Inbox → Action Queue UI + voice strip + location_id payload
    ↓
Sprint 4: Command Bar (Ctrl+K): /leak, /red, /force-close
    ↓
Sprint 5: Role-first pivot — убрать Mode Bar, sidebar/bottom nav по роли, analyticsDensity
```

### Архитектурные решения (зафиксированы)

1. **Mobile Shift:** Staged Focus Navigation (экран Focus → экран Context, `⬅ Назад к задаче`).
2. **Starvation / skip:** Redis TTL 600s на `skip` + кнопка **`reset_skips`** при `metrics.shift_empty_focus_while_risk_positive` и ненулевых `excluded_skip|excluded_next` (не ждать только TTL). Реализовано: FM-3, [`_tab_shift_control.html`](../app/templates/screens/_tab_shift_control.html).
3. **Voice calls:** `GET /api/admin/intelligence/voice/calls?location_id=` при активной точке; без точки — org-wide список (read-only). Фильтр API ✅; запись `location_id` в `voice_call_logs.payload_json` при `record_voice_call` ✅ (Twilio routing + Final Mile strip).

**Backend Sprint 1:** без изменений — `GET/POST /shift/*`, `money_queue`, `emit_event` уже покрывают модель.

**Ключевые файлы (целевые):** [`admin.html`](../app/templates/admin.html), [`admin-app.js`](../app/static/js/admin-app.js), [`docs/UI_DESIGN_SYSTEM.md`](UI_DESIGN_SYSTEM.md), [`docs/UI_MAP.md`](UI_MAP.md), [`G10_SHIFT_CONTROL_PLANE.md`](G10_SHIFT_CONTROL_PLANE.md).

### Focus Card (контракт UI ↔ API)

Поле `focus` в `GET /shift/state` — projection из [`shift_state_engine._focus_payload`](../app/services/shift_state_engine.py), **не** произвольный JSON:

| Поле | Тип | Назначение |
|------|-----|------------|
| `id`, `kind` | string | Идентификатор и тип (`slow_chat`, `abandoned_draft`, `pending_prepay`, …) |
| `type` | string | UI-группа: `chat` \| … (`KIND_TO_TYPE`) |
| `title`, `subtitle` | string | Заголовки карточки |
| `value_kzt` | number | Сумма риска/упущения (не `risk_kzt` в focus) |
| `wait_minutes`, `pulse` | int / string | Для чатов (G5 Live Pulse) |
| `phone`, `order_id` | string / int | Контекст для Context Dock |
| `actions` | array | ≤3 subtype из G10 (`complete`, `skip`, `next`, …) |
| `reason` | string | Engine reason (`highest_priority_score`, `active_focus_lease`, …) |

Context Dock (Sprint 2): `kind` ∈ {`slow_chat`, pulse red/amber} → `_shift_focus_chat.html`; `abandoned_draft` \| `pending_prepay` → `_shift_focus_order.html`.

---

## Решающий фактор (прямо сейчас)

**Есть ли клиент с несколькими локациями?**

- **Да** → Phase 1 (RBAC location-scope) срочно, это блокер продажи
- **Нет** → Phase 2 (Event consumers), даст аналитику без боли и подготовит Phase 3

---

## Антипаттерны (не делать)

- Не начинать Event + AI Context + Decision Engine одновременно
- Не переписывать `intent_router.py`, `context_engine.py` или `webhooks.py` целиком — только оборачивать
- Не создавать абстракции "на вырост" раньше Phase, в которой они нужны
- Не менять схему `Organization`/роли без анализа последствий для всех org (см. CLAUDE.md)
