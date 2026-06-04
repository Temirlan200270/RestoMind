# WhatsApp Retry Idempotency

## Ошибка

В диалоге у клиента одно входящее WhatsApp-сообщение может отображаться несколько раз, например:

```text
Самовывоз
Самовывоз
Самовывоз
```

При этом в логах Meta/WhatsApp у сообщения один `wamid`. Значит, дубли пришли не от клиента и не от WhatsApp, а были созданы внутри нашего backend-процесса.

Типичный сценарий:

1. `_process_message_inner` рано сохраняет входящее сообщение в `ChatLog`.
2. Дальше обработчик заказа падает с исключением.
3. `process_with_retry` запускает обработку того же `wamid` повторно.
4. Повторная попытка снова сохраняет тот же входящий текст и публикует `new_message`.
5. UI показывает один клиентский текст несколько раз.

Реальный пример корневого исключения:

```text
sqlalchemy.exc.InvalidRequestError:
Instance '<Order ...>' is not persistent within this Session
```

Это произошло потому, что в `route_intent/_handle_order` был передан `draft_order`, загруженный в другой SQLAlchemy-сессии. Затем код пытался сделать `db.refresh(existing_draft)` уже в текущей сессии.

## Что нельзя делать

Не считать retry безопасным, если в начале обработки уже есть side effects:

- запись входящего сообщения в `ChatLog`;
- публикация события в WebSocket/UI;
- обновление черновика заказа;
- отправка ответа клиенту.

Не передавать ORM-объект из одной `AsyncSession` в код, который будет обновлять или `refresh`-ить его в другой `AsyncSession`.

Не чинить дубли отключением retry. Retry нужен для временных ошибок, но все операции вокруг него должны быть идемпотентными.

## Правильное решение

### 1. Идемпотентность входящего сообщения

Для WhatsApp использовать `whatsapp_message_id` / `wamid` как idempotency key.

Перед созданием `ChatLog(role="user")` проверить, есть ли уже запись с тем же:

- `organization_id`;
- `user_id`;
- `role="user"`;
- `provider_message_id == wamid`.

Если запись уже есть, вернуть её `id` и не публиковать повторный `publish_chat_event`.

Текущая реализация: `app/api/webhooks.py::_save_inbound_chat_log`.

### 2. Не работать с detached ORM-черновиком

Если в `_handle_order` передали `draft_order`, перед обновлением надо заново получить persistent-экземпляр в текущей сессии:

```python
existing_draft = await db.get(Order, int(draft_order.id))
```

Если по `id` ничего не найдено, использовать обычный поиск активного черновика через `get_open_draft_order`.

Текущая реализация: `app/services/intent_router.py::_handle_order`.

### 3. Покрыть тестами

Обязательные регрессионные проверки:

- повторный `_save_inbound_chat_log` с тем же `wamid` создаёт только одну запись;
- `_handle_order` принимает detached `draft_order` и корректно обновляет активный черновик;
- fulfillment-only ответы вроде `Самовывоз`, `наличными`, `картой`, `через полчаса` не падают при активном черновике.

Текущие тесты:

- `tests/test_webhook_inbound_idempotency.py`;
- `tests/test_fulfillment_only_order.py`.

## Как диагностировать

Если в UI снова появились дубли:

1. Проверить `wamid` в логах webhook.
2. Если `wamid` один, искать exception после раннего сохранения входящего `ChatLog`.
3. Проверить, сколько `chat_logs` создано с этим `provider_message_id`.
4. Если дублей несколько, значит нарушена идемпотентность входящего сообщения.
5. Если дублей нет, но UI всё равно показывает повторы, искать повторную публикацию websocket-события.

## Быстрая проверка

```bash
pytest tests/test_webhook_inbound_idempotency.py tests/test_fulfillment_only_order.py -q
```

