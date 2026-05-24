"""Public demo entry (G10.8.2): zero-friction /demo URLs + rate limit."""

from __future__ import annotations

from fastapi import HTTPException

from app.core.config import settings
from app.core.rate_limiter import check_rate_limit_window
from app.services.demo_shift_scene import (
    DEMO_SCENE_BOOKING_RESCUE_30S,
    DEMO_SCENE_MONEY_RESCUE_30S,
)

# URL slug → internal scene id
PUBLIC_DEMO_SCENE_SLUGS: dict[str, str] = {
    "money": DEMO_SCENE_MONEY_RESCUE_30S,
    "": DEMO_SCENE_MONEY_RESCUE_30S,
    "chat": DEMO_SCENE_MONEY_RESCUE_30S,
    "booking": DEMO_SCENE_BOOKING_RESCUE_30S,
    "bookings": DEMO_SCENE_BOOKING_RESCUE_30S,
}

DEFAULT_PUBLIC_DEMO_SCENE = DEMO_SCENE_MONEY_RESCUE_30S


def public_demo_enabled() -> bool:
    """True when GET /demo is allowed (prod: DEMO_PUBLIC_ENABLED=true)."""
    if settings.demo_public_enabled:
        return True
    return bool(settings.app_debug)


def require_public_demo_enabled() -> None:
    if not public_demo_enabled():
        raise HTTPException(status_code=404, detail="Not Found")


def resolve_public_demo_scene_id(slug: str | None) -> str:
    key = str(slug or "").strip().lower()
    scene_id = PUBLIC_DEMO_SCENE_SLUGS.get(key)
    if scene_id is None:
        raise HTTPException(status_code=404, detail="Demo scene not found")
    allowed = settings.demo_public_scenes_list()
    if allowed and scene_id not in allowed:
        raise HTTPException(status_code=404, detail="Demo scene not found")
    return scene_id


async def check_demo_public_rate_limit(client_ip: str) -> bool:
    """Hourly cap per IP for GET /demo (abuse protection)."""
    ip = (client_ip or "unknown").strip() or "unknown"
    key = f"demo_public:{ip}"
    return await check_rate_limit_window(
        key,
        limit=int(settings.demo_rate_limit_per_hour),
        window_seconds=3600,
    )
