# RestoMind — параллельный план для ИИ 1 и ИИ 2

Документ — **рабочая карта** распределения эпиков из [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) между двумя ИИ, которые пишут код параллельно. Цель — закрыть оставшийся объём до 100% без конфликтов в одних и тех же файлах.

Если ищете короткий промпт для ИИ 2 — он в [docs/AI2_PARALLEL_PROMPT.md](docs/AI2_PARALLEL_PROMPT.md). Этот файл — расширенная версия с задачами, DoD и контрактами API.

---

## 0. Принципы работы

1. **Источник истины** — [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md), колонка «Статус по эпикам». Перед началом любой задачи сверяемся с ним.
2. **Контракты API определяет ИИ 1.** ИИ 2 ждёт согласованной формы ответа (или формирует моки и потом синхронизируется). Если API уже есть в коде — ИИ 2 потребляет как есть, не правит сигнатуру.
3. **Один эпик = одна ветка / один PR**, чтобы merge не «слепливал» правки разных слоёв.
4. **Порядок merge: ИИ 1 → ИИ 2** для эпиков, где трогаются общие файлы. ИИ 2 пуллится с main перед началом UI-работы.
5. **CHANGELOG.md** дописывается в `## [Unreleased]` обоими — конфликт по этому файлу решается простым re-add без потерь строк.
6. **`pytest`** перед PR: ИИ 1 — полный, ИИ 2 — минимум `tests/test_admin_ui_*` и smoke в браузере, если есть.

### Sync-точки (когда ИИ 1 и ИИ 2 синхронизируются)

| Когда | Что делать |
|--------|------------|
| **После каждого PR из E0.1** (раскол `app/api/admin/`) | **ИИ 2:** `git pull` с `main`. Если открыты правки в `admin.html` или `admin-app.js` — вмержить обновления и продолжить. |
| **После merge E2.2.B** (брендинг backend) | **ИИ 2:** переключить UI с моков на реальный API (внутренний тикет **2.4** / эквивалент в трекере). |
| **После merge E2.3.B** (биллинг backend) | **ИИ 2:** запустить UI лимитов/usage (внутренний тикет **2.5**). |
| **После merge E3 хвоста** (backend KPI) | **ИИ 2:** завершить полировку «Вклад ИИ» по полному контракту (внутренний тикет **2.3**). |

Номера **2.3 / 2.4 / 2.5** — внутренние тикеты фронта; при другом трекере сохраняйте смысл колонок «после какого backend-мерджа что включать в UI».

### Что НЕ делать, пока E0.1 не завершён (серия PR по расколу)

- **ИИ 1:** **не** начинать E2.2 / E2.3 / E9 / E13 с новыми эндпоинтами в `app/api/admin/*`. Иначе при расколе придётся переносить уже добавленный код — двойная работа и лишние конфликты.
- **ИИ 2:** **не** править пакет `app/api/admin/` (ни `_monolith.py`, ни будущие подмодули) во время серии PR ИИ 1. Нужен эндпоинт под UI — отдельный тикет ИИ 1: эндпоинт в целевом подмодуле после согласования.
- **Оба:** **не** редактировать [`tests/conftest.py`](tests/conftest.py) «внахлёст»; общие фикстуры не плодить в корневом conftest без нужды — для фичи предпочтительно [`tests/<feature>/conftest.py`](tests/conftest.py).

---

## 1. Зоны ответственности (карта файлов)

### Зона ИИ 1 — backend

Файлы, которые трогает **только ИИ 1** (за исключением точечных правок по согласованию):

- `app/db/models.py` и все миграции `alembic/versions/*`.
- `app/services/*` — кроме `prompts.py` (там ИИ 2 может править юр. тексты — см. E16).
- `app/api/webhooks.py`, `app/api/payment_webhook.py`, `app/api/superadmin.py`, пакет [`app/api/admin/`](app/api/admin/) (в т.ч. `_monolith.py` до полного раскола **E0.1**).
- `app/integrations/*` (whatsapp, iiko_client, telegram, twilio_*).
- `app/worker.py`, `app/main.py`, `app/core/config.py`.
- `requirements.txt`, `Dockerfile`, `render.yaml`, `docker-compose*.yml`.
- Тесты в `tests/` для бэкенд-слоя.

