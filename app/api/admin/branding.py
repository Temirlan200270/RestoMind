"""
E2.2 — Brand-настройки сети (Tenant.brand_*).

Сетевая (а не пер-филиальная) настройка: в одной арендной группе все филиалы
показывают одинаковый бренд в шапке админки. Контракт совместим с UI
``app/templates/screens/_tab_settings_branding.html`` и форматом
``GET /api/admin/auth/me → branding``.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import StaffUser, Tenant
from app.db.session import get_db
from app.services.tenant_scope import branding_empty_payload, resolve_active_tenant_id

from .deps import (
    _session_staff_user,
    admin_org_from_session,
    require_admin_session_active,
)

logger = logging.getLogger(__name__)

branding_router = APIRouter(dependencies=[Depends(require_admin_session_active)])

_BRAND_NAME_MAX = 255
_BRAND_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
_LOGO_MAX_BYTES = 1 * 1024 * 1024  # 1 MB — синхронизировано с подсказкой в UI
_LOGO_ALLOWED_MIME: dict[str, str] = {
    "image/png": "png",
    "image/jpeg": "jpg",
}
_LOGO_DIR = Path(__file__).resolve().parents[1] / "static" / "uploads" / "branding"
_LOGO_URL_PREFIX = "/static/uploads/branding"


def _normalize_brand_name(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    if not cleaned:
        return None
    if len(cleaned) > _BRAND_NAME_MAX:
        raise HTTPException(status_code=400, detail=f"brand_name длиннее {_BRAND_NAME_MAX} символов")
    return cleaned


def _normalize_brand_color(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    if not cleaned:
        return None
    if not _BRAND_COLOR_RE.match(cleaned):
        raise HTTPException(status_code=400, detail="brand_color_hex должен быть в формате #RRGGBB")
    return cleaned.lower()


def _branding_payload_from_tenant(t: Tenant | None) -> dict[str, Any | None]:
    if t is None:
        return branding_empty_payload()
    return {
        "brand_name": str(t.brand_name) if t.brand_name else None,
        "brand_color_hex": str(t.brand_color_hex) if t.brand_color_hex else None,
        "brand_logo_url": str(t.brand_logo_url) if t.brand_logo_url else None,
    }


async def _resolve_tenant_for_session(
    db: AsyncSession,
    *,
    staff: StaffUser | None,
    active_organization_id: int,
) -> Tenant:
    tenant_id = await resolve_active_tenant_id(
        db,
        staff=staff,
        active_organization_id=int(active_organization_id),
    )
    if tenant_id is None:
        raise HTTPException(
            status_code=409,
            detail="У филиала нет привязки к сети — сначала создайте Tenant в супер-админке.",
        )
    t = await db.get(Tenant, tenant_id)
    if t is None:
        raise HTTPException(status_code=404, detail="Tenant не найден")
    return t


class BrandingPatchBody(BaseModel):
    """PATCH /api/admin/branding — текстовые поля бренда (без логотипа)."""

    brand_name: str | None = Field(None, max_length=_BRAND_NAME_MAX)
    brand_color_hex: str | None = Field(None, max_length=9)


@branding_router.get("/branding")
async def get_branding(request: Request, db: AsyncSession = Depends(get_db)) -> dict[str, Any | None]:
    org_id = admin_org_from_session(request)
    staff = await _session_staff_user(request, db)
    t = await _resolve_tenant_for_session(db, staff=staff, active_organization_id=int(org_id))
    payload = _branding_payload_from_tenant(t)
    payload["tenant_id"] = int(t.id)
    return payload


@branding_router.patch("/branding")
async def patch_branding(
    request: Request,
    body: BrandingPatchBody,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any | None]:
    org_id = admin_org_from_session(request)
    staff = await _session_staff_user(request, db)
    t = await _resolve_tenant_for_session(db, staff=staff, active_organization_id=int(org_id))

    data = body.model_dump(exclude_unset=True)
    if "brand_name" in data:
        t.brand_name = _normalize_brand_name(data["brand_name"])
    if "brand_color_hex" in data:
        t.brand_color_hex = _normalize_brand_color(data["brand_color_hex"])
    await db.commit()
    await db.refresh(t)

    payload = _branding_payload_from_tenant(t)
    payload["tenant_id"] = int(t.id)
    return payload


@branding_router.post("/branding/logo")
async def upload_branding_logo(
    request: Request,
    file: Annotated[UploadFile, File(..., description="PNG или JPEG до 1 МБ")],
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any | None]:
    org_id = admin_org_from_session(request)
    staff = await _session_staff_user(request, db)
    t = await _resolve_tenant_for_session(db, staff=staff, active_organization_id=int(org_id))

    content_type = (file.content_type or "").strip().lower()
    ext = _LOGO_ALLOWED_MIME.get(content_type)
    if ext is None:
        raise HTTPException(status_code=415, detail="Допустимы только image/png и image/jpeg")

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Файл логотипа пустой")
    if len(raw) > _LOGO_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Файл логотипа больше 1 МБ")

    _LOGO_DIR.mkdir(parents=True, exist_ok=True)
    # Перезаписываем «единственный логотип арендатора» — старый файл удаляется самим mkdir-write.
    target = _LOGO_DIR / f"tenant-{int(t.id)}.{ext}"
    target.write_bytes(raw)

    cache_buster = int(datetime.now(timezone.utc).timestamp())
    new_url = f"{_LOGO_URL_PREFIX}/tenant-{int(t.id)}.{ext}?v={cache_buster}"
    t.brand_logo_url = new_url
    await db.commit()
    await db.refresh(t)

    payload = _branding_payload_from_tenant(t)
    payload["tenant_id"] = int(t.id)
    return payload
