"""Настройки SQLAlchemy pool для Postgres с учётом лимитов managed pooler (Supabase и др.)."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class PostgresPoolSettings:
    pool_size: int
    max_overflow: int
    pool_timeout: int
    pool_recycle: int
    pool_pre_ping: bool = True


def _parsed_host_port(database_url: str) -> tuple[str, int]:
    parsed = urlparse(database_url)
    host = (parsed.hostname or "").lower()
    port = parsed.port or 5432
    return host, port


def is_supabase_session_pooler(database_url: str) -> bool:
    """Supabase pooler :5432 — session mode, жёсткий лимит одновременных клиентов (~15 на план)."""
    host, port = _parsed_host_port(database_url)
    return "pooler.supabase.com" in host and port == 5432


def is_supabase_transaction_pooler(database_url: str) -> bool:
    """Supabase pooler :6543 — transaction mode (PgBouncer), больше соединений."""
    host, port = _parsed_host_port(database_url)
    return "pooler.supabase.com" in host and port == 6543


def resolve_postgres_pool_settings(
    database_url: str,
    *,
    pool_size: int = 0,
    max_overflow: int = -1,
    pool_timeout: int = 30,
    pool_recycle: int = 1800,
) -> PostgresPoolSettings:
    """
    Возвращает безопасные параметры пула.

    pool_size=0 и max_overflow=-1 — авто по DSN (Supabase session → малый пул).
    """
    if pool_size > 0:
        resolved_size = pool_size
    elif is_supabase_session_pooler(database_url):
        # Web + ARQ worker + overlap при деплое: жёсткий потолок 2 conn/проц.
        resolved_size = 2
    elif is_supabase_transaction_pooler(database_url):
        resolved_size = 8
    else:
        resolved_size = 10

    if max_overflow >= 0:
        resolved_overflow = max_overflow
    elif is_supabase_session_pooler(database_url):
        resolved_overflow = 0
    elif is_supabase_transaction_pooler(database_url):
        resolved_overflow = 4
    else:
        resolved_overflow = 5

    return PostgresPoolSettings(
        pool_size=resolved_size,
        max_overflow=resolved_overflow,
        pool_timeout=max(1, pool_timeout),
        pool_recycle=max(60, pool_recycle),
    )