### Зона ИИ 2 — фронт / UX / документация для оператора

Файлы, которые трогает **только ИИ 2**:

- `app/templates/*.html` (admin, superadmin, request-access, onboarding, любые компоненты).
- `app/static/js/*` (`admin-app.js` и любые новые JS).
- `app/static/css/*`.
- `app/static/uploads/*` (никаких правок кода — только инфраструктура каталогов при необходимости).
- Документация для оператора в `docs/` (если требуется), README по UX-фичам.
- Тесты UI: `tests/test_admin_ui_*.py` (если появятся фронтовые snapshot-тесты), и точечные backend-тесты только для эндпоинтов, которые добавляет сама ИИ 2 (см. ниже).

### Общие файлы — режим «узкий diff»

| Файл | Правила |
|------|---------|
| Пакет [`app/api/admin/`](app/api/admin/) | Пока идёт **E0.1** — только ИИ 1; ИИ 2 не коммитит в эти файлы (см. «Sync-точки» выше). После раскола — новые эндпоинты в **соответствующем подмодуле** (например `admin/branding.py` для E2.2), один домен = один логический коммит. В `_monolith.py` при промежуточном состоянии — секции `# ── <Эпик EX.Y> ──`, узкий diff. |
| `CHANGELOG.md` | Только дописывание в `## [Unreleased]`. Не трогать предыдущие записи. |
| `.env.example` | Каждый ИИ добавляет свой блок переменных в конце файла под комментарием с номером эпика. |
| `plan.md` | Только обновление статуса (галочка / снятие «открыто») по своим закрытым эпикам. Структуру не менять. |
| `IMPLEMENTATION_PLAN.md` | Обновление колонки «Статус по эпикам» при закрытии. |
| `tests/conftest.py` | По согласованию; добавлять новые фикстуры в новых файлах `tests/<feature>/conftest.py`. |

---

## 2. Эпики, уже закрытые в коде (НЕ трогать)

| Эпик | Состояние |
|------|-----------|
| **E1 ядро** | webhook + аудит `payment_webhook_events` + адаптеры `generic_hmac` / `cloudpayments` + каркасы `kaspi`/`freedom_pay` + Super Admin API+HTML аудита |
| **E17** | вкладка «Помощь клиентам» / «Ошибки», `POST /failed-tasks/{id}/retry`, кнопки retry/resolve |
| **E18** | индикатор Setup Score в шапке, чек-лист, тост на 100% |
| **E3 базовый** | `GET /api/admin/ai-value`, вкладка «Вклад ИИ», `tests/test_ai_value_metrics.py` |
| **E4** | optimistic update / merge — в коде |
| **E10** | `tests/regression/` — в коде |
| **E16 поле** | `Organization.prepayment_legal_text`, UI «Мой ресторан» |

Любые расширения этих эпиков — **только** в рамках п.4 ниже («хвосты»).

---

## 3. Что осталось до 100% — общая карта

Полный список открытого объёма:

- **E0** — рефакторинг архитектуры: см. [IMPLEMENTATION_PLAN.md §E0](IMPLEMENTATION_PLAN.md); приоритет **E0.1** (раскол `app/api/admin.py`) до крупных новых блоков **E2.2 / E2.3** в том же файле.
- **E1 хвост** — UNIQUE-индекс `(provider_slug, external_payment_id)` (продуктовое решение); скачивание сырого payload — `GET /api/superadmin/payment-webhook-events/{id}/payload.bin` + UI в Super Admin.
- **E2** — мульти-тенант «на продажу» (роли, брендинг, биллинг).
- **E3 хвост** — расширение KPI до полного списка из плана.
- **E5** — ARQ как единственная очередь.
- **E6** — латентность PSTN + Twilio bidirectional.
- **E7** — премиум-голос (OpenAI TTS / ElevenLabs).
- **E8** — WhatsApp интерактив + картинка-чек.
- **E9** — магия после импорта iiko (теги, upsell-seed, packaging-seed).
- **E11** — Strategy Engine из промпта в Python.
- **E12** — RAG для большого меню.
- **E13** — `order_suggestion_events` отдельная таблица.
- **E14** — авто-ссылка на оплату.
- **E15** — признак источника заказа в iiko.
- **E16 хвост** — юридически выверенный дефолтный текст в `prompts.py`.
- **S1–S4** — сквозные (документация, CI, .env, телеметрия).

