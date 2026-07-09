# Messaging Gateway MVP

Цель: дать RestoMind независимый слой обмена сообщениями, чтобы AI-агент мог принимать заказы и отвечать гостям не только через официальный WhatsApp API, но и через WhatsApp Web/Baileys, а позже через Telegram, Instagram и web-chat.

Это не должен быть "обход Meta" как отдельный костыль. Это должен быть аккуратный транспортный слой, где каналы связи заменяемы, а вся ценность продукта остается внутри RestoMind: контекст ресторана, история диалога, меню, заказ, память, аналитика и AI.

## Архитектурное решение

Рабочее название: `Messaging Gateway`.

Не использовать название `WhatsApp Gateway` для основного слоя. WhatsApp через Baileys будет первым провайдером, но не центром архитектуры.

Целевая схема:

```text
Provider Adapter
  ↓
Channel Event
  ↓
Inbound Message Queue
  ↓
Conversation Service
  ↓
AI Agent / Order Engine / CRM / Memory
  ↓
Outbound Message Queue
  ↓
Provider Adapter
```

Главное разделение ответственности:

- `Provider Adapter` знает только внешний канал: WhatsApp Meta, Baileys, Telegram, web-chat.
- `Messaging Gateway` нормализует входящие и исходящие сообщения.
- `Conversation Service` знает ресторан, гостя, историю, активный заказ, состояние AI и канал общения.
- `AI Agent` получает готовый `ConversationContext`, а не ищет историю и данные по разным таблицам.

## Architecture Principles

Эти принципы важнее конкретного первого провайдера. Если будущая реализация спорит с ними, нужно либо изменить реализацию, либо явно обновить этот документ.

1. Provider agnostic: бизнес-логика не зависит от WhatsApp, Telegram, Baileys или Meta API.
2. Conversation first: центр системы - диалог, а не входящий webhook конкретного канала.
3. Durable messages: входящие и исходящие сообщения сначала сохраняются, потом обрабатываются.
4. Idempotent processing: повтор одного и того же provider event не должен создавать дубль сообщения, заказа или ответа.
5. At-least-once delivery: система допускает повторную обработку, но не допускает тихую потерю сообщения.
6. AI never depends on transport: AI получает `ConversationContext`, а не provider-specific payload.
7. Transport never depends on AI: gateway не знает про меню, заказы, промпты, handoff-логику и правила ресторана.
8. Observable by default: у каждого сообщения есть trace/correlation identifiers, статусы и диагностируемая причина ошибки.

## Non Goals

`Messaging Gateway` не должен становиться местом, где живет вся продуктовая логика.

Он не является:

- CRM;
- chat UI;
- analytics layer;
- AI agent;
- order engine;
- payment system;
- notification platform;
- customer memory;
- меню-движком ресторана.

Если новая функция требует знания меню, заказа, гостя, AI-состояния или бизнес-правил, она должна жить в `Conversation Service`, order pipeline или другом доменном сервисе, а не внутри gateway.

## Что не делаем в MVP

В MVP не строим отдельную платформу уровня Twilio, Kafka/RabbitMQ-инфраструктуру или "Communication OS" как самостоятельный продукт.

MVP должен заложить правильные границы, но реализовать только минимальный полезный путь:

```text
WhatsApp Web/Baileys
  ↓
Messaging Gateway contracts
  ↓
Conversation Service
  ↓
текущий AI/order pipeline
  ↓
ответ гостю
```

## MVP Scope

MVP считается готовым, когда один ресторан может подключить отдельный WhatsApp номер через QR, получать входящие сообщения, обрабатывать их текущим AI-агентом, принимать заказ и отправлять ответы обратно, при этом сообщения не теряются при временной ошибке AI или канала.

Обязательные возможности:

- единый контракт входящих и исходящих сообщений;
- `Conversation Service` как центр сборки контекста;
- сущность `ChannelConnection`;
- сущность `ChannelMessage`;
- DB-backed очередь через статусы сообщений;
- retry для временных ошибок;
- первый provider: `whatsapp_baileys`;
- QR-подключение и статус сессии в админке;
- диагностика доставки и ошибок;
- возможность позже добавить официальный Meta provider без переписывания AI/order pipeline.

