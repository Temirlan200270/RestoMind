"""Demo data seed/clear for admin (E0.1 tail). G10.8 — scripted shift demo scenes."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db
from app.services.demo_data import clear_demo_data, demo_data_exists, seed_demo_data
from app.services.demo_shift_scene import build_demo_shift_state, list_demo_shift_scenes

from .deps import admin_org_from_session, require_admin_session_active

logger = logging.getLogger(__name__)

demo_router = APIRouter(dependencies=[Depends(require_admin_session_active)])


def _require_demo_scene_access(request: Request) -> None:
    """Demo scenes: demo-login session or local APP_DEBUG."""
    if bool(request.session.get("is_demo")) or settings.app_debug:
        return
    raise HTTPException(
        status_code=403,
        detail="Сценарий демо доступен после «Попробовать демо» или при APP_DEBUG=true",
    )


@demo_router.get("/demo/status")
async def demo_status(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    """Есть ли в БД пакет демо-пользователей (префикс телефона)."""
    oid = admin_org_from_session(request)
    return {"has_demo": await demo_data_exists(db, organization_id=oid)}


@demo_router.post("/demo/seed")
async def demo_seed(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    """Заполнить БД фальшивыми заказами, бронями и чатами (идемпотентно)."""
    oid = admin_org_from_session(request)
    stats = await seed_demo_data(db, organization_id=oid)
    if stats.get("skipped"):
        menu_n = int(stats.get("menu_items_added") or 0)
        if menu_n > 0:
            return {
                "ok": True,
                "partial": True,
                "message": "Демо-клиенты уже в БД; добавлено меню (позиций не было).",
                "menu_items_added": menu_n,
            }
        raise HTTPException(
            status_code=409,
            detail="Демо-данные уже есть. Сначала удалите их кнопкой «Удалить демо».",
        )
    return {"ok": True, **{k: v for k, v in stats.items() if k != "skipped"}}


async def _demo_delete_core(db: AsyncSession, organization_id: int) -> dict:
    """Общая логика удаления демо (БД + Redis-ключи сессий)."""
    if not await demo_data_exists(db, organization_id=organization_id):
        raise HTTPException(status_code=404, detail="Демо-данных нет")
    cleared = await clear_demo_data(db, organization_id=organization_id)
    return {"ok": True, **cleared}


@demo_router.delete("/demo")
async def demo_delete(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    """Удалить всех демо-пользователей и связанные заказы/брони/логи."""
    return await _demo_delete_core(db, admin_org_from_session(request))


@demo_router.post("/demo/delete")
async def demo_delete_post(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    """
    То же, что DELETE /admin/demo.
    Нужен для сред, где HTTP DELETE режется прокси/CDN (удаление «не работает», а POST проходит).
    """
    return await _demo_delete_core(db, admin_org_from_session(request))


@demo_router.get("/demo/shift-scenes")
async def demo_shift_scenes_list(request: Request) -> dict[str, Any]:
    """G10.8: каталог scripted demo-сцен для 20–30 сек pitch."""
    _require_demo_scene_access(request)
    return {"ok": True, "scenes": list_demo_shift_scenes()}


@demo_router.get("/demo/shift-scene/{scene_id}/state")
async def demo_shift_scene_state(
    request: Request,
    scene_id: str,
    phase: Annotated[str, Query(description="hook|tension|action|impact|next")] = "hook",
) -> dict[str, Any]:
    """G10.8: canned GET /shift/state для фазы сценки (без мутаций БД)."""
    _require_demo_scene_access(request)
    org_id = admin_org_from_session(request)
    try:
        payload = build_demo_shift_state(scene_id, phase, org_id=org_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return payload
