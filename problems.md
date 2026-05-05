# Backend

🔴 Критичность: High Backend Утечка данных меню между организациями
Где: app/services/order_logic.py:197, app/api/webhooks.py:942, app/api/admin/_monolith.py:3782, app/services/intent_router.py:347
Угроза: `load_available_menu()` читает все доступные позиции без фильтра по `organization_id`. В multi-tenant SaaS это прямой data leak: оператор и ИИ одной точки могут видеть меню другой точки, собирать заказ из чужих SKU и отправлять некорректный payload в iiko.
Как исправить:
```python
# app/services/order_logic.py
async def load_available_menu(
    db: AsyncSession,
    *,
    organization_id: int | None = None,
) -> list[MenuItem]:
    stmt = select(MenuItem).where(MenuItem.available == True)
    if organization_id is not None:
        stmt = stmt.where(MenuItem.organization_id == organization_id)
    stmt = stmt.order_by(MenuItem.category, MenuItem.name)
    res = await db.execute(stmt)
    return list(res.scalars().all())

# call sites
menu_items = await load_available_menu(db, organization_id=organization_id)
```

🔴 Критичность: High Backend Dedupe WhatsApp ломает идемпотентность и может терять входящие сообщения
Где: app/api/webhooks.py:140, app/api/webhooks.py:659, app/api/webhooks.py:1384, app/services/whatsapp_idempotency.py:53
Угроза: входящий webhook сначала помечается в Redis, а durable dedupe в БД либо происходит позже, либо при ошибке просто логируется warning. Если процесс падает между preclaim и нормальной обработкой, повторная доставка может быть отброшена как дубль, хотя сообщение реально не обработано. Обратная сторона тоже плохая: при падении БД код продолжает обработку без dedupe и допускает дубли заказа/чат-лога.
Как исправить:
```python
# app/api/webhooks.py
async def process_with_retry(...):
    org_id = ...
    if whatsapp_message_id:
        async with async_session_factory() as db:
            can_process = await try_start_whatsapp_inbound_in_db(
                db,
                message_id=whatsapp_message_id,
                organization_id=org_id,
                phone=phone,
            )
            await db.commit()
        if not can_process:
            return

    last_exc = None
    for attempt in range(MAX_RETRIES):
        try:
            await process_message(...)
            if whatsapp_message_id:
                async with async_session_factory() as db:
                    await mark_whatsapp_inbound_done(db, whatsapp_message_id)
                    await db.commit()
            return
        except Exception as exc:
            last_exc = exc
            ...

    if whatsapp_message_id:
        async with async_session_factory() as db:
            await mark_whatsapp_inbound_failed(db, whatsapp_message_id, str(last_exc))
            await db.commit()
```
И отдельно: не делать Redis-preclaim до durable handoff, либо использовать его только как soft-cache после записи в БД.

🔴 Критичность: High Backend OpenAI таймауты маскируются как успешная обработка
Где: app/services/ai_brain.py:141
Угроза: после исчерпания retry `call_openai()` возвращает fallback-ответ вместо исключения. Для ARQ и webhook pipeline это выглядит как success. Под rate limit/timeout вы не получаете retry очереди, а массово эскалируете оператору то, что должно было переждаться автоматически.
Как исправить:
```python
# app/services/ai_brain.py
from openai import APIConnectionError, APIError, APITimeoutError, RateLimitError

class TransientAiError(RuntimeError):
    pass

async def call_openai(..., raise_on_transient: bool = True) -> AiResponse:
    ...
    except (APIError, RateLimitError, APIConnectionError, APITimeoutError) as exc:
        last_exc = exc
        if attempt < max_retries - 1:
            await asyncio.sleep(backoff)
            continue
        if raise_on_transient:
            raise TransientAiError(str(exc)) from exc
        return _FALLBACK_RESPONSE
```
Worker/webhook должны пропускать `TransientAiError` вверх, чтобы сработал retry.