## Current Implementation Notes

Фактическая MVP-реализация уже закрывает несколько пунктов, которые изначально выглядели как hardening:

1. **Двусторонняя zero-trust авторизация шлюзов.**
   FastAPI и Node.js gateway проверяют общий секрет через `X-RestoMind-Gateway-Secret`. Backend защищает inbound/status/poll endpoints, а gateway защищает admin send/reconnect commands. Переменные окружения должны быть синхронизированы: `MESSAGING_GATEWAY_SECRET` на FastAPI и `RESTOMIND_GATEWAY_SECRET` на Node.js gateway.

2. **Активное pull-восстановление сессий.**
   После старта Node.js gateway сам опрашивает `GET /api/channels/gateway/connections?provider=whatsapp_baileys` и поднимает известные `ChannelConnection`. Это важнее простого "прочитать локальную папку сессий", потому что Render/VPS/container могут перезапускать backend и gateway независимо.

3. **Strangler-фасад `ConversationService`.**
   Текущий `ConversationService` намеренно тонкий: он принимает normalized channel event и аккуратно ведет его в уже проверенный `process_inbound_message` / `chat_serializer.py`. Это сохраняет качество текущего AI/order pipeline и дает безопасную точку для будущего выноса `CustomerProvider`, `OrderProvider`, `MenuProvider` и других context providers.

Эти решения считаются частью MVP, а не отложенным улучшением.

## Основные сущности

### Conversation

Единая сущность диалога независимо от канала.

Минимальные поля:

```text
id
organization_id
customer_id / guest_id
active_order_id
status
last_message_at
created_at
updated_at
```

`Conversation` не должна быть привязана только к WhatsApp. Один и тот же гость в будущем может иметь несколько каналов, но текущий MVP может начать с одного активного канала на диалог.

Все операции чтения и записи `Conversation` на стороне FastAPI должны быть tenant-scoped по `organization_id`. Для PostgreSQL это закрепляется RLS-политиками по тому же принципу, что `20260609_tenant_rls`; для messaging-таблиц фактическая миграция MVP должна явно покрывать `conversations`, `channel_connections` и `channel_messages`.

### ChannelConnection

Подключение ресторана к конкретному каналу.

Минимальные поля:

```text
id
organization_id
provider
status
external_account_id
phone
display_name
session_ref
is_default_outbound
last_seen_at
last_error
created_at
updated_at
```

Возможные `provider`:

```text
whatsapp_baileys
whatsapp_meta
telegram
instagram
webchat
```

MVP реализует только `whatsapp_baileys`.

Возможные `status`:

```text
connected
qr_required
connecting
disconnected
expired
rate_limited
banned
error
disabled
```

`session_ref` указывает на место хранения сессии. Не стоит хранить крупные Baileys credentials напрямую в основной строке `ChannelConnection`, чтобы не усложнять бэкапы, миграции и перенос серверов.

`ChannelConnection` также находится под tenant isolation по `organization_id`. Gateway не получает произвольный список всех подключений без проверки `X-RestoMind-Gateway-Secret`; admin endpoints дополнительно фильтруются текущей организацией админ-сессии.

`is_default_outbound` определяет канал для новых системных исходящих сообщений без входящего channel context: recovery брошенных черновиков, автосбор отзывов, уведомления и будущие рассылки. Для одной организации должен быть только один default outbound channel. В PostgreSQL это фиксируется partial unique index по `organization_id`, где `is_default_outbound = true`.

### ChannelMessage

Нормализованная запись входящего или исходящего сообщения.

Минимальные поля:

```text
id
organization_id
conversation_id
channel_connection_id
trace_id
correlation_id
provider
direction
external_chat_id
external_message_id
idempotency_key
status
message_type
text
payload_json
error_code
error_message
attempt_count
next_attempt_at
created_at
processing_at
sent_at
delivered_at
read_at
failed_at
```

`direction`:

```text
in
out
```

`status`:

```text
received
pending
processing
processed
sent
delivered
read
retrying
failed
cancelled
```

Для входящих сообщений idempotency строится из провайдера, подключения и внешнего ID:

```text
{provider}:{channel_connection_id}:{external_message_id}
```

