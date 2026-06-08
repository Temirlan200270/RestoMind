"""
 Асинхронное подключение к PostgreSQL и Redis (опционально).
Фабрика сессий для Dependency Injection в FastAPI.
"""

import logging
import os
import ssl
from collections.abc import AsyncGenerator
from typing import Any
from urllib.parse import urlparse, urlunparse

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.db.pool_settings import resolve_postgres_pool_settings
from app.db.ssl_context import postgres_connect_args
from app.db.tenant_rls import apply_tenant_rls

logger = logging.getLogger(__name__)

# --- БД (async) — PostgreSQL ---
engine_kwargs: dict[str, Any] = {
    "echo": settings.app_debug,
}

if settings.db_mode == "postgres":
    if os.environ.get("RESTOMIND_TEST_POSTGRES") == "true":
        engine_kwargs["poolclass"] = NullPool
        pool_cfg = None
    else:
        pool_cfg = resolve_postgres_pool_settings(
            settings.database_url,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_timeout=settings.db_pool_timeout,
            pool_recycle=settings.db_pool_recycle,
        )
        engine_kwargs.update(
            pool_size=pool_cfg.pool_size,
            max_overflow=pool_cfg.max_overflow,
            pool_timeout=pool_cfg.pool_timeout,
            pool_recycle=pool_cfg.pool_recycle,
            pool_pre_ping=pool_cfg.pool_pre_ping,
        )
    # Не полагаемся на `?sslmode=` в DATABASE_URL (SQLAlchemy/asyncpg иногда превращают это в kwargs).
    # TLS включаем явно через connect_args.
    engine_kwargs["connect_args"] = postgres_connect_args(settings.database_url)
    from app.db.pool_settings import is_supabase_session_pooler, is_supabase_transaction_pooler

    if pool_cfg is not None:
        if is_supabase_transaction_pooler(settings.database_url):
            logger.info("Supabase transaction pooler :6543 — higher client limit OK")
        elif is_supabase_session_pooler(settings.database_url):
            logger.warning(
                "Supabase session pooler detected — pool capped at %s+%s per process "
                "(project limit ~15). Prefer transaction pooler :6543 or set DB_POOL_SIZE.",
                pool_cfg.pool_size,
                pool_cfg.max_overflow,
            )
        logger.info(
            "Postgres pool: size=%s max_overflow=%s timeout=%ss recycle=%ss",
            pool_cfg.pool_size,
            pool_cfg.max_overflow,
            pool_cfg.pool_timeout,
            pool_cfg.pool_recycle,
        )

async_engine = create_async_engine(settings.database_url, **engine_kwargs)


