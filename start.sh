#!/bin/sh
set -e

echo "[boot] alembic current (before)"
alembic current || true
echo "[boot] alembic upgrade head"
alembic upgrade head
echo "[boot] alembic current (after)"
alembic current || true
echo "[boot] starting uvicorn"
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"

