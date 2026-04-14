"""
Точка входа FastAPI приложения RestoMind.
Lifespan управляет жизненным циклом подключений к БД и Redis.
"""

import asyncio
import logging
import logging.handlers
from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import text
from starlette.middleware.sessions import SessionMiddleware

from app.api.admin import auth_router as admin_auth_router
from app.api.admin import router as admin_router
from app.api.admin import ws_router as admin_ws_router
from app.api.webhooks import router as webhooks_router
from app.core.config import settings
from app.db.models import Base
from app.db.session import (
    InMemoryRedis,
    async_engine,
    async_session_factory,
    init_redis_or_fallback,
    redis_client,
    redis_pubsub_available,
)

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"
# cache_size=0: обход падения Jinja2 LRUCache на Python 3.14 (TypeError при ключе кэша).
templates = Jinja2Templates(
    env=Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(),
        cache_size=0,
    ),
)

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
LOG_DIR = Path("logs")


def _setup_logging() -> None:
    """Настройка логирования: stdout + ротация файлов + Sentry (опционально)."""
    level = logging.DEBUG if settings.app_debug else logging.INFO
    root = logging.getLogger()
    root.setLevel(level)

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter(LOG_FORMAT))
    root.addHandler(console)

    LOG_DIR.mkdir(exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        LOG_DIR / "restomind.log",
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
    root.addHandler(file_handler)

    error_handler = logging.handlers.RotatingFileHandler(
        LOG_DIR / "errors.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(logging.Formatter(LOG_FORMAT))
    root.addHandler(error_handler)

    if settings.sentry_dsn:
        try:
            import sentry_sdk
            sentry_sdk.init(
                dsn=settings.sentry_dsn,
                traces_sample_rate=0.1,
                environment="production" if not settings.app_debug else "development",
            )
            logging.getLogger(__name__).info("Sentry подключён")
        except ImportError:
            logging.getLogger(__name__).warning("sentry-sdk не установлен — Sentry отключён")


_setup_logging()
logger = logging.getLogger(__name__)


STOP_LIST_SYNC_INTERVAL = 900  # 15 минут


async def _stop_list_sync_loop() -> None:
    """Фоновая задача: синхронизирует стоп-листы из iiko каждые 15 минут."""
    from app.services.integration_health import record_stoplist_sync
    from app.services.menu_sync import sync_stop_lists

    if not settings.iiko_api_login or not settings.iiko_organization_id:
        logger.info("iiko не настроен — синхронизация стоп-листов отключена")
        return

    first_cycle = True
    while True:
        try:
            if not first_cycle:
                await asyncio.sleep(STOP_LIST_SYNC_INTERVAL)
            first_cycle = False
        except asyncio.CancelledError:
            break
        async with async_session_factory() as db:
            try:
                stats = await sync_stop_lists(
                    db, settings.iiko_api_login, settings.iiko_organization_id,
                )
                await record_stoplist_sync(db, True, None)
                await db.commit()
                logger.info("Стоп-листы синхронизированы: %s", stats)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Ошибка синхронизации стоп-листов: %s", exc, exc_info=True)
                try:
                    await record_stoplist_sync(db, False, str(exc))
                    await db.commit()
                except Exception as exc2:
                    logger.error("Не удалось сохранить статус интеграции iiko: %s", exc2)


async def _chat_log_retention_loop() -> None:
    """Фоновая задача: удаление старых chat_logs по CHAT_LOG_RETENTION_DAYS."""
    from app.services.chat_log_retention import purge_old_chat_logs

    if settings.chat_log_retention_days <= 0:
        logger.info("CHAT_LOG_RETENTION_DAYS=0 — автоочистка chat_logs выключена")
        return

    interval = settings.chat_log_retention_interval_seconds
    first_cycle = True
    while True:
        try:
            if not first_cycle:
                await asyncio.sleep(interval)
            first_cycle = False
        except asyncio.CancelledError:
            break
        try:
            async with async_session_factory() as db:
                await purge_old_chat_logs(db)
                await db.commit()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Ошибка ретеншна chat_logs: %s", exc, exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Жизненный цикл приложения:
    - При старте: создаём таблицы (если SQLite), проверяем подключения, запускаем фоновые задачи.
    - При остановке: корректно закрываем все соединения.
    """
    logger.info("Запуск %s...", settings.app_name)
    logger.info("Режим БД: %s", settings.db_mode)

    try:
        async with async_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Таблицы БД готовы (%s)", settings.db_mode)
    except Exception as exc:
        logger.warning("Не удалось создать таблицы: %s", exc)

    # Колонка operator_note у users (create_all не добавляет поля в существующие таблицы)
    try:
        async with async_engine.begin() as conn:
            if settings.db_mode == "sqlite":
                await conn.execute(text("ALTER TABLE users ADD COLUMN operator_note TEXT DEFAULT ''"))
            else:
                await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS operator_note TEXT DEFAULT ''"))
    except Exception:
        pass  # колонка уже есть или другой диалект

    try:
        async with async_engine.begin() as conn:
            if settings.db_mode == "sqlite":
                await conn.execute(text("ALTER TABLE orders ADD COLUMN iiko_last_error VARCHAR(512)"))
            else:
                await conn.execute(
                    text("ALTER TABLE orders ADD COLUMN IF NOT EXISTS iiko_last_error VARCHAR(512)"),
                )
    except Exception:
        pass

    try:
        async with async_engine.begin() as conn:
            if settings.db_mode == "sqlite":
                await conn.execute(text("ALTER TABLE users ADD COLUMN ai_paused BOOLEAN DEFAULT 0"))
            else:
                await conn.execute(
                    text("ALTER TABLE users ADD COLUMN IF NOT EXISTS ai_paused BOOLEAN DEFAULT FALSE"),
                )
    except Exception:
        pass

    try:
        async with async_engine.begin() as conn:
            if settings.db_mode == "sqlite":
                await conn.execute(text("ALTER TABLE chat_logs ADD COLUMN meta_json TEXT"))
            else:
                await conn.execute(
                    text("ALTER TABLE chat_logs ADD COLUMN IF NOT EXISTS meta_json JSONB"),
                )
    except Exception:
        pass

    for sql_sqlite, sql_pg in (
        (
            "ALTER TABLE chat_logs ADD COLUMN provider_message_id VARCHAR(128)",
            "ALTER TABLE chat_logs ADD COLUMN IF NOT EXISTS provider_message_id VARCHAR(128)",
        ),
        (
            "ALTER TABLE chat_logs ADD COLUMN delivery_status VARCHAR(32)",
            "ALTER TABLE chat_logs ADD COLUMN IF NOT EXISTS delivery_status VARCHAR(32)",
        ),
        (
            "ALTER TABLE chat_logs ADD COLUMN error_details TEXT",
            "ALTER TABLE chat_logs ADD COLUMN IF NOT EXISTS error_details JSONB",
        ),
        (
            "ALTER TABLE chat_logs ADD COLUMN status_updated_at TIMESTAMP",
            "ALTER TABLE chat_logs ADD COLUMN IF NOT EXISTS status_updated_at TIMESTAMPTZ",
        ),
    ):
        try:
            async with async_engine.begin() as conn:
                if settings.db_mode == "sqlite":
                    await conn.execute(text(sql_sqlite))
                else:
                    await conn.execute(text(sql_pg))
        except Exception:
            pass

    try:
        async with async_engine.begin() as conn:
            if settings.db_mode == "sqlite":
                await conn.execute(
                    text("CREATE INDEX IF NOT EXISTS ix_chat_logs_provider_message_id ON chat_logs (provider_message_id)"),
                )
            else:
                await conn.execute(
                    text(
                        "CREATE INDEX IF NOT EXISTS ix_chat_logs_provider_message_id ON chat_logs (provider_message_id)",
                    ),
                )
    except Exception:
        pass

    try:
        async with async_engine.begin() as conn:
            if settings.db_mode == "sqlite":
                await conn.execute(text("ALTER TABLE bookings ADD COLUMN hall VARCHAR(20) DEFAULT 'hall_1'"))
            else:
                await conn.execute(
                    text("ALTER TABLE bookings ADD COLUMN IF NOT EXISTS hall VARCHAR(20) DEFAULT 'hall_1'"),
                )
    except Exception:
        pass

    # Заказ: optimistic locking (версия строки)
    try:
        async with async_engine.begin() as conn:
            if settings.db_mode == "sqlite":
                await conn.execute(
                    text("ALTER TABLE orders ADD COLUMN version INTEGER NOT NULL DEFAULT 1"),
                )
            else:
                await conn.execute(
                    text(
                        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1",
                    ),
                )
    except Exception:
        pass

    # Заказ: связь с бронью (предзаказ в зале) и поля предоплаты
    for sql_sqlite, sql_pg in (
        ("ALTER TABLE orders ADD COLUMN booking_id INTEGER", "ALTER TABLE orders ADD COLUMN IF NOT EXISTS booking_id INTEGER"),
        (
            "ALTER TABLE orders ADD COLUMN prepayment_status VARCHAR(30) DEFAULT 'not_required'",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS prepayment_status VARCHAR(30) DEFAULT 'not_required'",
        ),
        (
            "ALTER TABLE orders ADD COLUMN payment_link_url VARCHAR(1024)",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS payment_link_url VARCHAR(1024)",
        ),
    ):
        try:
            async with async_engine.begin() as conn:
                if settings.db_mode == "sqlite":
                    await conn.execute(text(sql_sqlite))
                else:
                    await conn.execute(text(sql_pg))
        except Exception:
            pass

    # State Recovery: поля сессии в User для восстановления после потери Redis
    for sql_sqlite, sql_pg in (
        (
            "ALTER TABLE users ADD COLUMN current_state VARCHAR(50) DEFAULT 'chatting'",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS current_state VARCHAR(50) DEFAULT 'chatting'",
        ),
        (
            "ALTER TABLE users ADD COLUMN current_pending_order_id INTEGER",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS current_pending_order_id INTEGER",
        ),
        (
            "ALTER TABLE users ADD COLUMN current_pending_booking_id INTEGER",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS current_pending_booking_id INTEGER",
        ),
    ):
        try:
            async with async_engine.begin() as conn:
                if settings.db_mode == "sqlite":
                    await conn.execute(text(sql_sqlite))
                else:
                    await conn.execute(text(sql_pg))
        except Exception:
            pass

    await init_redis_or_fallback()

    stop_list_task = asyncio.create_task(_stop_list_sync_loop())
    chat_retention_task = asyncio.create_task(_chat_log_retention_loop())

    yield

    for bg in (stop_list_task, chat_retention_task):
        bg.cancel()
        try:
            await bg
        except asyncio.CancelledError:
            pass

    await redis_client.aclose()
    await async_engine.dispose()
    logger.info("%s остановлен.", settings.app_name)


app = FastAPI(
    title=settings.app_name,
    description="AI-оператор для ресторана: заказы, бронирование, FAQ через WhatsApp",
    version=settings.app_version,
    lifespan=lifespan,
)

# Сессия для формы входа в админку (cookie)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret_key,
    session_cookie="restomind_admin",
    max_age=14 * 24 * 3600,
    same_site="lax",
    https_only=not settings.app_debug,
)

# --- Подключение роутеров ---
app.include_router(webhooks_router, prefix="/api")
app.include_router(admin_auth_router, prefix="/api")
app.include_router(admin_router, prefix="/api")
app.include_router(admin_ws_router, prefix="/api")

STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# --- Системные эндпоинты ---


@app.get("/health", tags=["System"])
async def health_check() -> dict:
    """
    Максимально лёгкий эндпоинт для UptimeRobot / Render.
    Только признак, что процесс жив — без запросов к БД.
    """
    return {"status": "ok"}


@app.get("/health/deep", tags=["System"])
async def deep_health_check() -> dict:
    """
    Глубокая проверка: доступность БД и Redis (для ручной диагностики).
    """
    health: dict = {"status": "ok", "db": "ok", "redis": "ok"}

    try:
        async with async_session_factory() as db:
            await db.execute(text("SELECT 1"))
    except Exception as exc:
        health["db"] = f"error: {exc!s}"
        health["status"] = "error"

    try:
        await redis_client.ping()
        if settings.redis_enabled and not redis_pubsub_available():
            health["redis"] = "ok (in-memory fallback — задайте REDIS_URL или REDIS_ENABLED=false)"
        elif isinstance(redis_client, InMemoryRedis):
            health["redis"] = "ok (in-memory)"
    except Exception as exc:
        health["redis"] = f"error: {exc!s}"
        health["status"] = "error"

    return health


@app.get("/", response_class=HTMLResponse, tags=["Admin Panel"])
@app.get("/admin", response_class=HTMLResponse, tags=["Admin Panel"])
async def admin_page(request: Request) -> HTMLResponse:
    """Главная страница — админ-панель."""
    response = templates.TemplateResponse(request, "admin.html")
    # Чтобы браузер не держал устаревший HTML (Alpine/шаблон после деплоя).
    response.headers["Cache-Control"] = "no-store, max-age=0, must-revalidate"
    return response
