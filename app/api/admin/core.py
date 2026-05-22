"""Composite admin router: domain sub-routers (E0.1 tail)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from .bookings import bookings_router
from .branding import branding_router
from .chats import chats_router
from .customers import customers_router
from .demo import demo_router
from .deps import (
    require_admin_session,  # noqa: F401 - re-exported from app.api.admin for compatibility
    require_admin_session_active,
)
from .export import export_router
from .knowledge import knowledge_router
from .menu_bulk import menu_bulk_router
from .settings_ops import settings_ops_router
from .system import system_router

router = APIRouter(
    prefix="/admin",
    tags=["Admin Panel"],
    dependencies=[Depends(require_admin_session_active)],
)

router.include_router(demo_router)
router.include_router(settings_ops_router)
router.include_router(export_router)
router.include_router(menu_bulk_router)
router.include_router(knowledge_router)
router.include_router(branding_router)
router.include_router(bookings_router)
router.include_router(customers_router)
router.include_router(chats_router)
router.include_router(system_router)
# NOTE: rules_router, analytics_router, menu_router, organization_router, orders_router
# have their own prefix="/admin" — they are mounted directly in app.main at /api level.
