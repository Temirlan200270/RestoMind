"""
Точка входа FastAPI приложения RestoMind.
Lifespan управляет жизненным циклом подключений к БД и Redis.
"""

import asyncio
import logging
import logging.handlers
import os
import time
from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from app.middleware.static_cache import LongCacheStaticFiles
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import select, text
from starlette.middleware.sessions import SessionMiddleware

from app.middleware.admin_action_audit import admin_action_audit_middleware
from app.middleware.admin_org_rate_limit import admin_org_rate_limit_middleware
from app.middleware.tenant_rls import tenant_rls_middleware

from app.api.demo_public import demo_public_router
from app.api.admin import auth_router as admin_auth_router
from app.api.admin import router as admin_router
from app.api.admin import ws_router as admin_ws_router
from app.api.admin.analytics import router as admin_analytics_router
from app.api.admin.menu import router as admin_menu_router
from app.api.admin.orders import router as admin_orders_router
from app.api.admin.organization import router as admin_organization_router
from app.api.admin.rules import router as admin_rules_router
from app.api.admin.intelligence import router as admin_intelligence_router
from app.api.public_agent_actions import router as public_agent_actions_router
from app.api.admin.owner_intelligence import router as admin_owner_intelligence_router
from app.api.admin.owner_intelligence_analytics import router as admin_owner_intelligence_analytics_router
from app.api.admin.owner_intelligence_audits import router as admin_owner_intelligence_audits_router
from app.api.admin.owner_intelligence_ops import owner_intelligence_ops_router as admin_owner_intelligence_ops_router
from app.api.admin.owner_intelligence_upsell import router as admin_owner_intelligence_upsell_router
from app.api.admin.inventory_sync import router as admin_inventory_sync_router
from app.api.admin.waiter_kpi import router as admin_waiter_kpi_router
from app.api.admin.network import network_router as admin_network_router
from app.api.admin.test_bot import test_bot_router as admin_test_bot_router
from app.api.iiko_webhook import router as iiko_webhook_router
from app.api.admin.marketing import loyalty_router as admin_loyalty_router
from app.api.admin.marketing import router as admin_marketing_router
from app.api.payment_webhook import router as payment_webhook_router
from app.api.superadmin import router as superadmin_router
from app.api.telegram_webhook import router as telegram_webhook_router
from app.api.webhooks import router as webhooks_router
from app.core.config import settings
from app.db.models import Organization
from app.integrations.whatsapp import close_whatsapp_http_client, init_whatsapp_http_client
from app.services.demo_data import seed_demo_data
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

    # На Windows, при uvicorn --reload и при параллельных процессах тестов,
    # ротация RotatingFileHandler часто падает с WinError 32 (файл занят другим процессом).
    # Поэтому файловые лог-хендлеры включаем только в "нормальном" рантайме.
    # PYTEST_CURRENT_TEST появляется не всегда на стадии import/collection,
    # поэтому используем более надёжный признак — загруженный модуль pytest.
    import sys

    is_pytest = (
        "pytest" in sys.modules
        or bool((os.environ.get("PYTEST_CURRENT_TEST") or "").strip())
        or bool((os.environ.get("PYTEST_ADDOPTS") or "").strip())
    )
    is_reload = bool((os.environ.get("UVICORN_RELOAD") or "").strip())
    if is_pytest or is_reload:
        return

    LOG_DIR.mkdir(exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        LOG_DIR / "restomind.log",
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
        encoding="utf-8",
        delay=True,
    )
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
    root.addHandler(file_handler)

    error_handler = logging.handlers.RotatingFileHandler(
        LOG_DIR / "errors.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
        delay=True,
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

# Монотонные часы последнего запуска авто-синка меню в фоновом цикле (при 0 в настройках не используется).
_IIKO_MENU_AUTO_MONO_LAST: float | None = None


async def _iiko_sync_targets(db) -> list[tuple[int, str, str, str]]:
    """Филиалы с валидными кредами iiko + fallback на IIKO_* из .env."""
    from app.services.org_iiko import list_organizations_with_iiko_db, resolve_org_iiko_credentials

    targets: list[tuple[int, str, str, str]] = []
    for org_row in await list_organizations_with_iiko_db(db):
        c = await resolve_org_iiko_credentials(db, int(org_row.id))
        if c is None:
            continue
        targets.append(
            (
                int(org_row.id),
                c.api_login,
                c.iiko_organization_id,
                (c.terminal_group_id or "").strip(),
            ),
        )
    if not targets and settings.iiko_api_login and settings.iiko_organization_id:
        fallback_oid = await db.scalar(select(Organization.id).order_by(Organization.id.asc()).limit(1))
        targets.append(
            (
                int(fallback_oid) if fallback_oid is not None else int(settings.default_organization_id),
                settings.iiko_api_login.strip(),
                settings.iiko_organization_id.strip(),
                (settings.iiko_terminal_group_id or "").strip(),
            ),
        )
    return targets


async def _stop_list_sync_loop() -> None:
    """Фоново: стоп-листы iiko (каждые 15 мин) и опционально полный импорт меню (IIKO_MENU_AUTO_SYNC_INTERVAL_SECONDS)."""
    from app.services.integration_health import record_menu_sync, record_stoplist_sync
    from app.services.menu_sync import sync_menu_from_iiko, sync_stop_lists
    from app.services.redis_locks import acquire_redis_lock, release_redis_lock

    global _IIKO_MENU_AUTO_MONO_LAST
    first_cycle = True
    while True:
        try:
            if not first_cycle:
                await asyncio.sleep(STOP_LIST_SYNC_INTERVAL)
            first_cycle = False
        except asyncio.CancelledError:
            break
        lock_key = "restomind:bg:stop_list_sync"
        lock_token = await acquire_redis_lock(lock_key, ttl_sec=max(60, int(STOP_LIST_SYNC_INTERVAL * 2)))
        if lock_token is None:
            logger.info("Стоп-листы: цикл выполняет другой инстанс")
            continue
        async with async_session_factory() as db:
            try:
                targets = await _iiko_sync_targets(db)
                if not targets:
                    continue
                for oid, login, iorg, tg in targets:
                    try:
                        stats = await sync_stop_lists(
                            db,
                            login,
                            iorg,
                            terminal_group_id=tg or None,
                            menu_organization_id=oid,
                        )
                        from app.services.order_logic import invalidate_menu_context_cache

                        invalidate_menu_context_cache(oid)
                        await record_stoplist_sync(db, True, None, organization_id=oid)
                        logger.info("Стоп-листы org=%s: %s", oid, stats)
                    except Exception as exc:
                        logger.error("Стоп-листы org=%s: %s", oid, exc, exc_info=True)
                        await record_stoplist_sync(db, False, str(exc), organization_id=oid)

                menu_iv = max(0, int(settings.iiko_menu_auto_sync_interval_seconds or 0))
                if menu_iv > 0:
                    now_m = time.monotonic()
                    if _IIKO_MENU_AUTO_MONO_LAST is None:
                        _IIKO_MENU_AUTO_MONO_LAST = now_m
                    elif now_m - _IIKO_MENU_AUTO_MONO_LAST >= float(menu_iv):
                        _IIKO_MENU_AUTO_MONO_LAST = now_m
                        for oid, login, iorg, _tg in targets:
                            try:
                                stats = await sync_menu_from_iiko(
                                    db, login, iorg, restomind_organization_id=oid,
                                )
                                sk = stats.get("skipped")
                                detail_m = (
                                    f"Авто-синхронизация меню: успешно "
                                    f"(всего {stats.get('total', 0)}, новых {stats.get('created', 0)}, "
                                    f"обновлено {stats.get('updated', 0)}"
                                    + (f", пропущено {sk}" if sk else "")
                                    + ")"
                                )
                                await record_menu_sync(db, True, None, detail=detail_m, organization_id=oid)
                                logger.info("Авто-меню org=%s: %s", oid, stats)
                            except Exception as exc:
                                logger.error("Авто-меню org=%s: %s", oid, exc, exc_info=True)
                                await record_menu_sync(db, False, str(exc), organization_id=oid)

                await db.commit()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Ошибка синхронизации стоп-листов: %s", exc, exc_info=True)
                try:
                    await record_stoplist_sync(db, False, str(exc))
                    await db.commit()
                except Exception as exc2:
                    logger.error("Не удалось сохранить статус интеграции iiko: %s", exc2)
            finally:
                await release_redis_lock(lock_key, lock_token)


async def _chat_log_retention_loop() -> None:
    """Фоновая задача: удаление старых chat_logs по CHAT_LOG_RETENTION_DAYS."""
    from app.services.chat_log_retention import purge_old_chat_logs_for_all_orgs
    from app.services.redis_locks import acquire_redis_lock, release_redis_lock

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
        lock_key = "restomind:bg:chat_log_retention"
        lock_token = await acquire_redis_lock(lock_key, ttl_sec=max(60, int(interval * 2)))
        if lock_token is None:
            logger.info("Ретеншн chat_logs: цикл выполняет другой инстанс")
            continue
        try:
            async with async_session_factory() as db:
                await purge_old_chat_logs_for_all_orgs(db)
                await db.commit()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Ошибка ретеншна chat_logs: %s", exc, exc_info=True)
        finally:
            await release_redis_lock(lock_key, lock_token)


async def _payment_expiry_loop() -> None:
    """Каждые 10 мин: expired + WhatsApp re-initiation reminder для клиентов."""
    from app.services.payment_expiry import expire_stale_payment_transactions
    from app.services.payment_reminder import send_payment_reminders_for_expired
    from app.services.redis_locks import acquire_redis_lock, release_redis_lock

    while True:
        try:
            await asyncio.sleep(600)
        except asyncio.CancelledError:
            break
        lock_key = "restomind:bg:payment_expiry"
        lock_token = await acquire_redis_lock(lock_key, ttl_sec=900)
        if lock_token is None:
            logger.info("Payment expiry: цикл выполняет другой инстанс")
            continue
        try:
            async with async_session_factory() as db:
                expired = await expire_stale_payment_transactions(db)
                await db.commit()

            if expired:
                logger.info("Payment expiry: %d транзакций expired, запускаем reminders", len(expired))
                sent = await send_payment_reminders_for_expired(expired, async_session_factory)
                if sent:
                    logger.info("Payment reminders отправлено: %d", sent)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Ошибка payment_expiry_loop: %s", exc, exc_info=True)
        finally:
            await release_redis_lock(lock_key, lock_token)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Жизненный цикл приложения:
    - При старте: схема управляется Alembic до запуска приложения.
    - Seed tenants/demo, фоновые задачи, Redis/WhatsApp.
    - При остановке: корректно закрываем все соединения.
    """
    logger.info("Запуск %s...", settings.app_name)
    logger.info("Режим БД: %s", settings.db_mode)
    logger.info("PostgreSQL: startup DDL disabled; run Alembic migrations before uvicorn.")

    try:
        async with async_engine.begin() as conn:
            sql1 = (
                "INSERT INTO tenants (name, plan) SELECT 'Default', 'standard' "
                "WHERE NOT EXISTS (SELECT 1 FROM tenants LIMIT 1)"
            )
            await conn.execute(text(sql1))
            sql2 = (
                "UPDATE organizations SET tenant_id = (SELECT id FROM tenants ORDER BY id ASC LIMIT 1) "
                "WHERE tenant_id IS NULL"
            )
            await conn.execute(text(sql2))
            # Казахстан: по умолчанию используем единый локальный пояс UTC+5.
            sql3 = (
                "UPDATE organizations SET timezone = 'Etc/GMT-5' "
                "WHERE timezone IS NULL OR timezone = '' OR UPPER(timezone) = 'UTC'"
            )
            await conn.execute(text(sql3))
    except Exception as exc:
        # Это не DDL, но критично для мультитенантности — не глушим полностью.
        logger.warning("Startup seed/repair failed: %s", exc, exc_info=True)

    try:
        async with async_session_factory() as db:
            demo_org = await db.scalar(
                select(Organization).where(
                    Organization.is_demo.is_(True),
                ),
            )
            if demo_org is None:
                demo_org = await db.scalar(
                    select(Organization).where(Organization.slug == "demo"),
                )
                if demo_org is not None:
                    demo_org.is_demo = True
            if demo_org is None:
                tenant_id = await db.scalar(text("SELECT id FROM tenants ORDER BY id ASC LIMIT 1"))
                demo_org = Organization(
                    tenant_id=int(tenant_id) if tenant_id is not None else None,
                    name="Demo Cafe",
                    slug="demo",
                    is_active=True,
                    is_demo=True,
                    timezone="Etc/GMT-5",
                    currency="KZT",
                )
                db.add(demo_org)
                await db.flush()
            await seed_demo_data(db, organization_id=int(demo_org.id))
            await db.commit()
            from app.services.demo_login_cache import set_cached_demo_org_id

            set_cached_demo_org_id(int(demo_org.id))
    except Exception as exc:
        logger.warning("Demo organization seed failed: %s", exc, exc_info=True)

    await init_redis_or_fallback()

    if settings.is_prod_like:
        from app.services.task_queue import assert_arq_reachable_for_prod_startup

        await assert_arq_reachable_for_prod_startup()
        logger.info("ARQ/Redis: production/staging connectivity check OK")

    await init_whatsapp_http_client()

    stop_list_task = asyncio.create_task(_stop_list_sync_loop())
    chat_retention_task = asyncio.create_task(_chat_log_retention_loop())
    payment_expiry_task = asyncio.create_task(_payment_expiry_loop())

    from app.services.recommendations import recommendations_daily_loop
    recommendations_task = asyncio.create_task(recommendations_daily_loop(async_session_factory))

    yield

    for bg in (stop_list_task, chat_retention_task, payment_expiry_task, recommendations_task):
        bg.cancel()
        try:
            await bg
        except asyncio.CancelledError:
            pass

    await close_whatsapp_http_client()

    await redis_client.aclose()
    await async_engine.dispose()
    logger.info("%s остановлен.", settings.app_name)


app = FastAPI(
    title=settings.app_name,
    description="AI-оператор для ресторана: заказы, бронирование, FAQ через WhatsApp",
    version=settings.app_version,
    lifespan=lifespan,
)

# Phase 2b OS: per-org rate limit + audit admin mutations + Postgres RLS context
app.middleware("http")(tenant_rls_middleware)
app.middleware("http")(admin_org_rate_limit_middleware)
app.middleware("http")(admin_action_audit_middleware)

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
app.include_router(demo_public_router)
app.include_router(public_agent_actions_router, prefix="/api")
app.include_router(webhooks_router, prefix="/api")
app.include_router(payment_webhook_router, prefix="/api")
app.include_router(admin_auth_router, prefix="/api")
app.include_router(admin_router, prefix="/api")
app.include_router(admin_analytics_router, prefix="/api")
app.include_router(admin_menu_router, prefix="/api")
app.include_router(admin_orders_router, prefix="/api")
app.include_router(admin_organization_router, prefix="/api")
app.include_router(admin_rules_router, prefix="/api")
app.include_router(admin_intelligence_router, prefix="/api")
app.include_router(admin_owner_intelligence_router, prefix="/api")
app.include_router(admin_owner_intelligence_audits_router, prefix="/api")
app.include_router(admin_owner_intelligence_upsell_router, prefix="/api")
app.include_router(admin_owner_intelligence_analytics_router, prefix="/api")
app.include_router(admin_owner_intelligence_ops_router, prefix="/api")
app.include_router(admin_inventory_sync_router, prefix="/api")
app.include_router(admin_waiter_kpi_router, prefix="/api")
app.include_router(admin_network_router, prefix="/api")
app.include_router(admin_test_bot_router, prefix="/api")
app.include_router(admin_marketing_router, prefix="/api")
app.include_router(admin_loyalty_router, prefix="/api")
app.include_router(admin_ws_router, prefix="/api")
app.include_router(superadmin_router, prefix="/api")
app.include_router(telegram_webhook_router, prefix="/api")
app.include_router(iiko_webhook_router)  # prefix="/api/iiko" уже задан внутри роутера

STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", LongCacheStaticFiles(directory=str(STATIC_DIR)), name="static")


# --- Системные эндпоинты ---


@app.api_route("/health", methods=["GET", "HEAD"], tags=["System"])
async def health_check() -> dict:
    """
    Максимально лёгкий эндпоинт для UptimeRobot / Render.
    Только признак, что процесс жив — без запросов к БД.

    HEAD добавлен явно: FastAPI (в отличие от raw Starlette) не подставляет
    HEAD автоматически для GET-роутов, а внешние мониторы (UptimeRobot и пр.)
    часто шлют именно HEAD — без HEAD они получали 405.
    """
    return {"status": "ok"}


@app.api_route("/admin/health", methods=["GET", "HEAD"], tags=["System"])
async def admin_health_check() -> dict:
    """
    Отдельный health endpoint для внешних проверок, которые почему-то стучатся в /admin/health.
    HEAD поддержан явно — см. комментарий в /health.
    """
    return {"status": "ok"}


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> Response:
    """
    В проде браузеры/healthcheck часто запрашивают /favicon.ico.
    Делаем редирект на SVG в /static, чтобы не спамить 404 в логах.
    """
    return RedirectResponse(url="/static/favicon.svg", status_code=307)


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
@app.get("/dashboard", response_class=HTMLResponse, tags=["Admin Panel"])
@app.get("/analytics", response_class=HTMLResponse, tags=["Admin Panel"])
@app.get("/ai_value", response_class=HTMLResponse, tags=["Admin Panel"])
@app.get("/intelligence", response_class=HTMLResponse, tags=["Admin Panel"])
@app.get("/digital_twin", response_class=HTMLResponse, tags=["Admin Panel"])
@app.get("/incidents", response_class=HTMLResponse, tags=["Admin Panel"])
@app.get("/orders", response_class=HTMLResponse, tags=["Admin Panel"])
@app.get("/operator_queue", response_class=HTMLResponse, tags=["Admin Panel"])
@app.get("/bookings", response_class=HTMLResponse, tags=["Admin Panel"])
@app.get("/chats", response_class=HTMLResponse, tags=["Admin Panel"])
@app.get("/menu", response_class=HTMLResponse, tags=["Admin Panel"])
@app.get("/settings", response_class=HTMLResponse, tags=["Admin Panel"])
async def admin_page(request: Request) -> HTMLResponse:
    """Главная страница — админ-панель."""
    def _asset_ver() -> str:
        """
        Версия ассетов для cache-busting.
        На Render иногда нет RENDER_GIT_COMMIT → добавляем mtime статических файлов,
        чтобы после деплоя не оставался кэш старого JS/CSS (ломает навигацию/IA).
        """
        git_sha_local = (os.environ.get("RENDER_GIT_COMMIT") or os.environ.get("GIT_COMMIT") or "").strip()
        sha7_local = git_sha_local[:7] if git_sha_local else ""
        if sha7_local:
            return settings.app_version + f"-{sha7_local}"
        try:
            # Берём "самый новый" mtime из ключевых статик-файлов админки.
            # Это меняется при любом обновлении JS/CSS и надёжно сбрасывает кэш.
            base_dir = Path(__file__).resolve().parent
            js_p = base_dir / "static" / "js" / "admin-app.js"
            css_p = base_dir / "static" / "css" / "admin.css"
            mt = int(max(js_p.stat().st_mtime, css_p.stat().st_mtime))
            return settings.app_version + f"-m{mt}"
        except Exception:
            return settings.app_version

    asset_ver = _asset_ver()
    response = templates.TemplateResponse(
        request,
        "admin.html",
        {"asset_ver": asset_ver},
    )
    # Чтобы браузер не держал устаревший HTML (Alpine/шаблон после деплоя).
    response.headers["Cache-Control"] = "no-store, max-age=0, must-revalidate"
    return response


@app.get("/hub", response_class=HTMLResponse, tags=["Admin Panel"])
@app.get("/executive-hub", response_class=HTMLResponse, tags=["Admin Panel"])
async def executive_hub_page(request: Request) -> HTMLResponse:
    """Отдельная owner-поверхность Executive Hub с переходом в админку."""
    def _asset_ver() -> str:
        git_sha_local = (os.environ.get("RENDER_GIT_COMMIT") or os.environ.get("GIT_COMMIT") or "").strip()
        sha7_local = git_sha_local[:7] if git_sha_local else ""
        if sha7_local:
            return settings.app_version + f"-{sha7_local}"
        try:
            base_dir = Path(__file__).resolve().parent
            js_p = base_dir / "static" / "js" / "admin-app.js"
            css_p = base_dir / "static" / "css" / "admin.css"
            mt = int(max(js_p.stat().st_mtime, css_p.stat().st_mtime))
            return settings.app_version + f"-m{mt}"
        except Exception:
            return settings.app_version

    response = templates.TemplateResponse(
        request,
        "hub.html",
        {"asset_ver": _asset_ver()},
    )
    response.headers["Cache-Control"] = "no-store, max-age=0, must-revalidate"
    return response


@app.get("/admin/_/components", response_class=HTMLResponse, include_in_schema=False)
async def admin_components_storybook(request: Request) -> HTMLResponse:
    """
    Витрина UI-компонентов (Jinja2 макросы + токены).
    Доступ: только в debug-режиме (в проде роут скрыт).
    """
    if not settings.app_debug:
        # Не раскрываем наличие страницы (security through obscurity).
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Not Found")

    # Используем ту же схему cache-busting, что и /admin.
    git_sha = (os.environ.get("RENDER_GIT_COMMIT") or os.environ.get("GIT_COMMIT") or "").strip()
    sha7 = git_sha[:7] if git_sha else ""
    if sha7:
        asset_ver = settings.app_version + f"-{sha7}"
    else:
        try:
            base_dir = Path(__file__).resolve().parent
            js_p = base_dir / "static" / "js" / "admin-app.js"
            css_p = base_dir / "static" / "css" / "admin.css"
            mt = int(max(js_p.stat().st_mtime, css_p.stat().st_mtime))
            asset_ver = settings.app_version + f"-m{mt}"
        except Exception:
            asset_ver = settings.app_version
    response = templates.TemplateResponse(
        request,
        "_components_storybook.html",
        {"asset_ver": asset_ver},
    )
    response.headers["Cache-Control"] = "no-store, max-age=0, must-revalidate"
    return response


@app.get("/onboarding", response_class=HTMLResponse, tags=["Admin Panel"])
async def onboarding_page(request: Request) -> HTMLResponse:
    """Onboarding self-serve отключён: ведём на форму заявки."""
    _ = request
    return RedirectResponse(url="/request-access", status_code=307)


@app.get("/request-access", response_class=HTMLResponse, tags=["Admin Panel"])
async def request_access_page(request: Request) -> HTMLResponse:
    """Форма заявки на подключение ресторана."""
    git_sha = (os.environ.get("RENDER_GIT_COMMIT") or os.environ.get("GIT_COMMIT") or "").strip()
    sha7 = git_sha[:7] if git_sha else ""
    asset_ver = settings.app_version + (f"-{sha7}" if sha7 else "")
    response = templates.TemplateResponse(
        request,
        "request_access.html",
        {"asset_ver": asset_ver},
    )
    response.headers["Cache-Control"] = "no-store, max-age=0, must-revalidate"
    return response


@app.get("/superadmin", response_class=HTMLResponse, tags=["Admin Panel"])
async def superadmin_page(request: Request) -> HTMLResponse:
    """Панель владельца платформы (доступ проверяется API-эндпоинтами)."""
    git_sha = (os.environ.get("RENDER_GIT_COMMIT") or os.environ.get("GIT_COMMIT") or "").strip()
    sha7 = git_sha[:7] if git_sha else ""
    if sha7:
        asset_ver = settings.app_version + f"-{sha7}"
    else:
        try:
            base_dir = Path(__file__).resolve().parent
            js_p = base_dir / "static" / "js" / "admin-app.js"
            css_p = base_dir / "static" / "css" / "admin.css"
            mt = int(max(js_p.stat().st_mtime, css_p.stat().st_mtime))
            asset_ver = settings.app_version + f"-m{mt}"
        except Exception:
            asset_ver = settings.app_version
    response = templates.TemplateResponse(
        request,
        "superadmin.html",
        {"asset_ver": asset_ver},
    )
    response.headers["Cache-Control"] = "no-store, max-age=0, must-revalidate"
    return response