Для исходящих сообщений idempotency строится из внутреннего события/ответа:

```text
reply:{conversation_id}:{source_message_id}:{reply_version}
```

`ChannelMessage` хранит `organization_id` и должен быть защищен RLS/tenant-scope так же строго, как `Conversation` и `ChannelConnection`. Уникальность входящих событий фиксируется на уровне БД через provider/connection/external-message identity, чтобы повторный provider event не создавал дубль.

## Message Contract

Каждый event должен нести:

- `trace_id` - один технический путь обработки конкретного сообщения;
- `correlation_id` - цепочка связанных событий: входящее сообщение, AI-обработка, исходящий ответ, delivery ack;
- `idempotency_key` - защита от дублей при retry и повторных provider events.

Для входящих provider events `trace_id` создается на самой дальней границе, которая видит внешнее событие. В MVP для `whatsapp_baileys` это Node.js gateway: он получает событие из WhatsApp Web socket, присваивает `trace_id` и передает его в FastAPI в теле normalized event. Если provider не передал trace, FastAPI создает fallback `trace_id` перед сохранением `ChannelMessage`.

`trace_id` должен идти сквозь логи gateway, FastAPI, worker и outgoing dispatch. Для HTTP-взаимодействия допускается дублировать его в заголовке, но source of truth для normalized message event остается поле `trace_id` в контракте.

Внутренний входящий контракт:

```json
{
  "trace_id": "trace-uuid",
  "correlation_id": "conversation-or-message-chain-uuid",
  "idempotency_key": "whatsapp_baileys:connection-uuid:wamid-or-baileys-id",
  "provider": "whatsapp_baileys",
  "channel_connection_id": "uuid",
  "external_chat_id": "77001234567@s.whatsapp.net",
  "external_message_id": "wamid-or-baileys-id",
  "sender": {
    "external_id": "77001234567",
    "phone": "+77001234567",
    "display_name": "Гость"
  },
  "message": {
    "type": "text",
    "text": "Плов есть?",
    "payload": {},
    "metadata": {
      "quoted_message_id": null,
      "forwarded": false,
      "language": null
    }
  },
  "received_at": "2026-07-09T12:15:00Z"
}
```

Внутренний исходящий контракт:

```json
{
  "trace_id": "trace-uuid",
  "correlation_id": "conversation-or-message-chain-uuid",
  "idempotency_key": "reply:conversation:source:1",
  "provider": "whatsapp_baileys",
  "channel_connection_id": "uuid",
  "conversation_id": "uuid",
  "external_chat_id": "77001234567@s.whatsapp.net",
  "message": {
    "type": "text",
    "text": "Да, плов есть. Могу добавить одну порцию?",
    "payload": {},
    "metadata": {}
  }
}
```

## Conversation Service

MVP implementation note:

На первом этапе `Conversation Service` может быть тонким фасадом поверх текущего `process_inbound_message` и `chat_serializer.py`. Это осознанный Strangler Pattern: сначала все каналы проходят через единую точку входа, а затем внутренняя сборка `ConversationContext` постепенно выносится в context providers без большого переписывания рабочего AI/order pipeline.

В текущей реализации это уже зафиксировано как отдельный `ConversationService`, а не как логика внутри Baileys gateway. Gateway не знает меню, заказ, промпты и fallback-логику AI; он только доставляет normalized message event и принимает команды отправки.

`Conversation Service` должен быть единственной точкой, которая собирает контекст для AI и order pipeline.

Ответственность:

- найти или создать гостя;
- найти или создать `Conversation`;
- связать входящий `ChannelMessage` с диалогом;
- загрузить историю сообщений;
- загрузить активный черновик заказа;
- загрузить меню и доступность блюд;
- определить состояние human handoff;
- собрать `ConversationContext`;
- вызвать AI/order pipeline;
- создать исходящий `ChannelMessage`;
- поставить исходящее сообщение в очередь отправки.

`ConversationContext` не должен быть одной жесткой структурой, которую вручную расширяют при каждом новом сценарии. Он собирается из context providers.

Минимальный набор providers:

