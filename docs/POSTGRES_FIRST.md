# Postgres-first local development

RestoMind uses PostgreSQL for local development, CI, staging, and production, so Alembic, ORM models, and runtime data all live in one schema reality.

SQLite is no longer a supported runtime/test database. Do not use `restomind.db` for local development.

## Why

- Local SQLite drift already caused real bugs: code expected columns such as `menu_items.cost_price`, while the local DB did not have them.
- Render production runs Alembic against PostgreSQL, so local development should exercise the same database type.
- Startup DDL is disabled in `app/main.py`; schema changes go through Alembic.

## Local setup

1. Start PostgreSQL and Redis:

```bash
docker compose up -d db redis
```

2. Use Postgres in `.env`:

```dotenv
DB_MODE=postgres
POSTGRES_USER=restomind
POSTGRES_PASSWORD=restomind_secret
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=restomind_db
```

If you use a managed/local DSN, set `DATABASE_URL` instead.

3. Apply schema:

```bash
alembic upgrade head
alembic current
```

4. Load local data only after migrations:

```bash
python seed.py
python scripts/sync_menu_from_iiko.py
```

For iiko sales facts:

```bash
python scripts/backfill_olap_sales.py --org-id 1 --days 30
```

## Rules

- New schema changes go through Alembic only.
- Tests use PostgreSQL too. `tests/conftest.py` defaults to `TEST_DATABASE_URL=postgresql+asyncpg://restomind:restomind_secret@localhost:5432/restomind_test`.
- CI runs with a `postgres:16` service and `REDIS_MEMORY_ONLY=true`.
- Before debugging data issues, check:

```bash
alembic current
alembic heads
```

