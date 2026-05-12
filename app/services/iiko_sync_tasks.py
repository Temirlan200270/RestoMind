"""
Переиспользуемые задачи синхронизации меню и стоп-листа iiko.

Вызываются как напрямую из ARQ worker, так и из фонового loop в main.py.
Инкапсулируют: resolve creds → sync → cache invalidation → record → WS event.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def run_stoplist_sync(org_id: int) -> dict[str, Any]:
    """
    Синхронизирует стоп-лист iiko для одной организации.
    Возвращает dict с результатом: ok, error, stats.
    """
    from app.db.session import async_session_factory
    from app.services.events import publish_event
    from app.services.integration_health import record_stoplist_sync
    from app.services.menu_sync import sync_stop_lists
    from app.services.order_logic import invalidate_menu_context_cache
    from app.services.org_iiko import resolve_org_iiko_credentials

    async with async_session_factory() as db:
        creds = await resolve_org_iiko_credentials(db, org_id)
        if creds is None:
            logger.warning("run_stoplist_sync: org_id=%s — iiko credentials not configured", org_id)
            return {"ok": False, "error": "iiko credentials not configured", "org_id": org_id}

        ok = False
        error = ""
        stats: dict[str, int] = {}
        try:
            stats = await sync_stop_lists(
                db,
                creds.api_login,
                creds.iiko_organization_id,
                terminal_group_id=creds.terminal_group_id or None,
                menu_organization_id=org_id,
            )
            ok = True
        except Exception as exc:
            error = str(exc)
            logger.warning("run_stoplist_sync org_id=%s failed: %s", org_id, exc)

        await record_stoplist_sync(
            db,
            ok=ok,
            error=error,
            detail=str(stats) if stats else None,
            organization_id=org_id,
        )
        await db.commit()

    invalidate_menu_context_cache(org_id)

    await publish_event("stoplist_updated", {
        "organization_id": org_id,
        "ok": ok,
        "stats": stats,
        "error": error or None,
    })

    logger.info("run_stoplist_sync org_id=%s ok=%s stats=%s", org_id, ok, stats)
    return {"ok": ok, "error": error, "stats": stats, "org_id": org_id}


async def run_menu_sync(org_id: int) -> dict[str, Any]:
    """
    Синхронизирует меню iiko для одной организации.
    Возвращает dict с результатом: ok, error, stats.
    """
    from app.db.session import async_session_factory
    from app.services.events import publish_event
    from app.services.integration_health import record_menu_sync
    from app.services.menu_sync import sync_menu_from_iiko
    from app.services.order_logic import invalidate_menu_context_cache
    from app.services.org_iiko import resolve_org_iiko_credentials

    async with async_session_factory() as db:
        creds = await resolve_org_iiko_credentials(db, org_id)
        if creds is None:
            logger.warning("run_menu_sync: org_id=%s — iiko credentials not configured", org_id)
            return {"ok": False, "error": "iiko credentials not configured", "org_id": org_id}

        ok = False
        error = ""
        stats: dict[str, Any] = {}
        try:
            stats = await sync_menu_from_iiko(
                db,
                creds.api_login,
                creds.iiko_organization_id,
                restomind_organization_id=org_id,
            )
            ok = True
        except Exception as exc:
            error = str(exc)
            logger.warning("run_menu_sync org_id=%s failed: %s", org_id, exc)

        await record_menu_sync(
            db,
            ok=ok,
            error=error,
            detail=str(stats) if stats else None,
            organization_id=org_id,
        )
        await db.commit()

    invalidate_menu_context_cache(org_id)

    await publish_event("menu_updated", {
        "organization_id": org_id,
        "ok": ok,
        "stats": stats,
        "error": error or None,
    })

    logger.info("run_menu_sync org_id=%s ok=%s stats=%s", org_id, ok, stats)
    return {"ok": ok, "error": error, "stats": stats, "org_id": org_id}
