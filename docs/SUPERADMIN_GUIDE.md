# Super Admin (для владельца платформы) — как работает и куда улучшать

Документ описывает **текущее фактическое поведение** Super Admin в RestoMind и даёт список улучшений (UI/UX + функционал), которые имеют смысл именно для роли владельца платформы.

## Быстрый вход (как попасть в Super Admin)

### 1) Доступ

Super Admin — это **staff-пользователь** с флагом `is_superadmin=true`.

- **Проверка доступа**: API `GET /api/superadmin/me` (внутри используется `require_superadmin`).
- **Требование**: быть залогиненным в админку (cookie-сессия) и иметь `is_superadmin`.

### 2) Как войти

- Сначала вход через обычную админку: `GET /admin` → `POST /api/admin/auth/login`.
- Затем открываешь: `GET /superadmin` (UI) — сама страница доступна всем, но **данные подтягиваются через API** и при отсутствии прав будет ошибка в UI.

### 3) Как выдать себе Super Admin (локально/в окружении)

В репозитории есть скрипт:

- `scripts/grant_superadmin.py`

Он предназначен для выдачи роли владельца (флаг `is_superadmin`) существующему staff-юзеру.

## Что есть в Super Admin сейчас (факт по коду)

UI: `app/templates/superadmin.html`  
API: `app/api/superadmin.py`  

Страница сейчас состоит из 4 крупных блоков:

1) **Аудит платёжных webhook** (read-only)
2) **Заявки на подключение** (модерация)
3) **Создать ресторан вручную** (org + admin staff)
4) **Рестораны** (список + базовое управление доступом и технастройкой)

## 1) Регистрация ресторана через заявку (как работает)

### Каналы создания заявки

В коде заявка — это запись `RegistrationRequest` со статусом `pending`.

- **Публичное API создания заявки**: `POST /api/superadmin/registration-requests`
  - поля: `restaurant_name`, `contact_name`, `phone`, `email`, `has_iiko`, `note`
  - статус создаётся как `pending`
  - best-effort отправляется уведомление в Telegram (если настроено):
    - `SUPERADMIN_TELEGRAM_CHAT_ID` (или fallback на `TELEGRAM_ADMIN_CHAT_ID`)

Публичная форма в продукте доступна по `GET /request-access`. Она должна в итоге приводить к созданию такой заявки (через соответствующий эндпоинт в админ-auth, если он используется, или напрямую через `/api/superadmin/registration-requests`).

### Модерация заявки в Super Admin UI

В UI показываются **только pending-заявки**:

- `GET /api/superadmin/registration-requests?status=pending`

Кнопки:

- **Одобрить** → `POST /api/superadmin/registration-requests/{id}/approve`
  - создаёт (опционально) `Tenant` (если передали `tenant_name`)
  - создаёт `Organization`
  - создаёт staff-юзера с `role=admin`
  - если пароль не задан — генерируется и возвращается как `generated_password`
  - заявка переводится в `approved`, проставляется `decided_at` и `decided_by_staff_id`

- **Отклонить** → `POST /api/superadmin/registration-requests/{id}/reject`
  - переводит заявку в `rejected`, проставляет `decided_at` и `decided_by_staff_id`

### Что важно помнить (операционно)

- Сгенерированный пароль показывается **один раз** в UI через модальное окно с copy-кнопкой и обязательным подтверждением «Я сохранил пароль».
- После approve/reject заявка больше не `pending` и повторная обработка вернёт `409`.

## 2) Рестораны: управление оргами (как работает)

### Список организаций + KPI за 30 дней

- `GET /api/superadmin/organizations`

Возвращает по каждой организации:

- базовые поля: `id`, `name`, `slug`, `created_at`, `tenant_id`, `tenant_name`, `tenant_plan`
- доступ: `is_active`, `is_demo`
- KPI: `orders_30d`, `revenue_30d`, `staff_count`
- интеграции: `has_iiko`, `has_whatsapp`, а также `iiko_organization_id`, `whatsapp_phone_number_id`, `telegram_ops_chat_id`
- расписание: `schedule_json` (нормализованное), `operational_label`, `is_business_open`, `is_kitchen_open`