---

## 4. Распределение эпиков

Условные обозначения: **B** — backend (ИИ 1), **F** — фронт (ИИ 2), **K** — координация (оба).

### Спринт A — параллельно с первого дня

#### Эпик E0 — рефакторинг `admin.py` (P0, техдолг)

Цель — уменьшить конфликты двух ИИ в одном файле. Подробная таблица **E0.1–E0.7** — [IMPLEMENTATION_PLAN.md §E0](IMPLEMENTATION_PLAN.md).

| Подзадача | Кому | Файлы | DoD-флаг |
|-----------|------|-------|----------|
| **E0.1** Раскол монолита на подмодули (перенос без смены логики) | B | `app/api/admin.py` → `app/api/admin/*.py`, [`main.py`](app/main.py) | те же URL; `pytest` зелёный; допускается цепочка PR |
| E0.2 SQL/CRUD → сервисы | B | `app/services/order_admin.py`, … | тонкие роутеры |
| E0.3 Pydantic для `items_json` | B | `app/schemas/order_items.py` (или аналог) | типизированный доступ без миграции |
| E0.4 Единый владелец dialog state | B | `dialog_mgr`, `users`, Redis | см. E5 |
| E0.5 `organization_integration_settings` | B | миграция, модели | см. E2.3 |
| E0.6 Tenancy `Depends` / RLS опционально | B | `tenant_scope`, deps | инкремент к E2.1 |
| E0.7 Доменные события поверх `publish_event` | B | `app/services/events.py` | см. E11 |

**Порядок с E2:** **E2.1.B** уже в main. Следующий крупный шаг ИИ 1 по админ-backend: **E0.1**, затем **E2.2.B** / **E2.3.B** (не наоборот — иначе снова раздувается монолит).

#### Эпик E2 — мульти-тенант (P0, XL)

| Подзадача | Кому | Файлы | DoD-флаг |
|-----------|------|-------|----------|
| E2.1.B Модель `StaffUser.tenant_owner_id`, миграция | B | `app/db/models.py`, `alembic/versions/<ts>_tenant_owner.py` | миграция применена |
| E2.1.B API `GET /auth/me` (расширение), `POST /auth/select-org` | B | `app/api/admin.py` (секция `# ── E2.1 ──`), `app/services/tenant_scope.py` | контракт ниже зафиксирован |
| E2.1.B Тесты scope | B | `tests/test_tenant_owner_scope.py`, `tests/test_select_org.py` | `pytest` зелёный |
| E2.1.F Селектор филиала в шапке | F | `app/templates/admin.html`, `app/static/js/admin-app.js` | **готово** — дропдаун при `available_organizations.length > 1`, `POST /auth/select-org` |
| E2.2.B Колонки `Tenant.brand_*`, миграция | B | `app/db/models.py`, миграция | DDL применён |
| E2.2.B API `GET/PATCH /api/admin/branding`, `POST /branding/logo` | B | `app/api/admin.py` (секция `# ── E2.2 ──`), `app/services/branding.py` (новый) | загрузка логотипа ≤ 1 MB, валидация PNG/JPG |
| E2.2.F UI «Настройки → Брендинг» (загрузка лого, color picker, превью шапки) | F | [`admin.html`](app/templates/admin.html), [`admin-app.js`](app/static/js/admin-app.js) | вкладка и сохранение при наличии E2.2.B; шапка — цвет аватара из `brand_color_hex`, лого из URL |
| E2.3.B Колонки `Tenant.plan_status / trial_ends_at / seats_limit / monthly_message_limit`, таблица `billing_usage`, ежедневный rollup | B | `app/db/models.py`, `alembic/versions/<ts>_billing.py`, `app/services/billing.py`, регистрация cron в `app/main.py` | ежедневный job заполняет `billing_usage` |
| E2.3.B Блок входа: при `plan_status='suspended'` — 403 на login и игнор WA | B | `app/api/admin.py` (auth/login), `app/api/webhooks.py` | юнит-тест блокировки |
| E2.3.B Superadmin API `/tenants/{id}/usage` | B | `app/api/superadmin.py` | пагинация по дням |
| E2.3.F Superadmin UI: usage-страница, индикатор плана | F | `app/templates/superadmin.html`, `app/static/js/admin-app.js` (или отдельный JS) | график за 30/90 дней |
| E2.3.F Дашборд админки: «Использовано X из Y сообщений» | F | `app/templates/admin.html`, `admin-app.js` (читает из `/api/admin/auth/me`) | блок виден при наличии лимита |

