"""In-process cache for demo org id — demo-login без лишних запросов к Postgres."""

from __future__ import annotations

_cached_demo_org_id: int | None = None


def set_cached_demo_org_id(org_id: int | None) -> None:
    global _cached_demo_org_id
    if org_id is not None and int(org_id) > 0:
        _cached_demo_org_id = int(org_id)


def get_cached_demo_org_id() -> int | None:
    return _cached_demo_org_id


def clear_cached_demo_org_id() -> None:
    global _cached_demo_org_id
    _cached_demo_org_id = None


def resolve_demo_org_id_from_settings() -> int | None:
    from app.core.config import settings

    env_id = int(getattr(settings, "demo_organization_id", 0) or 0)
    if env_id > 0:
        return env_id
    cached = get_cached_demo_org_id()
    if cached is not None and cached > 0:
        return cached
    return None
