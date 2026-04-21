"""
Конфигурация Alembic для асинхронных миграций.
Использует asyncpg и модели из app.db.models.
"""

import asyncio
import os
import ssl
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.core.config import settings
from app.db.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Подставляем реальный DSN из конфига приложения
config.set_main_option("sqlalchemy.url", settings.database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Генерация SQL-скрипта без подключения к БД."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Запуск миграций через асинхронный движок."""
    url = settings.database_url
    connect_args = {}
    # Supabase / managed Postgres обычно требуют TLS. asyncpg принимает `ssl` как bool/SSLContext.
    # В норме используем системный trust store (verify-full). Если у окружения нет CA цепочки,
    # лучше чинить trust store, а не отключать верификацию.
    if url.startswith("postgresql"):
        ctx = ssl.create_default_context()
        try:
            import certifi

            ctx.load_verify_locations(certifi.where())
        except Exception:
            pass
        if (os.environ.get("ALEMBIC_SSL_NO_VERIFY") or "").strip().lower() in {"1", "true", "yes"}:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        connect_args = {"ssl": ctx}
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args=connect_args,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Точка входа для онлайн-миграций (асинхронный режим)."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