**Контракт `GET /api/admin/auth/me` после E2.1** (фиксируется ИИ 1, ИИ 2 потребляет):

```json
{
  "id": 12, "email": "owner@x.kz", "role": "admin",
  "is_superadmin": false,
  "tenant_owner_id": 3,
  "active_organization_id": 7,
  "available_organizations": [
    {"id": 7, "name": "Бариста ЦУМ"},
    {"id": 8, "name": "Бариста Достык"}
  ],
  "tenant": {"id": 3, "name": "Coffee Holding", "plan": "standard", "plan_status": "active"},
  "branding": {"brand_name": "Бариста", "brand_logo_url": "/static/uploads/branding/3.png", "brand_color_hex": "#7B3F00"},
  "ws_token": "…"
}
```

#### Эпик E3 хвост — полные KPI (P0, M)

| Подзадача | Кому | Файлы |
|-----------|------|-------|
| Расширить агрегаты: средний чек bot vs operator, `first_response_avg_sec`, разбивка по дням `bot_orders` / `bot_revenue` | B | `app/services/intelligence_analytics.py`, `app/api/admin.py` (`/ai-value`) |
| Полировка UI «Вклад ИИ»: топ-5 принятых, тренд `ai_profit`, пустые состояния, период custom | F | `app/templates/admin.html`, `app/static/js/admin-app.js` |
| Тесты на новые поля | B | `tests/test_ai_value_metrics.py` |

**DoD.** Раздел «Вклад ИИ» содержит все поля из таблицы метрик в [IMPLEMENTATION_PLAN.md §E3](IMPLEMENTATION_PLAN.md). UI без console error в браузере на пустом периоде.

#### Эпик E16 хвост — дефолтный юр. текст (P2, S)

| Подзадача | Кому | Файлы |
|-----------|------|-------|
| Дефолтная константа `DEFAULT_PREPAYMENT_LEGAL_TEXT_RU/KZ/EN` (текст согласован с продуктом) | F | `app/services/prompts.py` (только секция с константой), `app/templates/admin.html` (placeholder) |
| Подстановка дефолта при пустом `Organization.prepayment_legal_text` | B | `app/services/prompts.py` (используется при сборке prompt) — **через узкий diff в той же функции**, не плодим обёртки |

Координация: подсадить дефолт можно в одной функции; ИИ 2 кладёт константу, ИИ 1 (или ИИ 2 — кто первый) добавляет вызов. Конфликтов нет, если работать через `Edit` в разных строках файла.

#### Эпик E1 хвост — продуктовая идемпотентность (P0, S)

Только ИИ 1.

- Решение: оставить идемпотентность на уровне `apply_payment_webhook` (как сейчас) **или** добавить partial-unique-index по `(provider_slug, external_payment_id) WHERE external_payment_id IS NOT NULL` — миграция Alembic, тест на повторный INSERT.
- Download raw payload в Super Admin: эндпоинт `GET /api/superadmin/payment-webhook-events/{id}/payload.bin` (octet-stream); ИИ 2 затем добавит кнопку «Скачать payload» в `superadmin.html` — это **отдельный** PR ИИ 2 после merge ИИ 1.