async_session_factory = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency — асинхронная сессия БД. Автоматически закрывается после запроса."""
    async with async_session_factory() as session:
        await apply_tenant_rls(session)
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# --- Redis (опционально) ---
# Если Redis отключён — используем простое in-memory хранилище

class InMemoryRedis:
    """
    Заглушка Redis для локальной разработки без Redis-сервера.
    Хранит данные в оперативной памяти. Поддерживает базовые операции.
    """

    def __init__(self) -> None:
        self._store: dict[str, list[str]] = {}
        self._kv: dict[str, str] = {}
        self._sets: dict[str, set[str]] = {}

    async def ping(self) -> bool:
        return True

    async def get(self, key: str) -> str | None:
        return self._kv.get(key)

    async def set(
        self,
        key: str,
        value: str,
        ex: int | None = None,
        nx: bool = False,
    ) -> bool | None:
        if nx and key in self._kv:
            return None
        self._kv[key] = value
        return True

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self._kv[key] = value

    async def sadd(self, key: str, *values: str) -> int:
        bucket = self._sets.setdefault(key, set())
        before = len(bucket)
        for v in values:
            bucket.add(v)
        return len(bucket) - before

    async def smembers(self, key: str) -> list[str]:
        return list(self._sets.get(key, set()))

    async def srem(self, key: str, *values: str) -> int:
        bucket = self._sets.get(key, set())
        removed = 0
        for v in values:
            if v in bucket:
                bucket.remove(v)
                removed += 1
        return removed

    async def lrange(self, key: str, start: int, end: int) -> list[str]:
        data = self._store.get(key, [])
        if end == -1:
            return data[start:]
        return data[start:end + 1]

    async def rpush(self, key: str, value: str) -> int:
        if key not in self._store:
            self._store[key] = []
        self._store[key].append(value)
        return len(self._store[key])

    async def lpop(self, key: str) -> str | None:
        items = self._store.get(key, [])
        if not items:
            return None
        return items.pop(0)

    async def llen(self, key: str) -> int:
        return len(self._store.get(key, []))

    async def incr(self, key: str) -> int:
        raw = self._kv.get(key, "0")
        try:
            val = int(raw) + 1
        except ValueError:
            val = 1
        self._kv[key] = str(val)
        return val

    async def ltrim(self, key: str, start: int, end: int) -> None:
        if key in self._store:
            self._store[key] = self._store[key][start:] if end == -1 else self._store[key][start:end + 1]

    async def expire(self, key: str, ttl: int) -> None:
        pass

    async def delete(self, key: str) -> None:
        self._store.pop(key, None)
        self._kv.pop(key, None)

    async def scan(self, cursor: int, match: str | None = None, count: int = 64) -> tuple[int, list[str]]:
        prefix = (match or "").replace("*", "")
        keys = [k for k in self._kv if k.startswith(prefix)]
        return 0, keys

    async def aclose(self) -> None:
        pass

    def pipeline(self) -> "InMemoryPipeline":
        return InMemoryPipeline(self)


class InMemoryPipeline:
    """Эмуляция Redis pipeline для in-memory хранилища."""

    def __init__(self, store: InMemoryRedis) -> None:
        self._store = store
        self._commands: list[tuple[str, tuple]] = []

    def rpush(self, key: str, value: str) -> "InMemoryPipeline":
        self._commands.append(("rpush", (key, value)))
        return self

    def ltrim(self, key: str, start: int, end: int) -> "InMemoryPipeline":
        self._commands.append(("ltrim", (key, start, end)))
        return self

    def expire(self, key: str, ttl: int) -> "InMemoryPipeline":
        self._commands.append(("expire", (key, ttl)))
        return self

    async def execute(self) -> list:
        results = []
        for cmd, args in self._commands:
            method = getattr(self._store, cmd)
            result = await method(*args)
            results.append(result)
        return results


def redacted_redis_url_for_logs(url: str) -> str:
    """URL для логов без пароля (rediss://default:secret@host → rediss://***@host:6379)."""
    try:
        p = urlparse(url)
        if not p.scheme:
            return "(redis)"
        host = p.hostname or ""
        port = f":{p.port}" if p.port else ""
        if p.username is not None or p.password is not None:
            netloc = f"***@{host}{port}"
        else:
            netloc = f"{host}{port}" if host or port else p.netloc
        return urlunparse((p.scheme, netloc, p.path or "", "", p.query, p.fragment))
    except Exception:
        return "(redis)"


def redis_connection_kwargs() -> dict[str, Any]:
    """
    Параметры redis.asyncio для облачного TLS (Upstash и др.).
    REST Upstash (HTTPS + токен) Pub/Sub не поддерживает — нужен rediss:// и redis-py.
    """
    url = settings.redis_url
    kw: dict[str, Any] = {
        "decode_responses": True,
        "socket_connect_timeout": 5.0,
        "socket_timeout": 5.0,
        "retry_on_timeout": True,
    }
    if url.startswith("rediss://") and settings.redis_ssl_skip_verify:
        kw["ssl_cert_reqs"] = ssl.CERT_NONE
    return kw


def _create_redis_client() -> Any:
    """Создаёт Redis-клиент или in-memory заглушку."""
    if settings.redis_memory_only:
        logger.info(
            "REDIS_MEMORY_ONLY=true — внешний Redis не используется (in-memory, без лимитов провайдера)."
        )
        return InMemoryRedis()
    if settings.redis_enabled:
        from redis.asyncio import Redis

        logger.info("Redis включён: %s", redacted_redis_url_for_logs(settings.redis_url))
        return Redis.from_url(settings.redis_url, **redis_connection_kwargs())

    logger.info("Redis отключён — используется in-memory хранилище")
    return InMemoryRedis()


redis_client = _create_redis_client()


def redis_pubsub_available() -> bool:
    """
    True — реальный TCP Redis (Pub/Sub, ZSET для rate limit).
    False — REDIS выключен или откат на InMemoryRedis после ошибки подключения.
    """
    if not settings.redis_enabled:
        return False
    return not isinstance(redis_client, InMemoryRedis)


async def init_redis_or_fallback() -> None:
    """
    Проверка соединения при старте. Если REDIS_ENABLED=true, а хост недоступен
    (типично: на Render забыли REDIS_URL → остаётся localhost:6379), переключаемся на InMemoryRedis.
    """
    global redis_client
    if settings.redis_memory_only:
        logger.info("Redis: режим только память (REDIS_MEMORY_ONLY) — пропуск проверки сети")
        return
    if not settings.redis_enabled:
        logger.info("Redis отключён в настройках — in-memory")
        return
    if isinstance(redis_client, InMemoryRedis):
        return
    try:
        await redis_client.ping()
        logger.info("Redis подключён: %s", redacted_redis_url_for_logs(settings.redis_url))
    except Exception as exc:
        logger.error(
            "REDIS_ENABLED=true, но подключение к Redis не удалось: %s. "
            "Задайте REDIS_URL (Upstash: rediss://default:ПАРОЛЬ@…upstash.io:6379) "
            "или установите REDIS_ENABLED=false. Временно используется in-memory.",
            exc,
        )
        try:
            await redis_client.aclose()
        except Exception:
            pass
        redis_client = InMemoryRedis()


async def get_redis() -> Any:
    """Dependency — клиент Redis (или in-memory заглушка)."""
    return redis_client