🔴 Критичность: High Backend Redis state и БД расходятся при падении между двумя транзакциями
Где: app/services/dialog_mgr.py:74, app/api/webhooks.py:1023
Угроза: `set_user_state()`/`set_pending_order()` внутри основной обработки пишут Redis и параллельно делают best-effort `_db_sync()` в отдельной сессии. Основная транзакция по заказу и чату коммитится позже. Если процесс упадет между `_db_sync()` и главным commit, Redis и `users.current_state` будут говорить одно, а `orders/chat_logs` в БД другое. Это ломает продолжение диалога, повторную доставку и ручную обработку оператором.
Как исправить:
```python
# внутри основной транзакции webhooks
user.current_state = result.new_state.value if result.new_state else user.current_state
user.current_pending_order_id = result.pending_order_id
user.current_pending_booking_id = result.pending_booking_id
await db.commit()

# после commit только cache update
if result.new_state:
    await redis.set(state_key, result.new_state.value, ex=STATE_TTL)
```
`dialog_mgr` должен уметь режим `cache_only`, а source of truth для durable state должна быть одна транзакция БД.

🔴 Критичность: High Backend Исходящее сообщение оператора отправляется наружу до фиксации ChatLog
Где: app/api/admin/_monolith.py:3641, app/api/admin/_monolith.py:3682, app/api/admin/_monolith.py:3781
Угроза: в `admin_send_message` и resend flow сообщение уходит в WhatsApp при еще незафиксированном `ChatLog(status='sending')`. Если commit потом упадет, клиент получил сообщение, а система не знает об отправке. Повторный resend создаст дубль, история чата и audit trail разъедутся. В `admin test-bot` OpenAI тоже вызывается внутри живой DB-сессии, удерживая connection во время внешнего I/O.
Как исправить:
```python
# phase 1
chat = ChatLog(..., delivery_status='sending')
db.add(chat)
await db.commit()

# phase 2
provider_id = await send_message(...)

# phase 3
async with async_session_factory() as db2:
    row = await db2.get(ChatLog, chat.id)
    row.delivery_status = 'sent'
    row.provider_message_id = provider_id
    await db2.commit()
```
Для test-bot: сначала вычитать контекст из БД, закрыть сессию, потом вызывать OpenAI.

🟠 Критичность: Medium Backend События публикуются в UI до commit и создают phantom state
Где: app/api/webhooks.py:780, app/services/intent_router.py:661, app/api/admin/_monolith.py:3666
Угроза: WebSocket может показать новый message/order update, которого еще нет в committed БД. Следующий REST reload откатит интерфейс назад. Под нагрузкой оператор увидит "мигающие" заказы и статусы, а часть действий уйдет по устаревшим данным.
Как исправить:
```python
await db.commit()
await publish_event(...)
```
Нормальный production-вариант: outbox table + отдельный publisher worker.

🟠 Критичность: Medium Backend `publish_event()` открывает новый Redis client на каждое событие
Где: app/services/events.py:38, app/services/events.py:53
Угроза: под шквалом webhook/admin actions это создает лишние TCP/Redis handshake, повышает latency и расходует connection budget. Там же синхронно дергается staff notification, что расширяет критический путь пользовательского запроса.
Как исправить:
```python
# app/services/events.py
_event_redis: Redis | None = None

async def get_event_redis() -> Redis:
    global _event_redis
    if _event_redis is None:
        _event_redis = Redis.from_url(settings.redis_url, decode_responses=True)
    return _event_redis

async def publish_event(...):
    redis = await get_event_redis()
    await redis.publish(channel, payload)
```
Staff notification лучше вынести в очередь.

🟠 Критичность: Medium Backend Payload `order_updated` недостаточен для version-guard на клиенте
Где: app/api/admin/_monolith.py:730, app/services/intent_router.py:661
Угроза: часть событий приходит без полной сериализации заказа и без актуального `row_version`. Клиент вынужден делать `loadOrders()`, а это снова открывает окно гонки между WS и REST.
Как исправить:
```python
await publish_event(
    org_id,
    {
        "type": "order_updated",
        "order": serialize_order(order),
        "row_version": int(order.row_version),
    },
)
```

