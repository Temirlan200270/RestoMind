#!/bin/sh
set -e

if [ "${RUN_BOOT_MIGRATIONS:-false}" = "true" ]; then
  echo "[boot] alembic upgrade head"
  alembic upgrade head
else
  echo "[boot] skipping alembic; run migrations in pre-deploy (set RUN_BOOT_MIGRATIONS=true to override)"
fi

echo "[boot] starting uvicorn"
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"

