"""Pool sizing for managed Postgres (Supabase session limit)."""

from app.db.pool_settings import (
    is_supabase_session_pooler,
    is_supabase_transaction_pooler,
    resolve_postgres_pool_settings,
)


def test_supabase_session_pooler_detected():
    url = "postgresql://postgres.x:pass@aws-0-eu.pooler.supabase.com:5432/postgres"
    assert is_supabase_session_pooler(url) is True
    assert is_supabase_transaction_pooler(url) is False


def test_supabase_transaction_pooler_detected():
    url = "postgresql://postgres.x:pass@aws-0-eu.pooler.supabase.com:6543/postgres"
    assert is_supabase_transaction_pooler(url) is True
    assert is_supabase_session_pooler(url) is False


def test_session_pooler_uses_small_pool_by_default():
    url = "postgresql://postgres.x:pass@aws-0-eu.pooler.supabase.com:5432/postgres"
    cfg = resolve_postgres_pool_settings(url)
    assert cfg.pool_size == 1
    assert cfg.max_overflow == 0
    assert cfg.pool_size + cfg.max_overflow <= 1


def test_explicit_pool_size_overrides_auto():
    url = "postgresql://postgres.x:pass@aws-0-eu.pooler.supabase.com:5432/postgres"
    cfg = resolve_postgres_pool_settings(url, pool_size=2, max_overflow=0)
    assert cfg.pool_size == 2
    assert cfg.max_overflow == 0


def test_generic_postgres_moderate_defaults():
    url = "postgresql://user:pass@db.example.com:5432/restomind"
    cfg = resolve_postgres_pool_settings(url)
    assert cfg.pool_size == 10
    assert cfg.max_overflow == 5


def test_rewrite_session_pooler_to_transaction():
    from app.db.pool_settings import rewrite_supabase_session_to_transaction_pooler

    url = "postgresql://postgres.x:pass@aws-0-eu.pooler.supabase.com:5432/postgres"
    out = rewrite_supabase_session_to_transaction_pooler(url)
    assert ":6543/" in out
    assert ":5432/" not in out


def test_settings_supabase_prefer_transaction_pooler(monkeypatch):
    from app.core.config import Settings

    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://postgres.x:pass@aws-0-eu.pooler.supabase.com:5432/postgres",
    )
    monkeypatch.setenv("SUPABASE_PREFER_TRANSACTION_POOLER", "true")
    s = Settings()
    assert ":6543/" in s.database_url