🟡 Критичность: Low Backend Дублирование логики upsell уже начало расходиться
Где: app/services/sales_strategy.py, app/services/strategy_engine.py, app/services/upsell_utils.py
Угроза: две ветки считают состав корзины, rejected/offered items и eligibility по-разному. Бот начнет повторно предлагать отклоненные позиции, а аналитика и фактический диалог будут видеть разные состояния.
Как исправить:
```python
# app/services/upsell_utils.py
def cart_iiko_ids(items: list[dict[str, Any]]) -> set[str]: ...
def rejected_upsell_iiko_ids(meta: dict[str, Any]) -> set[str]: ...
def offered_upsell_iiko_ids(meta: dict[str, Any]) -> set[str]: ...
```
Оставить один модуль и перевести обе стратегии на него.

🟡 Критичность: Low Backend Подозрительный лишний файл-интеграция
Где: app/integrations/telephony.py
Угроза: файл выглядит как runtime-stub без реального использования. Такие полуинтеграции дают ложный сигнал, что канал поддерживается, и повышают шанс, что кто-то начнет опираться на мертвый код.
Как исправить: либо удалить файл, либо явно пометить его как draft/dev-only и исключить из production surface.

# Frontend

🔴 Критичность: High Frontend `loadOrders()` может перетереть более свежие WS-данные
Где: app/static/js/admin-app.js:2895, app/static/js/admin-app.js:3509
Угроза: текущая защита по `row_version` работает только для пересекающихся id, но финальное `this.orders = incoming.map(...)` выбрасывает локальные более свежие заказы, которых не было в REST snapshot. Результат: оператор видит откат статуса, может повторно отправить заказ в iiko или принять решение по устаревшим данным.
Как исправить:
```javascript
async loadOrders() {
  const reqId = ++this._ordersLoadSeq;
  const { ok, status, data } = await this.apiJsonResponse(`/api/admin/orders?${p.toString()}`);
  if (reqId !== this._ordersLoadSeq) return;
  if (!ok) {
    this.ordersLoadError = this.formatApiError(data.detail) || `Не удалось загрузить заказы (${status})`;
    return;
  }

  const incoming = Array.isArray(data.orders) ? data.orders : [];
  const merged = new Map(this.orders.map((o) => [Number(o.id), o]));
  for (const next of incoming) {
    const id = Number(next.id);
    const prev = merged.get(id);
    if (!prev || Number(next.row_version || 0) >= Number(prev.row_version || 0)) {
      merged.set(id, next);
    }
  }
  this.orders = Array.from(merged.values());
}
```

🟠 Критичность: Medium Frontend `order_updated` без полного заказа делает version-guard почти бесполезным
Где: app/static/js/admin-app.js:2728, app/static/js/admin-app.js:2895
Угроза: когда WS payload не несет `data.order`, клиент падает обратно в `loadOrders()`. Это возвращает ту же race-condition, которую versioning должен был убрать.
Как исправить:
```javascript
onOrderUpdated(data) {
  if (!data.order) return;
  const oid = Number(data.order.id);
  const idx = this.orders.findIndex((o) => Number(o.id) === oid);
  const prev = idx >= 0 ? this.orders[idx] : null;
  if (prev && Number(prev.row_version || 0) > Number(data.order.row_version || 0)) return;
  if (idx >= 0) this.orders.splice(idx, 1, data.order);
  else this.orders.unshift(data.order);
}
```
И на backend всегда отправлять сериализованный `order`.