---

### Спринт B

#### Эпик E5 — ARQ-only (P1, M) — только ИИ 1

Полностью backend. ИИ 2 не участвует.

- Удалить fallback на `BackgroundTasks` в `app/services/task_queue.py`.
- При `REDIS_ENABLED=false` (или недоступен) — startup-fail с понятным сообщением.
- `app/worker.py` — все джобы зарегистрированы.
- `render.yaml`, `docker-compose.prod.yml` — отдельный сервис worker.
- Тесты: `tests/test_task_queue_required.py`.

**Координация с ИИ 2:** после удаления `BackgroundTasks` поведение видимо для оператора только косвенно (новые ошибки в «Помощи клиентам» при недоступном Redis). UI-правок нет.

#### Эпик E14 — авто-ссылка на оплату (P2, M)

| Подзадача | Кому | Файлы |
|-----------|------|-------|
| Протокол `PaymentLinkProvider`, реестр, kaspi-mock и cloudpayments-mock | B | `app/services/payment_link.py`, `app/services/payment_providers/*_link.py` |
| Интеграция в `intent_router`: при `requires_order_prepayment` без ссылки — генерация и сохранение в `Order.payment_link_url` | B | `app/services/intent_router.py` |
| Отправка ссылки в WhatsApp в шаблоне «требуется предоплата» | B | `app/services/customer_reply.py` или `prompts.py` (одно место) |
| Тесты | B | `tests/test_payment_link_kaspi.py` |
| Поле «Включить авто-ссылку» в админке (toggle на филиал) | F | `app/templates/admin.html`, `admin-app.js` (читает из `Organization.payment_link_provider` — добавить колонку) |
| Колонка `Organization.payment_link_provider VARCHAR(64) NULL` | B | миграция |

**Контракт.** `Organization.payment_link_provider`: `null | "kaspi" | "cloudpayments" | "manual"`. ИИ 2 в UI показывает select с этими значениями.

---

### Спринт C

#### Эпик E8 — WhatsApp интерактив + картинка-чек (P1, M)

| Подзадача | Кому | Файлы |
|-----------|------|-------|
| `send_template` с поддержкой `interactive` (button reply, list reply) | B | `app/integrations/whatsapp.py` |
| Парсинг входящих `interactive` в вебхуке как text-intent | B | `app/api/webhooks.py` |
| Шаблоны `order_confirmed_v1`, `prepayment_required_v1`, `delivery_status_v1` (Meta-approved строки в коде) | B | `app/integrations/whatsapp_templates.py` (новый) |
| Генератор картинки-чека PNG (Pillow) | B | `app/services/order_receipt_image.py` |
| Отправка картинки после `confirmed` при флаге org `send_receipt_image` | B | `app/api/webhooks.py` (confirmation_flow) + миграция колонки |
| Тесты | B | `tests/test_whatsapp_interactive.py`, `tests/test_order_receipt_image.py` |
| Toggle «Отправлять картинку чека» в админке | F | `app/templates/admin.html`, `admin-app.js` |
| Превью картинки в админке (по тестовому заказу) | F | новый компонент в админке + вызов нового `GET /api/admin/orders/{id}/receipt-preview.png` (этот endpoint реализует ИИ 1) |

#### Эпик E9 — магия после импорта iiko (P2, L)

