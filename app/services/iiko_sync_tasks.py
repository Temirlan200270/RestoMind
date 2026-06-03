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
    Синхронизирует стоп-лист POS для одной организации.
    Возвращает dict с результатом: ok, error, stats.
    """
    import app.services.pos.adapters  # noqa: F401 — register adapters

    from app.db.session import async_session_factory
    from app.services.events import publish_event
    from app.services.integration_health import record_stoplist_sync
    from app.services.order_logic import invalidate_menu_context_cache
    from app.services.pos.adapters.base import get_pos_adapter

    async with async_session_factory() as db:
        try:
            adapter = await get_pos_adapter(db, org_id)
        except ValueError as exc:
            logger.warning("run_stoplist_sync: org_id=%s — %s", org_id, exc)
            return {"ok": False, "error": str(exc), "org_id": org_id}

        ok = False
        error = ""
        stats: dict[str, int] = {}
        try:
            stats = await adapter.sync_stoplist(db, org_id)
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
    Синхронизирует меню POS для одной организации.
    Возвращает dict с результатом: ok, error, stats.
    """
    import app.services.pos.adapters  # noqa: F401 — register adapters

    from app.db.session import async_session_factory
    from app.services.events import publish_event
    from app.services.integration_health import record_menu_sync
    from app.services.order_logic import invalidate_menu_context_cache
    from app.services.pos.adapters.base import get_pos_adapter

    async with async_session_factory() as db:
        try:
            adapter = await get_pos_adapter(db, org_id)
        except ValueError as exc:
            logger.warning("run_menu_sync: org_id=%s — %s", org_id, exc)
            return {"ok": False, "error": str(exc), "org_id": org_id}

        ok = False
        error = ""
        stats: dict[str, Any] = {}
        try:
            if hasattr(adapter, "sync_menu_replace"):
                stats = await adapter.sync_menu_replace(
                    db,
                    org_id,
                    prune_missing=True,
                    prune_mode="archive",
                    prune_legacy=True,
                )
            else:
                stats = await adapter.sync_menu(db, org_id)
            ok = True
            from app.services.organization_memory import record_memory_event

            await record_memory_event(
                db,
                org_id,
                event_type="menu_import",
                entity_type="menu",
                summary=(
                    f"Menu sync completed: created={stats.get('created', 0)}, "
                    f"updated={stats.get('updated', 0)}, "
                    f"archived={stats.get('archived', 0)}, deleted={stats.get('deleted', 0)}, "
                    f"total={stats.get('total', 0)}."
                ),
                payload={"stats": stats},
                source="iiko_sync",
                confidence_score=1.0,
            )
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


async def run_inventory_sync(org_id: int) -> dict[str, Any]:
    """
    Синхронизирует остатки iiko Office для одной организации.
    Возвращает dict: ok, error, stats, org_id.
    """
    from app.db.session import async_session_factory
    from app.services.events import publish_event
    from app.services.integration_health import record_inventory_sync
    from app.services.iiko_inventory_sync import sync_inventory_from_iiko_office
    from app.services.org_iiko_office import resolve_org_iiko_office_credentials

    async with async_session_factory() as db:
        creds = await resolve_org_iiko_office_credentials(db, org_id)
        if creds is None:
            logger.warning(
                "run_inventory_sync: org_id=%s — iiko Office credentials not configured",
                org_id,
            )
            return {
                "ok": False,
                "error": "iiko Office credentials not configured",
                "org_id": org_id,
            }

        ok = False
        error = ""
        stats: dict[str, Any] = {}
        try:
            stats = await sync_inventory_from_iiko_office(db, org_id, creds=creds)
            ok = True
        except Exception as exc:
            error = str(exc)
            logger.warning("run_inventory_sync org_id=%s failed: %s", org_id, exc)

        await record_inventory_sync(
            db,
            ok=ok,
            error=error,
            detail=str(stats) if stats else None,
            organization_id=org_id,
        )
        await db.commit()

    await publish_event("inventory_updated", {
        "organization_id": org_id,
        "ok": ok,
        "stats": stats,
        "error": error or None,
    })

    logger.info("run_inventory_sync org_id=%s ok=%s stats=%s", org_id, ok, stats)
    return {"ok": ok, "error": error, "stats": stats, "org_id": org_id}


async def run_full_iiko_sync_for_org(org_id: int) -> None:
    """Полная ручная синхронизация: номенклатура, затем стоп-листы (для BackgroundTasks)."""
    await run_menu_sync(org_id)
    await run_stoplist_sync(org_id)
