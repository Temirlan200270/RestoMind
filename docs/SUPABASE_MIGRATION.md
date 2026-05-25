# Миграция PostgreSQL: Render → Supabase

RestoMind использует обычный PostgreSQL (`DATABASE_URL`, SQLAlchemy + asyncpg, Alembic). Supabase — это управляемый Postgres; приложению не нужны клиентские SDK Supabase для работы с данными.

## 1. Проект Supabase и строка подключения

1. [Supabase Dashboard](https://supabase.com/dashboard) → **New project** (регион по желанию, пароль БД сохраните).
2. **Project Settings → Database**:
   - Скопируйте **URI** в разделе connection strings.
   - Для приложения на Render (долгоживущий процесс + пул SQLAlchemy в [app/db/session.py](app/db/session.py)) предпочтительны:
     - **Direct connection** (порт `5432`), или
     - **Session pooler** (порт `5432`, `*.pooler.supabase.com`) — лимит **~15 клиентов на весь проект**. RestoMind на Render: **`DB_POOL_SIZE=3`, `DB_MAX_OVERFLOW=2`** на процесс (web + worker) — см. [`render.yaml`](../render.yaml). При `EMAXCONNSESSION`: **transaction pooler :6543** (`SUPABASE_PREFER_TRANSACTION_POOLER=true`) или уменьшите pool.
   - **Transaction pooler** (порт `6543`) — больше соединений; приложение отключает prepared statement cache для asyncpg. Подходит при высокой параллельности.
3. Убедитесь, что в URI есть `?sslmode=require` (часто уже встроен в строку из Dashboard).

Локально в `.env` для проверки:

```env
DATABASE_URL=postgresql://postgres.[ref]:[PASSWORD]@aws-0-[region].pooler.supabase.com:5432/postgres?sslmode=require
```

Приложение само переводит `postgresql://` в `postgresql+asyncpg://` ([app/core/config.py](app/core/config.py)).

---

## 2. Перенос данных (pg_dump / pg_restore)

Нужны клиенты **PostgreSQL** (`pg_dump`, `pg_restore`) в PATH — например [PostgreSQL Windows installer](https://www.postgresql.org/download/windows/) или пакет на Linux/macOS.

**Источник:** в Render → ваша **PostgreSQL** → **Connections** → **External Database URL** (или внутренний URL, если гоняете дамп с другого сервиса в той же сети).

**Приёмник:** URI Supabase для **прямого** подключения (для полного restore удобнее direct, не transaction pooler).

### Вариант: custom-формат (рекомендуется)

Замените плейсхолдеры кавычками в PowerShell.

```powershell
$env:RENDER_DATABASE_URL = "postgresql://..."
$env:SUPABASE_DATABASE_URL = "postgresql://postgres.[ref]:[PASSWORD]@db.[ref].supabase.co:5432/postgres?sslmode=require"

pg_dump --format=custom --no-owner --no-acl --dbname=$env:RENDER_DATABASE_URL -f restomind.dump
pg_restore --no-owner --no-acl --dbname=$env:SUPABASE_DATABASE_URL restomind.dump
```

### Bash

```bash
export RENDER_DATABASE_URL="postgresql://..."
export SUPABASE_DATABASE_URL="postgresql://...@db.xxx.supabase.co:5432/postgres?sslmode=require"

pg_dump --format=custom --no-owner --no-acl --dbname="$RENDER_DATABASE_URL" -f restomind.dump
pg_restore --no-owner --no-acl --dbname="$SUPABASE_DATABASE_URL" restomind.dump
```

Если `pg_restore` ругается на объекты `auth` / `storage` в схеме `auth` Supabase — вы делали дамп только своей схемы `public`. Для RestoMind достаточно дампа схемы приложения (Alembic создаёт таблицы в `public`). При необходимости ограничьте дамп:

```bash
pg_dump --format=custom --no-owner --no-acl --schema=public --dbname="$RENDER_DATABASE_URL" -f restomind.dump
```

После успешного restore проверьте строки в ключевых таблицах (например через Supabase **Table Editor** или `psql`).

---

## 3. Проверка миграций (Alembic)

Из корня репозитория, с активированным venv и тем же `DATABASE_URL`, что у Supabase:

**PowerShell:**

```powershell
$env:DATABASE_URL = "postgresql://...@db.xxx.supabase.co:5432/postgres?sslmode=require"
alembic upgrade head
```

**Bash:**

```bash
export DATABASE_URL="postgresql://...@db.xxx.supabase.co:5432/postgres?sslmode=require"
alembic upgrade head
```

Ожидается завершение без ошибок; таблица `alembic_version` должна соответствовать последней ревизии. Если дамп уже содержал актуальную схему, Alembic может просто подтвердить «уже на head».

---

## 4. Render: переменные окружения

1. **Dashboard** → ваш **Web Service** → **Environment**.
2. Задайте **`DATABASE_URL`** = полная строка Supabase (как в Dashboard, с `sslmode=require`). Символы в пароле при необходимости [URL-encode](https://developer.mozilla.org/en-US/docs/Glossary/Percent-encoding).
3. Сохраните и выполните **Manual Deploy** (или дождитесь деплоя из git).

`preDeployCommand: alembic upgrade head` в [render.yaml](../render.yaml) по-прежнему прогоняет миграции перед стартом.

### Переход со старого Blueprint (managed Postgres на Render)

Если раньше `DATABASE_URL` подставлялся из ресурса **PostgreSQL** в Blueprint, после обновления репозитория задайте `DATABASE_URL` вручную (секрет Supabase). Старый инстанс PostgreSQL на Render можно удалить, когда убедитесь, что прод ходит в Supabase — чтобы не платить дважды.

---

## 5. Сеть и IPv4

Если Render не подключается к хосту Supabase, проверьте [документацию Supabase](https://supabase.com/docs/guides/database/connecting-to-postgres) (в т.ч. **IPv4 add-on** для direct-подключений с сетей без IPv6). Сначала попробуйте **Session pooler** или строку из раздела **Pooler** в Dashboard.

---

## См. также

- [DEPLOY_RENDER.md](../DEPLOY_RENDER.md) — деплой веб-сервиса на Render.
- [.env.example](../.env.example) — переменные `DATABASE_URL` / `DB_MODE`.