| Подзадача | Кому | Файлы |
|-----------|------|-------|
| Сервис `menu_autotag` (эвристики + LLM-фоллбек) | B | `app/services/menu_autotag.py` |
| `POST /api/admin/menu/autotag?dry_run=` | B | `app/api/admin.py` (секция `# ── E9.1 ──`) |
| Сервис `upsell_seed` | B | `app/services/upsell_seed.py` |
| `POST /api/admin/upsell-rules/seed?dry_run=` | B | `app/api/admin.py` (секция `# ── E9.2 ──`) |
| Сервис `packaging_seed` | B | `app/services/packaging_seed.py` |
| `POST /api/admin/packaging-rules/seed?dry_run=` | B | `app/api/admin.py` (секция `# ── E9.3 ──`) |
| Кнопка «Предложить теги» с модалкой diff в «Меню» | F | `admin-app.js`, `admin.html` |
| Кнопка «Создать стартовые правила» в «Допродажи» (мастер) | F | `admin-app.js`, `admin.html` |
| Кнопка «Предложить правила упаковки» в «Упаковка» | F | `admin-app.js`, `admin.html` |

**Контракт ответа `dry_run=true`** (одинаковый для всех трёх):
```json
{ "added": [...], "skipped": [...], "would_apply": 7 }
```
ИИ 2 показывает diff; при подтверждении вызывает тот же эндпоинт с `dry_run=false`.

---

### Спринт D

#### Эпик E6 — латентность PSTN + Twilio bidirectional (P1, XL) — ИИ 1 + ИИ 2 в финале

| Подзадача | Кому | Файлы |
|-----------|------|-------|
| Bidirectional Media Stream (исходящий μ-law в WS) | B | `app/api/twilio_voice.py`, `app/integrations/twilio_media.py` |
| VAD (RMS + порог 700 мс) | B | `app/services/voice_pipeline.py` |
| Streaming TTS | B | `app/services/tts_streaming.py`, интеграция в pipeline |
| Filler-фразы | B | `app/services/voice_filler.py` + `.wav` сэмплы в `app/static/voice_filler/<lang>/` |
| Метрики latency (структурированные JSON-логи) | B | `app/services/voice_metrics.py` |
| `GET /api/admin/voice-metrics?period=` | B | `app/api/admin.py` (секция `# ── E6 ──`) |
| Юнит-тесты | B | `tests/test_voice_vad.py`, `tests/test_twilio_outbound_frames.py` |
| Симулятор stream | B | `scripts/twilio_stream_simulator.py` |
| UI «Голос: латентность» — p50/p95/p99 по этапам | F | блок в «Аналитика» или «Интеграции» |

#### Эпик E7 — премиум-голос (P1, M)

| Подзадача | Кому | Файлы |
|-----------|------|-------|
| `tts_openai`, `tts_elevenlabs`, `tts_router` с выбором по env | B | `app/services/tts_*.py` |
| Тесты | B | `tests/test_tts_router.py` |
| Переключатель «Голос провайдер» в админке (Edge / OpenAI / ElevenLabs) | F | блок в «Интеграции → Голос», вызов `PATCH /api/admin/integrations` (ИИ 1 расширяет существующий эндпоинт) |

**Контракт.** Колонка `Organization.tts_provider VARCHAR(32) NULL` или env-only. Решает ИИ 1 — продуктовый выбор.

---

### Спринт E

#### Эпик E11 — Strategy Engine (P2, L) — только ИИ 1

Полностью backend. UI не меняется (правила upsell уже в админке через `upsell_rules`).

- Расширить `app/services/strategy_engine.py`: `SalesContext`, `DecisionEngine`, `RecommendationPicker`, `MessageEnhancer`, `Tracer`.
- Интегрировать в `intent_router` после парсинга `AIBrainResponse`.
- Сократить `prompts.py` блок RECOMMENDATION_ENGINE на тон + ограничения языка.
- Тесты `tests/test_strategy_engine.py`.

#### Эпик E13 — `order_suggestion_events` (P3, M) — ИИ 1 + UI ИИ 2

| Подзадача | Кому | Файлы |
|-----------|------|-------|
| Модель + миграция | B | `app/db/models.py`, миграция |
| Запись из `recommendation_sync` (двойная запись + обратная совместимость) | B | `app/services/recommendation_sync.py` |
| `GET /api/admin/upsell-events?period=&accepted=` | B | `app/api/admin.py` (секция `# ── E13 ──`) |
| Тесты | B | `tests/test_order_suggestion_events.py` |
| Список upsell-событий в «Допродажи» с фильтрами | F | `admin.html`, `admin-app.js` |