- `RestaurantProvider` - организация, филиал, часы работы, правила приема заказов;
- `CustomerProvider` - гость, телефон, имя, история заказов, предпочтения;
- `HistoryProvider` - последние сообщения и важные состояния диалога;
- `OrderProvider` - активный черновик заказа, состав, доставка, оплата;
- `MenuProvider` - доступные блюда, цены, категории, стоп-лист;
- `MemoryProvider` - устойчивые факты о ресторане и госте;
- `HandoffProvider` - операторский режим, причина handoff, кто ведет диалог;
- `ChannelProvider` - текущий канал, provider, connection health, ограничения канала.

Пример итогового `ConversationContext` для AI:

```json
{
  "organization_id": 1,
  "conversation_id": "uuid",
  "customer": {
    "phone": "+77001234567",
    "name": "Гость"
  },
  "channel": {
    "provider": "whatsapp_baileys",
    "connection_id": "uuid"
  },
  "history": [],
  "active_order": {},
  "menu_context": [],
  "handoff_state": null
}
```

Это пример формы, а не окончательная схема на годы. Новые блоки контекста добавляются через providers, чтобы AI не зависел от таблиц, webhook payload и provider-specific деталей. Для AI это всегда один и тот же диалоговый контекст.

## Outbound Channel Resolution

Для ответа внутри активного диалога приоритет задает входящий channel context:

```text
inbound ChannelMessage
  -> channel_connection_id + external_chat_id
  -> send reply through the same provider connection
```

Для новых системных исходящих сообщений входящего контекста может не быть. В этом случае MVP использует explicit default outbound channel:

1. Если текущая обработка имеет channel context, ответ идет через этот канал.
2. Если context нет, но у организации есть `ChannelConnection.is_default_outbound = true` и канал `connected`, сообщение идет через него.
3. Если default channel не найден или недоступен, система сохраняет обратную совместимость и использует старый Meta/WhatsApp fallback, если он настроен.

Admin UI должен позволять владельцу выбрать default channel кнопкой "Для новых рассылок". Это закрывает сценарий, где обычные диалоги уже идут через QR/Baileys, а проактивные сообщения могли бы случайно уйти через старый `WHATSAPP_*` Meta ENV.

Текущее MVP-поведение:

- `whatsapp_baileys` default используется для proactive outbound, когда есть `outbound_chat_log_id` и из него можно определить `organization_id`;
- external chat id для WhatsApp Web строится из номера гостя как `{digits}@s.whatsapp.net`;
- если default Baileys не задан или не connected, поведение откатывается к прежнему `send_message(phone, text)` через Meta integration.

## Health Layer

Статус `connected` у WhatsApp не означает, что вся цепочка работает. Health должен быть отдельным слоем, который показывает состояние каждого звена.

Минимальные health domains:

- provider health: Baileys/Meta/Telegram адаптер жив и может принимать events;
- connection health: конкретный ресторанный аккаунт подключен, не требует QR, не заблокирован;
- queue health: входящие и исходящие сообщения обрабатываются, нет растущего backlog;
- AI health: AI provider отвечает, retry не растут, soft fallback не доминирует;
- internal API health: gateway может достучаться до RestoMind, подписи и tenant routing проходят;
- delivery health: исходящие сообщения реально отправляются, а не только создаются в БД.

MVP должен показывать в админке хотя бы агрегированный статус:

```text
works
needs_reconnect
degraded
blocked
failed
```

Для диагностики внутри логов и БД должны сохраняться конкретные причины: `qr_required`, `provider_disconnected`, `ai_unavailable`, `queue_backlog`, `send_failed`, `rate_limited`.

## Queue And Retry

На MVP не нужен отдельный брокер. Достаточно DB-backed очереди:

```text
ChannelMessage.status = pending
next_attempt_at <= now()
attempt_count < max_attempts
```

Входящий путь:

```text
receive from provider
  ↓
persist ChannelMessage(direction=in, status=received)
  ↓
mark processing
  ↓
Conversation Service
  ↓
create ChannelMessage(direction=out, status=pending)
```

Исходящий путь:

```text
select pending outbound messages
  ↓
send via provider adapter
  ↓
mark sent / retrying / failed
  ↓
update delivery status when provider sends ack
```

Retry нужен для:

