# Project: RestoMind — Часть 4 (Go-Live: Real-Time Admin & Production DevOps)

## Статус проекта
Бизнес-логика, AI, интеграции (WhatsApp, iiko), БД и базовый Docker — полностью готовы. Имеется статичная SPA-админка на Alpine.js.
**Цель Части 4:** Подготовить систему к реальному пилоту в ресторане.
1. Оживить админку через WebSockets (Live-чаты и Канбан заказов), чтобы менеджер мог мгновенно реагировать и перехватывать диалоги.
2. Настроить Production-ready инфраструктуру с авто-SSL (HTTPS) для приема вебхуков от Meta.

## Строгие правила (Rules v4)
1. **Traefik over Nginx:** Для обратного проксирования и SSL используем **Traefik**. Он сам получает и обновляет сертификаты Let's Encrypt через Docker-лейблы.
2. **Redis Pub/Sub:** Для WebSockets используем Redis Pub/Sub, чтобы вебхук мог «пушнуть» событие в сокет-сервер.
3. **Alpine.js WebSocket reactivity:** Фронтенд не переписывается. Используем текущий стек (Alpine.js). Данные обновляются реактивно при получении сообщений из сокета.

---

## Пошаговый план разработки (Часть 4)

### Фаза 1: Бэкенд — WebSockets & Redis Pub/Sub
- [x] Добавить функции для публикации и подписки (`publish_event`, `subscribe_events`) через Redis Pub/Sub → `app/services/events.py`
- [x] Создать WebSocket эндпоинт `ws://.../api/admin/ws` → `app/api/admin.py`
- [x] Внедрить отправку событий (Event Publishing):
  - Новое сообщение от юзера → `publish_event('new_message', data)` → `webhooks.py`
  - Ответ от AI → `publish_event('new_message', data)` → `webhooks.py`
  - Изменение статуса заказа → `publish_event('order_updated', data)` → `webhooks.py` + `intent_router.py`
  - Запрос escalate → `publish_event('human_needed', data)` → `webhooks.py`
  - Смена состояния чата → `publish_event('state_changed', data)` → `admin.py`

### Фаза 2: Фронтенд — Live Admin Panel (Alpine.js)
- [x] Подключение к WebSocket при загрузке страницы + авто-реконнект с backoff
- [x] **Live-Чат:** двухпанельный интерфейс (список чатов + окно диалога), реактивные сообщения, автоскролл, поле ввода для оператора
- [x] **Алерты:** при `human_needed` — красная пульсирующая плашка поверх экрана + звуковой сигнал (Web Audio API), кнопка «Перехватить»
- [x] **Канбан заказов:** 3 колонки (DRAFT → CONFIRMED → SENT_TO_IIKO), карточки перемещаются при `order_updated`, переключение между Канбан/Таблица
- [x] Индикатор WebSocket-соединения в сайдбаре (зелёная/красная точка)
- [x] Бейдж непрочитанных сообщений на вкладке «Диалоги»

### Фаза 3: DevOps — Traefik, SSL & Docker Compose Prod
- [x] Создать `docker-compose.prod.yml` (Traefik v3.1 + FastAPI + PostgreSQL + Redis)
- [x] Настроить Traefik: порты 80/443, Let's Encrypt HTTP Challenge, HTTP→HTTPS редирект
- [x] Добавить лейблы к FastAPI для маршрутизации (WebSocket проксируется автоматически)
- [x] Создать `DEPLOY_GUIDE.md`: VPS, домен, A-запись, Docker, .env, запуск, WhatsApp webhook, бэкапы

### Фаза 4: WhatsApp WABA Switch (Реальный трафик)
- [x] Логика переключения `WHATSAPP_API_TOKEN` работает (auto-detect в `whatsapp.py`)
- [x] Инструкция настройки Webhook URL в кабинете Meta → `DEPLOY_GUIDE.md` (шаг 8)
- [x] curl-команда для тестирования webhook без Meta → `DEPLOY_GUIDE.md`
- [x] Маршрут `/admin` добавлен для удобства доступа

---

## Итог
Все 4 фазы Части 4 выполнены. Система готова к деплою на боевой сервер.
