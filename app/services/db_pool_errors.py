"""Detect Postgres pooler exhaustion (Supabase EMAXCONNSESSION)."""

from __future__ import annotations


def is_postgres_pool_exhausted(exc: BaseException) -> bool:
    """True when managed pooler rejected a new session (Supabase ~15 cap on :5432)."""
    msg = str(exc).lower()
    if "emaxconnsession" in msg:
        return True
    if "max clients reached" in msg and "session mode" in msg:
        return True
    cause = getattr(exc, "__cause__", None) or getattr(exc, "orig", None)
    if cause is not None and cause is not exc:
        return is_postgres_pool_exhausted(cause)
    return False


POOL_EXHAUSTED_USER_MESSAGE = (
    "База данных временно перегружена (лимит соединений Supabase). "
    "Повторите через несколько секунд или переключите DATABASE_URL на transaction pooler :6543."
)
