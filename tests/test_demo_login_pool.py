"""Demo login cache and pool exhaustion helpers."""

from app.services.db_pool_errors import is_postgres_pool_exhausted
from app.services.demo_login_cache import (
    clear_cached_demo_org_id,
    resolve_demo_org_id_from_settings,
    set_cached_demo_org_id,
)


def test_demo_org_cache_roundtrip() -> None:
    clear_cached_demo_org_id()
    assert resolve_demo_org_id_from_settings() is None
    set_cached_demo_org_id(42)
    assert resolve_demo_org_id_from_settings() == 42
    clear_cached_demo_org_id()


def test_pool_exhausted_detection() -> None:
    class FakeExc(Exception):
        pass

    inner = FakeExc("(EMAXCONNSESSION) max clients reached in session mode")
    outer = FakeExc("connection failed")
    outer.__cause__ = inner
    assert is_postgres_pool_exhausted(outer) is True
    assert is_postgres_pool_exhausted(FakeExc("other error")) is False