### Блокировка / разблокировка ресторана

Кнопка “Активен/Заблокирован” вызывает:

- `PATCH /api/superadmin/organizations/{organization_id}/status` → `{ is_active: bool }`

### Технастройки (прямо в таблице)

Сейчас в UI прямо в строке организации редактируются:

- `iiko_organization_id`
- `whatsapp_phone_number_id`

Через:

- `PATCH /api/superadmin/organizations/{organization_id}/credentials`

**Замечание:** в API поддерживаются также:

- `iiko_api_login` (с шифрованием в `iiko_api_login_enc`, если задан ключ Fernet)
- `iiko_terminal_group_id`
- `telegram_ops_chat_id`

Но UI сейчас не показывает поля для `iiko_api_login`, `iiko_terminal_group_id`, `telegram_ops_chat_id`.

### График работы (редактор расписания)

UI открывает модалку “График” и сохраняет:

- `PATCH /api/superadmin/organizations/{organization_id}/schedule` → `{ schedule_json: {...} }`

Есть UX-логика:

- fallback-график, если расписание неполное/битое
- валидация: “приём заказов до” не может быть позже “заведение работает до”
- кнопка “Применить ко всем дням”

### Force Sync меню iiko

Кнопка “Force Sync” вызывает:

- `POST /api/superadmin/organizations/{organization_id}/sync-menu`

Условия:

- креды iiko должны быть заполнены (через механизм резолва в `resolve_org_iiko_credentials`)
  - иначе `400`

### Сброс пароля staff-админа

Кнопка “Сброс пароля”:

1) грузит staff:
   - `GET /api/superadmin/organizations/{organization_id}/staff`
2) выбирает `role=admin` (если нет — первый попавшийся)
3) сбрасывает:
   - `POST /api/superadmin/organizations/{org_id}/staff/{staff_id}/reset-password`
   - пароль генерируется, если не передали свой

## 3) Создание ресторана вручную (как работает)

Форма “Создать ресторан вручную”:

- `POST /api/superadmin/organizations`

Поведение:

- создаёт `Tenant`, если указан `tenant_name`
- создаёт `Organization`
- создаёт staff admin (`role=admin`)
- если пароль не задан — возвращает `generated_password`

## 4) Аудит платёжных webhook (как пользоваться)

Блок “Аудит платёжных webhook” — read-only просмотр входящих webhook-запросов.

Список:

- `GET /api/superadmin/payment-webhook-events?provider=&applied=&limit=&offset=`

Деталь:

- `GET /api/superadmin/payment-webhook-events/{id}`

Скачивание сырого body:

- `GET /api/superadmin/payment-webhook-events/{id}/payload.bin` (`application/octet-stream`)

В UI есть:

- фильтр по `provider`
- фильтр `applied`
- признак `verified` / `applied` / `duplicate`
- просмотр headers и payload (text/base64)

## Типовые сценарии “как владелец”

### Сценарий: пришёл лид (заявка)

1) Открыть `GET /superadmin`
2) Блок “Заявки на подключение”
3) Одобрить → сохранить временный пароль → передать ответственному (или сразу войти/поменять)
4) Перейти в “Рестораны” → проверить `is_active=true`, интеграции, расписание

### Сценарий: ресторан “не платит” / “не обслуживаем”

1) Найти ресторан в таблице
2) Нажать “Заблокировать”
3) (Опционально) оставить запись/причину — сейчас в UI/БД **нет**, это кандидат на улучшение

### Сценарий: “у ресторана не работает меню / iiko”

1) Проверить `has_iiko`, заполненность `iiko_organization_id`
2) При необходимости добавить/исправить iiko креды (сейчас часть полей не выведена в UI)
3) Нажать “Force Sync”
4) Открыть админку ресторана и проверить ошибки интеграций/стоп-листов

### Сценарий: разбор проблем оплаты (webhook)

1) Фильтр `provider` и/или `applied=false`
2) Открыть событие → посмотреть `verify_error`, headers, payload
3) Скачать `payload.bin` для разбора/реплея

## Что можно улучшить (предложения)