- временной ошибки AI;
- временной ошибки отправки;
- потери соединения с WhatsApp Web;
- рестарта сервера во время обработки;
- rate limit провайдера.

Если сообщение осталось в `processing` после падения процесса, worker должен считать его зависшим и вернуть в обработку после короткого lease окна. MVP-ориентир: `processing_at` старше 5 минут снова считается due. Проверка выполняется планировщиком ARQ worker для входящих и исходящих сообщений: due query выбирает `received/pending/retrying`, а также stale `processing`, где `processing_at <= now() - 5 minutes`.

Lease-правило:

- при claim сообщения worker атомарно переводит его в `processing` и ставит `processing_at = now()`;
- успешная обработка переводит сообщение в terminal/next status: `processed`, `sent`, `delivered`, `failed`;
- если процесс умер и terminal status не поставлен, следующий ARQ tick видит stale `processing` по `processing_at` и возвращает сообщение в обработку;
- retry должен быть idempotent: повтор обработки не должен создать второй заказ или второй одинаковый ответ.

Не retry-ить бесконечно. MVP-лимит:

```text
max_attempts = 3-5
backoff = 10s, 30s, 2m, 5m
```

После исчерпания попыток сообщение получает `failed`, а в админке появляется событие для оператора.

## WhatsApp Baileys Provider

Первый provider: `whatsapp_baileys`.

Минимальные обязанности:

- создать сессию для `ChannelConnection`;
- показать QR при `qr_required`;
- сохранить credentials в хранилище по `session_ref`;
- восстановить сессию после рестарта;
- при старте выполнить pull известных подключений через `GET /api/channels/gateway/connections?provider=whatsapp_baileys`;
- слушать входящие сообщения;
- нормализовать их в Message Contract;
- отправлять исходящие сообщения;
- обновлять статусы подключения;
- передавать delivery/read события, если доступны.

Основные internal endpoints MVP:

```text
POST /api/channels/inbound
POST /api/channels/gateway/connections/status
GET  /api/channels/gateway/connections
POST /api/channels/gateway/messages/status
GET  /api/channels/gateway/outbound/pending
POST /api/channels/gateway/outbound/{channel_message_id}/dispatch
```

Все gateway endpoints защищаются `X-RestoMind-Gateway-Secret`. Admin endpoints управления подключениями живут отдельно:

```text
GET  /api/admin/channel-connections
GET  /api/admin/channel-connections/health
POST /api/admin/channel-connections
POST /api/admin/channel-connections/{id}/reconnect
POST /api/admin/channel-connections/{id}/disable
```

Рекомендуемый формат проекта:

```text
services/messaging-gateway/
  src/
    providers/
      whatsapp_baileys/
    contracts/
    storage/
    workers/
```

Для MVP можно начать как отдельный Node.js сервис, потому что Baileys живет в Node.js-экосистеме. RestoMind backend остается Python/FastAPI и получает нормализованные events через internal API.

## Admin UI MVP

В админке ресторана нужен простой экран подключения канала.

MVP UI:

- список подключений;
- provider;
- номер телефона;
- статус;
- время последней активности;
- последняя ошибка;
- кнопка "Подключить WhatsApp";
- QR-код для сканирования;
- кнопка "Переподключить";
- кнопка "Отключить";
- журнал последних входящих/исходящих сообщений с их статусами.

Тексты должны быть бизнесовыми, без лишней технической терминологии:

- "WhatsApp подключен";
- "Нужно обновить подключение";
- "Сообщения временно не отправляются";
- "Последняя ошибка";
- "Повторить подключение".

## MVP Implementation Plan

### Phase 1. Contracts And Data Model

Результат: в RestoMind есть каноническая модель каналов и сообщений.

Задачи:

- описать Python-схемы входящего и исходящего channel event;
- добавить модели `Conversation`, `ChannelConnection`, `ChannelMessage` или адаптировать существующие сущности, если они уже частично есть;
- добавить миграции;
- добавить idempotency constraints для входящих сообщений;
- добавить базовые unit-тесты контрактов и idempotency.

Критерий готовности:

- одно и то же входящее сообщение с тем же external ID не создает дубль;
- исходящее сообщение можно сохранить без немедленной отправки;
- provider-специфичный payload не протекает в AI/order pipeline.

