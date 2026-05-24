"""G10.8.2 — public demo landing: GET /demo → session + autoplay redirect."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin.auth import establish_demo_session
from app.db.session import get_db
from app.services.db_pool_errors import POOL_EXHAUSTED_USER_MESSAGE, is_postgres_pool_exhausted
from app.services.demo_public import (
    check_demo_public_rate_limit,
    require_public_demo_enabled,
    resolve_public_demo_scene_id,
)

logger = logging.getLogger(__name__)

demo_public_router = APIRouter(tags=["Demo Public"])


@demo_public_router.get("/demo")
@demo_public_router.get("/demo/{scene_slug}")
async def public_demo_entry(
    request: Request,
    scene_slug: str = "money",
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Zero-friction sales demo: cookie session + redirect to admin pitch."""
    require_public_demo_enabled()
    client_ip = request.client.host if request.client else "unknown"
    if not await check_demo_public_rate_limit(client_ip):
        raise HTTPException(
            status_code=429,
            detail="Слишком много запросов демо. Попробуйте через час.",
        )
    scene_id = resolve_public_demo_scene_id(scene_slug)
    try:
        await establish_demo_session(request, db)
    except HTTPException:
        raise
    except Exception as exc:
        if is_postgres_pool_exhausted(exc):
            logger.warning("public /demo pool exhausted: %s", exc)
            raise HTTPException(status_code=503, detail=POOL_EXHAUSTED_USER_MESSAGE) from exc
        raise
    target = f"/admin?demo=1&demo_scene={scene_id}#shift"
    return RedirectResponse(url=target, status_code=302)