🟠 Критичность: Medium Frontend Silent fail в административных действиях
Где: app/static/js/admin-app.js:2793, app/static/js/admin-app.js:3061, app/static/js/admin-app.js:3543
Угроза: `resendFailedChatMessage`, `loadChatList`, `loadFailedTasks` в ряде веток ограничиваются `console.warn` или тихим сбросом loading-state. Для оператора это выглядит как "кнопка нажалась и ничего не произошло". На проде это рождает повторные клики, дубли отправки и ручной хаос.
Как исправить:
```javascript
if (!ok) {
  const message = this.formatApiError(data.detail) || `Ошибка (${status})`;
  this.failedTasksError = message;
  await this.showUiAlert(message, 'Ошибка');
  return;
}
```
И аналогично для chat list / resend flows.

🟠 Критичность: Medium Frontend Статус `sending_to_iiko` не доведен до UI-модели
Где: app/static/js/admin-app.js:399, app/templates/admin.html:777
Угроза: backend уже использует промежуточный статус, но `statusConfig`, фильтры и части шаблона его не знают. Оператор видит пропавший или странно размеченный заказ, а значит повторно жмет действие, которое уже выполняется.
Как исправить:
```javascript
sending_to_iiko: {
  label: 'Отправляется в iiko',
  badge: 'bg-amber-100 text-amber-800',
  column: 'confirmed',
},
```
И добавить его в фильтры/легенду.

🟡 Критичность: Low Frontend Дублирование карточек заказа в шаблоне уже создает риск функционального расхождения
Где: app/templates/admin.html:823, app/templates/admin.html:999, app/templates/admin.html:1042
Угроза: kanban/table/mobile рендерят почти один и тот же order summary разными блоками. Любая правка по `iiko_last_error`, prepayment, клиентским полям или action-buttons очень легко попадет только в одну ветку. Итог: desktop и mobile дают оператору разную картину по одному и тому же заказу.
Как исправить:
```jinja2
{# app/templates/admin/_order_bits.html #}
{% macro order_status_bits(order_expr) %}
<div x-show="{{ order_expr }}.iiko_last_error" class="mt-2 rounded border border-red-300 bg-red-50 px-2 py-2">
  <p class="text-[10px] font-bold uppercase text-red-900">Ошибка iiko</p>
  <p class="text-xs text-red-900" x-text="{{ order_expr }}.iiko_last_error"></p>
</div>
<div class="font-mono text-xs text-gray-700" x-text="{{ order_expr }}.user_phone || '—'"></div>
{% endmacro %}
```
И переиспользовать макрос во всех представлениях.

🟡 Критичность: Low Frontend Две JS-функции делают один и тот же rebuild заказа
Где: app/static/js/admin-app.js:1261, app/static/js/admin-app.js:3596
Угроза: `submitOrderCompositionFromLines` и `submitOrderRebuildDraft` дублируют почти одинаковый сценарий. Любая правка валидации, expected_version или post-success sync очень быстро разойдется и даст разные результаты для двух кнопок одного workflow.
Как исправить:
```javascript
async submitOrderRebuild({ closeComposition = false } = {}) {
  ...
  if (updated) {
    this.selectedOrder = updated;
    this.initOrderRebuildFromSelected();
    this.initOrderCompositionLinesFromSelected();
    this.syncOrderPaymentFormFromSelected();
    if (closeComposition) this.orderCompositionOpen = false;
  }
}
```
Обе кнопки должны вызывать одну функцию с разным флагом.

# Лишние и сомнительные файлы

🔴 Критичность: Low Backend/Frontend Рабочие артефакты и временные файлы в корне проекта
Где: problems.md, возможные черновые миграции и незавершенные service-файлы
Угроза: такие файлы начинают жить как будто это production-документация или рабочий код, хотя по факту отражают промежуточное состояние аудита/рефакторинга. Это повышает риск, что следующая разработка будет опираться на устаревший документ или полусобранную миграцию.
Как исправить: оставить только те артефакты, которые реально входят в процесс разработки, остальное либо удалить, либо перенести в отдельную папку `docs/audits/` и назвать явно как временный отчет.