### Phase 2. Conversation Service

Результат: текущая WhatsApp-логика постепенно переносится в общий conversation pipeline.

Задачи:

- создать сервис `ConversationService`;
- реализовать find-or-create conversation по organization, provider и external chat;
- собрать `ConversationContext`;
- подключить текущий AI/order pipeline через этот context;
- оставить текущий Meta webhook рабочим, но направить его через общий сервис;
- покрыть сценарии меню, рекомендаций, черновика заказа и human handoff тестами.

Критерий готовности:

- текущий официальный WhatsApp webhook продолжает работать;
- меню/заказные вопросы идут через единый conversation path;
- AI не зависит от конкретного provider.

### Phase 3. DB-backed Queue

Результат: обработка и отправка сообщений становятся надежнее.

Задачи:

- добавить worker для входящих сообщений, если сообщение еще не обработано;
- добавить worker для исходящих сообщений;
- реализовать retry/backoff;
- записывать `attempt_count`, `next_attempt_at`, `error_code`, `error_message`;
- ставить `processing_at` при claim и переоткрывать stale `processing` сообщения через ARQ worker lease check;
- добавить failover-поведение: после нескольких ошибок не терять сообщение, а показывать проблему оператору.

Критерий готовности:

- временная ошибка AI не теряет входящее сообщение;
- временная ошибка отправки не теряет ответ;
- после рестарта pending messages продолжают обрабатываться.
- stale `processing` сообщения старше lease окна снова попадают в due query и не зависают навсегда.

### Phase 4. Baileys Messaging Gateway

Результат: первый неофициальный WhatsApp Web provider работает как транспорт.

Задачи:

- создать Node.js сервис для Baileys;
- реализовать QR lifecycle;
- сохранять session credentials по `session_ref`;
- реализовать pull-восстановление сессий из `GET /api/channels/gateway/connections`;
- добавить shared-secret авторизацию `X-RestoMind-Gateway-Secret` в обе стороны;
- принимать входящие сообщения;
- отправлять их в RestoMind internal inbound endpoint;
- принимать outbound send commands от RestoMind;
- отправлять сообщения через Baileys;
- обновлять status/health подключения.

Критерий готовности:

- ресторан один раз сканирует QR;
- после рестарта сервиса сессия восстанавливается;
- после рестарта gateway сам поднимает известные подключения из FastAPI, а не ждет ручного действия админа;
- входящее сообщение появляется в RestoMind;
- AI отвечает гостю через Baileys;
- отключение сессии видно в админке.

### Phase 5. Admin Connection UI

Результат: владелец или оператор может подключить WhatsApp без ручного доступа к серверу.

Задачи:

- экран подключений;
- QR modal;
- статус подключения;
- переподключение;
- отключение;
- последние ошибки;
- последние сообщения и статусы доставки.

Критерий готовности:

- можно подключить новый WhatsApp номер из админки;
- можно понять, почему канал не работает;
- оператор видит, что сообщение не отправилось и требует внимания.

### Phase 6. Production Hardening

Результат: MVP можно давать первым клиентам.

Задачи:

- rate limit на internal endpoints;
- подпись запросов между gateway и RestoMind через `X-RestoMind-Gateway-Secret` (в текущем MVP уже реализовано как обязательная защита, а не post-MVP опция);
- tenant isolation по `organization_id`;
- PostgreSQL RLS для `conversations`, `channel_connections` и `channel_messages` по `organization_id`, по тому же принципу, что `20260609_tenant_rls`; фактическая messaging-миграция MVP должна быть отдельной и проверяемой, например `20260709_messaging_gateway_rls`;
- structured logs с `trace_id`, `conversation_id`, `channel_message_id`;
- alert при `disconnected`, `expired`, `banned`, росте `failed`;
- runbook для переподключения WhatsApp;
- backup/restore для session storage.

Критерий готовности:

- сбой канала диагностируется без SSH;
- сессии можно перенести/восстановить;
- внутренний API не принимает неподписанные events;
- есть понятная инструкция поддержки.

## MVP Acceptance Scenarios

### New WhatsApp Connection

