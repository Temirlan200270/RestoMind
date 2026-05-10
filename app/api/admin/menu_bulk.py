"""Массовые операции по меню (E0.1: вынесено из монолита)."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin.deps import admin_org_from_session, require_admin_session_active, _menu_item_in_org
from app.api.admin.menu_schemas import MenuBulkStoplistBody
from app.db.session import get_db
from app.services.order_logic import invalidate_menu_context_cache

logger = logging.getLogger(__name__)

menu_bulk_router = APIRouter(dependencies=[Depends(require_admin_session_active)])


@menu_bulk_router.post("/menu/bulk-stoplist")
async def bulk_menu_stoplist(
    request: Request,
    body: MenuBulkStoplistBody,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Пакет: стоп / снять стоп / сменить раздел (строка category как в menu_items).
    Per-item ошибки — в теле ответа; чужие позиции — not_found.
    """
    org_id = admin_org_from_session(request)
    failed: list[dict[str, Any]] = []
    updated = 0
    unique_item_ids = list(dict.fromkeys(int(item_id) for item_id in body.item_ids))

    for item_id in unique_item_ids:
        try:
            item = await _menu_item_in_org(db, item_id, org_id)
        except HTTPException:
            failed.append({"id": item_id, "error": "not_found"})
            continue

        if body.action == "stop":
            item.is_available = False
        elif body.action == "unstop":
            item.is_available = True
        else:
            item.category = body.category or ""
        updated += 1

    if updated:
        await db.flush()
        invalidate_menu_context_cache(org_id)

    return {"ok": True, "updated": updated, "failed": failed}
