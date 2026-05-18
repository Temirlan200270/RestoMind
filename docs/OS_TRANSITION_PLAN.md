# RestoMind OS — План перехода SaaS → AI Operating System

Этот документ фиксирует **реальный engineering roadmap** перехода, привязанный к текущему состоянию кода, а не к архитектурным концепциям.

Принцип: **Strangler Pattern** — новые слои добавляются как обёртки вокруг работающей системы. Ничего не переписывается целиком. Прод не останавливается.

---

## Текущее состояние (честная оценка)

| Фаза | Что | Готовность | Где в коде |
|------|-----|------------|-----------|
| Phase 1: Tenant Isolation | `organization_id` в каждом запросе, запрет межтенантного доступа | ~90% | CLAUDE.md как жёсткий запрет; везде в `app/api/` и `app/services/` |
| Phase 2: RBAC | `admin_ok` в сессии, роли существуют | ~60% | `app/api/admin/`; resource-scope (какой оператор видит какой чат) — слабо |
| Phase 3: Event System | `SystemEvent` модель, `emit_system_event`, `publish_event` | ~40% | `app/db/models.py`, `app/services/system_events.py`, `app/services/trace_context.py` |
| Phase 4: AI Context Layer | `fetch_ai_read_context`, `AIReadContext`, сборка контекста для LLM | ~70% | `app/services/context_engine.py` |
| Phase 5: Decision Engine | `validate_order` в intent_router — тактический seed | ~30% | `app/services/intent_router.py` |

**Главный вывод:** система уже не MVP. Это Early Production SaaS с зачатками OS-архитектуры. Задача — замкнуть нервную систему, а не перестраивать скелет.

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

### Что сейчас

`fetch_ai_read_context` в [`app/services/context_engine.py`](app/services/context_engine.py) уже строит `AIReadContext`. Но он не персистируется.

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