```text
админ открывает подключения
  ↓
нажимает "Подключить WhatsApp"
  ↓
видит QR
  ↓
сканирует телефоном
  ↓
статус становится connected
```

### Guest Order Flow

```text
гость пишет "Здравствуйте, что посоветуешь?"
  ↓
Baileys получает сообщение
  ↓
RestoMind создает ChannelMessage
  ↓
Conversation Service собирает context
  ↓
AI отвечает по меню
  ↓
исходящее сообщение отправляется гостю
```

### Temporary AI Failure

```text
входящее сообщение сохранено
  ↓
AI временно падает
  ↓
сообщение получает retrying
  ↓
worker повторяет обработку
  ↓
ответ отправляется
```

### Disconnected WhatsApp

```text
исходящий ответ создан
  ↓
Baileys connection disconnected
  ↓
сообщение остается pending/retrying
  ↓
админ видит "Нужно обновить подключение"
  ↓
после восстановления сообщение отправляется
```

## Risks

### WhatsApp Web Is Unofficial

Baileys использует WhatsApp Web-протокол. Это практично для MVP, но не гарантированная долгосрочная инфраструктура.

Риск снижается тем, что Baileys изолирован как provider. При появлении официального Meta API меняется provider, а не conversation/order/AI слой.

### Session Expiration

Сессия может слететь из-за действий пользователя, изменений Meta или долгой неактивности.

Риск снижается через:

- `ChannelConnection.status`;
- QR reconnect flow;
- alert в админке;
- сохранение session credentials;
- runbook поддержки.

### Duplicate Messages

Провайдер или retry может повторно прислать один и тот же event.

Риск снижается через:

- `external_message_id`;
- `idempotency_key`;
- уникальные constraints;
- idempotent processing.

### Provider Leakage

Если детали Baileys попадут в AI/order pipeline, потом будет трудно добавить Meta, Telegram или web-chat.

Риск снижается через:

- нормализованный Message Contract;
- `ConversationContext`;
- запрет provider-specific логики внутри AI/order pipeline.

## Post-MVP Roadmap

Этот раздел фиксирует направления развития, ради которых MVP сразу проектируется provider-agnostic. Детальная дорожная карта должна жить отдельно, чтобы этот RFC не превращался в общий product roadmap.

### More Providers

Добавить новые provider adapters без переписывания `Conversation Service` и AI/order pipeline:

- `whatsapp_meta` после прохождения Meta verification;
- `telegram` для ресторанов, которым удобен Telegram;
- `webchat` как контролируемый канал на сайте ресторана;
- `instagram` после стабилизации Meta-интеграций.

### Rich Message Types

Расширить `message_type`, сохранив тот же контракт:

```text
text
image
audio
document
location
button_reply
list_reply
order_summary
payment_link
```

MVP начинает с `text`, но `payload` и `metadata` должны позволять добавить quoted messages, attachments, buttons, location, language, forwarded flags и provider-specific details без изменения базового контракта.

### Reliability And Delivery

Усилить надежность:

- полная цепочка delivery tracking: `created`, `queued`, `sent`, `delivered`, `read`, `failed`;
- dedicated broker, если DB-backed очередь станет узким местом;
- dashboard качества канала: ошибки, retry, latency, downtime;
- alert владельцу при `qr_required`, `disconnected`, `rate_limited`, росте `failed`.

### Customer And Operator Experience

Развить слой диалогов:

- multi-channel customer identity: один customer profile, несколько channel identities;
- human handoff inbox с причиной handoff, последними AI-действиями и быстрым возвратом в autopilot;
- внутренние заметки по гостю;
- аналитика стоимости и надежности каналов, чтобы понимать, когда ресторану стоит перейти с Baileys на официальный Meta provider.

## Final Direction

Правильная стратегия:

```text
не строить WhatsApp-костыль
не строить Communication OS целиком
построить минимальный Messaging Gateway с правильными границами
```

MVP должен дать быстрый путь к первым клиентам через WhatsApp Web/Baileys, но архитектура должна сразу защищать продукт от зависимости от одного провайдера.

Ценность RestoMind находится не в канале доставки сообщений, а в AI, заказах, контексте ресторана, памяти и операционной аналитике. Канал должен быть заменяемым.
