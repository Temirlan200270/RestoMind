# RestoMind — Conventions (инварианты разработки)

Это **контракт разработки**, который агент обязан соблюдать.

- **Что делать (задачи/статусы):** `docs/ROADMAP.md`
- **Что сделано (журнал):** `CHANGELOG.md`

---

## 7 правил разработки (хардкор)

1. **Async-first** — I/O (БД, Redis, внешние API) только через `async`/`await`.
2. **WhatsApp webhook** — быстрый `200 OK`; тяжёлая логика не должна блокировать ответ.
3. **Structured AI** — ответы ИИ парсим в Pydantic (`AIBrainResponse`), без ручного JSON/regex.
4. **Тонкие роутеры** — в `app/api/` только приём запроса и вызов сервисов; бизнес‑ветвление живёт в `app/services/`.
5. **Цены/меню не “из головы”** — цены и номенклатура берём из БД/контекста меню; модель не придумывает цены.
6. **Консистентность (versioning)** — DRAFT обновляется через optimistic locking (`Order.row_version`).
7. **Idempotency + source of truth** — Redis не источник истины; idempotency входящих сообщений/событий обязателен.

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

