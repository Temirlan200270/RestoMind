#!/bin/sh
set -eu

worker_pid=""
web_pid=""
watchdog_pid=""

cleanup() {
    if [ -n "$watchdog_pid" ] && kill -0 "$watchdog_pid" 2>/dev/null; then
        kill "$watchdog_pid" 2>/dev/null || true
    fi
    if [ -n "$worker_pid" ] && kill -0 "$worker_pid" 2>/dev/null; then
        echo "[boot] stopping embedded ARQ worker"
        kill "$worker_pid" 2>/dev/null || true
        wait "$worker_pid" 2>/dev/null || true
    fi
    if [ -n "$web_pid" ] && kill -0 "$web_pid" 2>/dev/null; then
        echo "[boot] stopping uvicorn"
        kill "$web_pid" 2>/dev/null || true
        wait "$web_pid" 2>/dev/null || true
    fi
}

trap cleanup INT TERM EXIT

if [ "${RUN_MIGRATIONS_ON_START:-true}" != "false" ]; then
    echo "[boot] alembic current (before)"
    alembic current || true
    echo "[boot] alembic upgrade heads"
    alembic upgrade heads
    echo "[boot] alembic current (after)"
    alembic current || true
fi

if [ "${START_EMBEDDED_WORKER:-true}" != "false" ] \
    && [ "${ARQ_ENABLED:-false}" = "true" ] \
    && [ "${REDIS_ENABLED:-false}" = "true" ] \
    && [ "${REDIS_MEMORY_ONLY:-false}" != "true" ] \
    && [ -n "${REDIS_URL:-}" ]; then
    echo "[boot] starting embedded ARQ worker"
    python -m arq app.worker.WorkerSettings &
    worker_pid="$!"
else
    echo "[boot] embedded ARQ worker skipped; set START_EMBEDDED_WORKER=true, ARQ_ENABLED=true, REDIS_ENABLED=true, REDIS_MEMORY_ONLY=false and REDIS_URL"
fi

echo "[boot] starting uvicorn"
uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" &
web_pid="$!"

if [ -n "$worker_pid" ]; then
    (
        while kill -0 "$web_pid" 2>/dev/null; do
            if ! kill -0 "$worker_pid" 2>/dev/null; then
                echo "[boot] embedded ARQ worker exited; stopping web process so Render restarts the service"
                kill "$web_pid" 2>/dev/null || true
                exit 1
            fi
            sleep 5
        done
    ) &
    watchdog_pid="$!"
fi

wait "$web_pid"
exit "$?"
