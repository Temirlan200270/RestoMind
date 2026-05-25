# RestoMind — Conventions (инварианты разработки)

Это **контракт разработки**, который агент обязан соблюдать.

- **Что делать (задачи/статусы):** `docs/ROADMAP.md`
- **Что сделано (журнал):** `CHANGELOG.md`

---

## Rule 0: The Money Test

**Любой новый код должен либо показывать владельцу потерю денег, либо помогать её вернуть.**

Сложные прогнозы, архитектурные абстракции и «умные» функции — вторичны. Если фича не проходит Money Test, она не приоритет. Исключения: безопасность, надёжность, мультитенантность.

---

## 7 правил разработки (хардкор)

1. **Async-first** — I/O (БД, Redis, внешние API) только через `async`/`await`.
2. **WhatsApp webhook** — быстрый `200 OK`; тяжёлая логика не должна блокировать ответ.
3. **Structured AI** — ответы ИИ парсим в Pydantic (`AIBrainResponse`), без ручного JSON/regex.
4. **Тонкие роутеры** — в `app/api/` только приём запроса и вызов сервисов; бизнес‑ветвление живёт в `app/services/`.
5. **Цены/меню не “из головы”** — цены и номенклатура берём из БД/контекста меню; модель не придумывает цены.
6. **Консистентность (versioning)** — DRAFT обновляется через optimistic locking (`Order.row_version`).
7. **Idempotency + source of truth** — Redis не источник истины; idempotency входящих сообщений/событий обязателен. FSM: durable — `users.current_state`; при смене режима для админки из webhook — пара `human_needed` + `state_changed` (`publish_state_event`), не только алерт.
8. **Quick replies (LLM bypass)** — детерминированные короткие ответы только через `app/services/quick_replies.py`; новые шаблоны добавлять после замера false-positive; длина сообщения >40 символов не bypass'ит LLM.
9. **Нет айтишных терминов в UI** — пользовательский интерфейс говорит языком оператора ресторана, не разработчика. Запрещено добавлять в UI-тексты, лейблы, кнопки и подсказки: `webhook`, `payload`, `endpoint`, `API`, `boolean`, `null`, `callback`, `UUID`, `JSON`, `migrate`, `deprecated` и подобные технические термины, **если их там не было раньше**. Исключение: раздел «Настройки» для технических интеграций (iiko, WhatsApp), где термины неизбежны и уже присутствуют.

---

## 9. Инварианты OS-архитектуры (агентам обязательно читать)

Три правила вытекают из стратегии перехода RestoMind → AI OS ([`docs/OS_TRANSITION_PLAN.md`](docs/OS_TRANSITION_PLAN.md)). Нарушение любого из них создаёт технический долг уровня Phase 1–3 и блокирует enterprise-продажи.

**Rule 9 — Tenant Isolation Enforcement.**
Запрещено писать ORM/SQL запросы без явной фильтрации по `organization_id`. Каждый SELECT к таблицам `orders`, `chat_logs`, `bookings`, `menu_items`, `users`, `system_events` обязан содержать `.where(Model.organization_id == org_id)` или аналог. Нет исключений — даже в фоновых задачах и cron-джобах. Crossтенантный доступ (`SELECT * FROM orders`) — критический баг, не технический долг.

**Rule 10 — Event-First.**
Любое изменение бизнес-состояния (новый заказ, смена статуса, подтверждение брони, эскалация, смена режима ИИ) обязано порождать системное событие через `emit_system_event` из [`app/services/system_events.py`](app/services/system_events.py). Аналитика должна стремиться к чтению из событий (`SystemEvent`), а не напрямую из таблиц сущностей. Запрещено добавлять прямой SQL к `Order`/`ChatLog` в новых аналитических endpoint'ах — использовать агрегаты по `system_events`.

**Rule 11 — AI Context через ContextBuilder.**
ИИ не должен получать данные из БД напрямую внутри LLM-вызова. Все данные для промпта готовятся слоем `fetch_ai_read_context` → `AIReadContext` в [`app/services/context_engine.py`](app/services/context_engine.py). Сырые SQL-запросы внутри `call_openai` / `call_ai_with_audio` — запрещены. Новые поля контекста добавляются в `AIReadContext`, а не в тело вызова ИИ.

---

## 8. Инварианты Jinja2/HTML-шаблонов (агентам обязательно читать)

> Нарушение этих правил не детектируется линтерами — только тестами и визуально в браузере.

### 8.1 Баланс `<div>` в screen-файлах

**Правило:** Каждый файл `app/templates/screens/_tab_*.html` должен иметь **одинаковое количество** открывающих `<div` и закрывающих `</div>`.

**Почему это важно:**  
Лишний `</div>` в середине файла закрывает родительский flex-контейнер **преждевременно**. Следующий HTML-блок становится отдельным дочерним элементом `lg:flex` и на десктопе отображается как **отдельная колонка** рядом с контентом вместо того, чтобы быть под ним.

**Исторический пример (2026-05):**  
В `_tab_settings_restaurant.html` лишний `</div>` закрывал `flex-1` content div до секции “Платёжные провайдеры”. На десктопе платёжный блок плавал **рядом** с формой профиля. Тест: `tests/test_template_div_balance.py::test_settings_restaurant_payment_inside_content`.

**Что проверять при редактировании шаблонов:**
- После каждого добавления/удаления блока: `grep -c '<div' file` == `grep -c '</div>' file`
- Новые секции (напр. “Платёжные провайдеры”) должны добавляться **внутри** существующего `flex-1 min-w-0 space-y-6` div, не после его закрытия
- Settings-таблицы: внешний контейнер `lg:flex` должен иметь ровно **2 flex-children**: `settings_tabs()` и один `flex-1` content div

### 8.2 Синхронизация модели и миграций

**Правило:** Добавление любого нового поля в ORM-модель (`app/db/models.py`) **обязательно требует**:
1. Новой Alembic-миграции в `alembic/versions/` с `op.add_column`
2. Для SQLite dev-среды: добавить `ALTER TABLE ... ADD COLUMN` в `_apply_sqlite_startup_schema_patches()` в `app/main.py`

**Почему это критично:**  
SQLAlchemy генерирует `SELECT model_col1, model_col2, ...` при любом `db.get(Model, id)` или `select(Model)`. Если столбец есть в модели, но не в БД, **любой запрос к этой модели упадёт** с `UndefinedColumnError`. Для Organization это означает, что **вход в админку полностью ломается** — login-эндпоинт делает `db.get(Organization, oid)`.

**Исторический пример (2026-05):**  
`payment_config_json` добавлен в модель `Organization`, но миграция на прод уже применилась без этого столбца. Результат: `column organizations.payment_config_json does not exist` на каждый запрос → невозможно войти в систему. Тест: `tests/test_admin_login_regression.py::test_login_with_default_credentials`.

**Чек-лист при добавлении поля:**
```
[ ] Новое поле в app/db/models.py
[ ] Миграция alembic/versions/YYYYMMDD_*.py с op.add_column()
[ ] SQLite-патч в app/main.py _apply_sqlite_startup_schema_patches()
[ ] Запустить tests/test_admin_login_regression.py локально
```