#### Эпик E12 — RAG меню (P3, L) — только ИИ 1

- Эмбеддинги `MenuItem` (или отдельная таблица).
- Поиск top-k.
- Интеграция в `build_menu_context` под флагом `MENU_RAG_ENABLED`.
- `POST /api/admin/menu/reindex` (UI кнопка — ИИ 2 в спринте E добавляет на «Меню»; маленький отдельный PR).

#### Эпик E15 — признак источника заказа в iiko (P3, S) — только ИИ 1

- Дополнить payload `create_delivery_order` в `iiko_client.py` префиксом `RM-{order_id}`.
- Юнит-тест.

---

## 5. Распределение S-задач (сквозные)

| Сквозная | Кому | Действие |
|----------|------|----------|
| **S1** Документация | оба | Каждый дополняет CHANGELOG/plan.md/IMPLEMENTATION_PLAN.md по своему эпику. После закрытия эпика — снять «открыто» в plan.md и обновить статус-таблицу в IMPLEMENTATION_PLAN.md. |
| **S2** CI | B | `worker-import-check` job; маркер `regression` в `pytest.ini` (если ещё не зафиксирован). |
| **S3** `.env.example` | оба | ИИ 1 — переменные платежей/голоса/ARQ. ИИ 2 — переменные UI (если появятся, например `ADMIN_FEATURE_RECEIPT_PREVIEW=true`). Каждый — в **своём блоке** в конце файла. |
| **S4** Telemetry | B | JSON-логи на критичных путях, `sentry_sdk.set_tag`. ИИ 2 не трогает. |

---

## 6. Контракты API, по которым ИИ 2 ждёт ИИ 1

В одном месте, чтобы ИИ 2 мог сверяться:

| Эндпоинт | Эпик | Кто реализует | Кто потребляет |
|----------|------|---------------|----------------|
| `GET /api/admin/auth/me` (расширение `available_organizations`, `branding`) | E2.1, E2.2 | B | F |
| `POST /api/admin/auth/select-org` | E2.1 | B | F |
| `GET /api/admin/branding`, `PATCH`, `POST /branding/logo` | E2.2 | B | F |
| `GET /api/superadmin/tenants/{id}/usage` | E2.3 | B | F |
| `GET /api/superadmin/payment-webhook-events/{id}/payload.bin` | E1 хвост | B | F (кнопка скачивания) |
| `GET /api/admin/ai-value` (расширенные KPI) | E3 хвост | B | F |
| `POST /api/admin/menu/autotag?dry_run=` | E9.1 | B | F |
| `POST /api/admin/upsell-rules/seed?dry_run=` | E9.2 | B | F |
| `POST /api/admin/packaging-rules/seed?dry_run=` | E9.3 | B | F |
| `GET /api/admin/orders/{id}/receipt-preview.png` | E8.2 | B | F |
| `GET /api/admin/voice-metrics?period=` | E6.4 | B | F |
| `GET /api/admin/upsell-events` | E13 | B | F |

Если ИИ 2 нужно начать UI до готовности API — **разрешено** сделать MSW-заглушку (mock service worker) или JS-фикстуру; после готовности API ИИ 2 удаляет фикстуру и подключает реальный вызов в **отдельном PR**.

---

## 7. Чек-лист в начале каждой сессии

### ИИ 1
1. `git pull origin main`.
2. Открыть [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) и [PARALLEL_AI_PLAN.md](PARALLEL_AI_PLAN.md), сверить «закрыто» / «в работе».
3. Если эпик трогает `app/api/admin.py` — проверить, нет ли свежих правок ИИ 2 в той же функции; если есть — взять задачу из другого эпика.
4. После реализации: `pytest`, обновить статус в IMPLEMENTATION_PLAN, дописать в CHANGELOG `## [Unreleased]`.
5. PR с заголовком `E<N>.<M>: <краткое описание>`.