Ниже — идеи, которые реально усилят панель именно для владельца платформы: меньше ручной рутины, меньше ошибок, быстрее диагностика.

### A) UI/UX: навигация и “операционная” плотность

- **Сайдбар/табы внутри Super Admin**: сейчас 4 больших блока в одной длинной странице. Лучше сделать верхний segmented или sidebar:
  - “Заявки”
  - “Рестораны”
  - “Платежи (webhook audit)”
  - “Служебное”
- **Поиск по ресторанам**: строка поиска `q` (по `name`, `slug`, `tenant`, `org_id`, `whatsapp_phone_number_id`).
- **Сортировка колонок**: по `created_at`, `revenue_30d`, `orders_30d`, `is_active`, “нет WhatsApp”, “нет iiko”.
- **Пагинация**: таблицы сейчас без paging (кроме webhook events limit/offset). Для orgs/requests это станет проблемой.

### B) UX “не потерять пароль” (критичный момент)

`generated_password` и `new_password` показываются через обязательную модалку:

- пароль не попадает в toast
- есть кнопка “Скопировать”
- закрытие заблокировано до чекбокса “Я сохранил пароль”

Следующий безопасный контур: серверный одноразовый “секрет” или “reset link” вместо показа пароля.

### C) Управление интеграциями как “чеклист готовности”

В строке org уже есть сигналы `has_iiko` / `has_whatsapp` и расписание.

- **Добавить**: компактный чеклист readiness (чтобы сразу видеть “почему не работает”):
  - WhatsApp OK / not configured
  - iiko OK / missing apiLogin / missing org_id / missing terminal group
  - меню импортировано (N items > 0)
  - стоп-лист свежий (last sync OK)
- **Добавить**: кнопку “Открыть админку этого ресторана” (deep-link) — чтобы одним кликом прыгать в `/admin` и выбирать org (если ты tenant-owner/superadmin).

### D) Секреты и безопасность (минимум)

- **Маскирование** чувствительных полей в UI (например `iiko_api_login`):
  - показывать как `••••••` и раскрывать только в режиме “Edit”
  - отдельно кнопка “Сбросить”/“Заменить”
- **Явный аудит действий superadmin**:
  - кто заблокировал org
  - кто сбросил пароль
  - кто менял креды/расписание
  - кто делал force sync

### E) Регистрация/заявки: процесс и коммуникация

Сейчас “reject” не хранит причину (в API есть поле `reason`, но оно игнорируется в реализации reject).

- **Добавить**: хранить `decision_reason` (в БД) и показывать в истории заявки.
- **Добавить**: вкладки `pending/approved/rejected` в UI.
- **Добавить**: “назначить ответственного” (staff_id) или хотя бы текстовое поле “кто ведёт лид”.
- **Добавить**: быстрые шаблоны ответов (что нужно прислать для подключения WhatsApp/iiko).

### F) Платежи: анализ и поиск

Сейчас можно фильтровать только по `provider` и `applied`.

- **Добавить**:
  - фильтр по `organization_id`, `order_id`, `external_payment_id`
  - фильтр “signature failed”
  - быстрый “скопировать payload.json” (если текстовый) и “скачать bin”
  - “связанные сущности”: ссылка на заказ в админке (если `order_id` есть)

### G) Стабильность UI-кода Super Admin

Сейчас JS-логика `superAdminPage()` живёт inline в `superadmin.html`.

- **Улучшение**: вынести в `app/static/js/superadmin-app.js` (как минимум для тестируемости и уменьшения размера шаблона).
- **Улучшение**: переиспользовать дизайн-систему `ds-*` (по возможности), чтобы не плодить “вторую админку”.

## Что улучшать в первую очередь (моя рекомендация)

1) **Модалка с паролем + copy** после approve/create (самая частая боль и риск).
2) **Поиск + фильтры по ресторанам** (без этого платформа не масштабируется).
3) **Технастройки iiko/WhatsApp как чеклист** + “Открыть админку ресторана”.
4) **История заявок + причины** (потому что sales/process).
5) **Платежный аудит**: фильтры по org/order/ext_id + ссылки на заказ.

