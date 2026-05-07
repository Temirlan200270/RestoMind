# RestoMind — Roadmap & Single Source of Truth

Единственный файл для отслеживания статусов задач, багов и технического долга.

**Правило для ИИ:** при завершении задачи ставить галочку `[x]` здесь и делать запись в `CHANGELOG.md`. Другие “планы/трекеры” больше не обновляем.

Временные мини‑родмапы/чеклисты на 1–2 недели — в `docs/sprints/` (но статусы задач всё равно только здесь).

## 🔴 P0: Критический техдолг и баги (делать сейчас)

> Перенесено из бывшего `problems.md` (2026‑05): потенциальные data leaks, потеря/дубли сообщений, рассинхроны state и гонки UI.

- [x] **test_bot: LLM вне DB‑сессии:** чтение → LLM (без сессии) → запись; DB‑соединение не держим во время ответа модели (`app/api/admin/test_bot.py`).
- [x] **Telegram fire‑and‑forget:** realtime путь не ждёт Telegram-уведомления (`app/services/events.py`: `asyncio.create_task(...)` вместо `await`).
- [x] **Экстренное закрытие (полный стек):** `Organization.force_closed_until/force_closed_reason` + миграция + учёт в `time_context.py` + `POST /api/admin/organization/force-close` + UI (профиль, модалка, причина).
- [x] **Счётчик токенов (полный стек):** `AiUsageLog` + upsert индекс `(organization_id, day)`; `_usage` в `AIBrainResponse`; заполнение в `openai_p.py`; fire-and-forget upsert; UI «Токены сегодня».
- [x] **Dashboard mobile: статус работы на всех экранах:** 🟢/🟡/⚫️ + красный бейдж «⛔ Временно закрыто» при force-close (клик ведёт в настройки).

- [ ] **Data leak меню между организациями:** `load_available_menu()` должен фильтровать по `organization_id` (и call-sites обязаны передавать org). См. `problems.md` (старое): `app/services/order_logic.py:197`, `app/api/webhooks.py`, `app/api/admin/_monolith.py`, `app/services/intent_router.py`.
- [ ] **WhatsApp inbound dedupe durable handoff:** не ставить Redis-preclaim раньше durable записи в БД; фиксировать `done/failed` статус в БД (идемпотентность не должна “терять” сообщение при падении процесса). См. `app/api/webhooks.py`, `app/services/whatsapp_idempotency.py`.
- [ ] **OpenAI timeout masking → retry:** таймауты/RateLimit должны приводить к `TransientAiError` (или эквиваленту), чтобы очередь/вебхук делал retry, а не “успешно” эскалировал. См. `app/services/ai_brain.py`.
- [ ] **Source of Truth для dialog state:** durable state пишем в БД в основной транзакции, Redis обновляем **после commit** (cache-only). Убрать best-effort `_db_sync()` в отдельной сессии. См. `app/services/dialog_mgr.py`, `app/api/webhooks.py`.
- [ ] **Operator outbound: отправка наружу только после фиксации ChatLog:** сначала записать `ChatLog(delivery_status='sending')` + commit, потом отправить в WhatsApp, потом обновить `provider_message_id`/статус. См. `app/api/admin/_monolith.py` (старое).
- [ ] **UI: race-condition в заказах (REST vs WS):** `loadOrders()` не должен перетирать более свежие WS-данные; merge по `row_version`/seq и отмена устаревших запросов. См. `app/static/js/admin-app.js`.
- [ ] **Admin UI refactor (без смены поведения):** разнести `app/templates/admin.html` на Jinja `{% include %}` по крупным блокам/экранам (`app/templates/screens/*`) без изменения Alpine/DOM; затем отдельным шагом — “ленивый DOM” (`x-if`/mount-on-demand) для performance.

## 🟡 P1: Ближайший спринт (Core SaaS)

- [ ] **E0.1: добить раскол временного `_monolith.py`** на подмодули `app/api/admin/` (цель: файлы ≤ ~1500 строк, без изменения поведения).
- [ ] **E2.2 Branding (backend):** `Tenant.brand_*` + `GET/PATCH /api/admin/branding` + `POST /api/admin/branding/logo` (UI уже готов/частично готов).
- [ ] **E2.3 Billing (минимум):** `Tenant.plan_status`, `billing_usage`, ежедневный rollup; блокировка login/вебхуков при `suspended`.
- [ ] **E5 ARQ-only:** убрать fallback на `BackgroundTasks` в `app/services/task_queue.py`, worker как обязательная часть прода.

## 🟢 P2: Развитие (Growth)

- [ ] **E1 хвост (платежи):** raw payload + полноценная верификация подписей для провайдеров (не ломая существующую идемпотентность).
- [ ] **E14 авто‑ссылка на оплату:** генерация ссылок в `intent_router` для предоплаты.
- [ ] **E8 WhatsApp интерактив:** кнопки Meta templates + (опционально) картинка‑чек.
- [ ] **Telegram оператор‑бот:** управление диалогами из Telegram (эскалации/ответы/уведомления).
- [x] **Экстренное закрытие ресторана:** причина + длительность паузы + корректное поведение вне рабочего времени.

## ⚪ P3: Бэклог и R&D

- [ ] **E11 Strategy Engine:** вынести upsell-логику из промпта в Python‑правила.
- [ ] **E12 RAG по меню:** семантический поиск для больших каталогов.
- [ ] **BI по iiko:** продажи по времени суток и автоподстройка upsell.