### ИИ 2
1. `git pull origin main`.
2. Открыть [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) и [docs/AI2_PARALLEL_PROMPT.md](docs/AI2_PARALLEL_PROMPT.md).
3. Проверить, появился ли нужный API в коде (см. §6). Если ещё нет — заглушка или другая задача.
4. После реализации: smoke в браузере (логин → переход на вкладку → проверить отсутствие ошибок в консоли), запись в CHANGELOG.
5. PR с заголовком `E<N>.<M>: <UI описание>`.

---

## 8. Правила разрешения конфликтов

1. **Конфликт в `app/api/admin.py`.** Каждый эндпоинт в собственной секции с маркером `# ── E<N>.<M> ──`. При git-конфликте — оба блока сохраняются, никогда не сливать в один.
2. **Конфликт в `CHANGELOG.md`.** Берём обе записи, кладём подряд под `## [Unreleased]`.
3. **Конфликт в `admin.html`.** ИИ 1 не трогает `admin.html`, кроме случаев генерации скрытого `<script>` с серверным флагом. Если нужно — узкий diff и явное согласование.
4. **Конфликт в `admin-app.js`.** ИИ 1 не трогает; если ИИ 1 случайно добавил JS — переносится в ветку ИИ 2.
5. **Миграции.** ИИ 1 — единственный, кто пишет Alembic-ревизии. Имя файла: `<YYYYMMDD>_<epic>_<slug>.py`. Несколько ревизий в одном спринте линеаризуются по `down_revision`.
6. **Если оба правят `prompts.py`.** Делятся на разные функции/константы; финальный merge — `Edit` точечно.

---

## 9. Карта объёма (что осталось до 100%)

| Спринт | ИИ 1 | ИИ 2 | Длительность |
|--------|------|------|--------------|
| A | **E0.1** первым; затем **E2.2+E2.3** backend, E3 хвост backend, E1 хвост, E16 backend | E2.2 UI брендинг, E2.3 UI usage, E3 хвост UI (E2.1 UI / E2.1.B уже в main) | 3–4 недели |
| B | E5, E14 backend | E14 UI toggle | 2 недели |
| C | E8 backend, E9 backend | E8 UI, E9 UI (3 кнопки + diff-модалки) | 2 недели |
| D | E6 backend, E7 backend | E6 UI метрики, E7 UI селектор | 3 недели |
| E | E11, E12, E13 backend, E15 | E12 кнопка, E13 UI событий | 2 недели |

Итог при идеальной параллели: **≈ 12 недель** до 100% (vs ≈ 16 недель в одиночном плане).

---

## 10. Глобальный Definition of Done (повторение из IMPLEMENTATION_PLAN)

Эпик закрыт **только** когда:

1. Код в `main`, миграции применены на dev/прод-копии (`alembic upgrade head` без ошибок).
2. Тесты зелёные локально и в CI.
3. README / .env.example / plan.md / CHANGELOG.md / IMPLEMENTATION_PLAN.md обновлены.
4. Smoke-тест в админке пройден (для UI-эпиков — ИИ 2; для backend — ИИ 1 через `/docs` или curl).
5. Логи без WARN/ERROR на штатном пути.
6. Если меняется поведение для гостя — обновлены WhatsApp-шаблоны.

---

## 11. Что делать первым

1. **ИИ 1:** **E2.1.B** уже закрыт в коде (`tenant_owner_id`, расширенный `/auth/me`, `/auth/select-org`). Следующий приоритет: **E0.1** — раскол [`app/api/admin.py`](app/api/admin.py) на подмодули (**до** добавления **E2.2.B** / **E2.3.B** в тот же монолит). Детали — [IMPLEMENTATION_PLAN.md §E0](IMPLEMENTATION_PLAN.md).
2. **ИИ 2:** **E2.1.F** и полировка **E3** могут идти параллельно; следующий крупный UI — **E2.2.F** (брендинг), когда готов контракт API (**E2.2.B**) или по мокам. На время серии PR **E0.1** согласовывать любые правки в зоне будущего пакета `app/api/admin/*`.

Дальше — по таблице из §4 (спринт A включает **E0** и **E2**), спринты A → E.
