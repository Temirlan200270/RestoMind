"""
Состояние интеграций для админки: последние успехи/ошибки синхронизации с iiko.
Глобальная строка (legacy) и отдельно по каждому филиалу (organization_integration_sync).
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete as sql_delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import IntegrationEvent, IntegrationHealth, OrganizationIntegrationSync

ROW_ID = 1
_MAX_EVENTS = 100


async def _get_or_create_row(db: AsyncSession) -> IntegrationHealth:
    row = await db.get(IntegrationHealth, ROW_ID)
    if row is None:
        row = IntegrationHealth(id=ROW_ID)
        db.add(row)
        await db.flush()
    return row


async def _get_or_create_org_row(db: AsyncSession, organization_id: int) -> OrganizationIntegrationSync:
    row = await db.get(OrganizationIntegrationSync, int(organization_id))
    if row is None:
        row = OrganizationIntegrationSync(organization_id=int(organization_id))
        db.add(row)
        await db.flush()
    return row


async def _append_integration_event(
    db: AsyncSession,
    kind: str,
    ok: bool,
    message: str,
    *,
    organization_id: int | None = None,
) -> None:
    """Добавить строку в журнал (с усечением хвоста при переполнении)."""
    db.add(
        IntegrationEvent(
            kind=kind[:40],
            ok=ok,
            message=(message or "")[:4000],
            organization_id=organization_id,
        )
    )
    await db.flush()
    n = await db.scalar(select(func.count()).select_from(IntegrationEvent))
    if n and n > _MAX_EVENTS:
        excess = int(n) - _MAX_EVENTS
        old_ids = await db.execute(
            select(IntegrationEvent.id)
            .order_by(IntegrationEvent.created_at.asc())
            .limit(excess)
        )
        ids = [row_t[0] for row_t in old_ids.all()]
        if ids:
            await db.execute(sql_delete(IntegrationEvent).where(IntegrationEvent.id.in_(ids)))


async def record_stoplist_sync(
    db: AsyncSession,
    ok: bool,
    error: str | None = None,
    *,
    detail: str | None = None,
    organization_id: int | None = None,
) -> None:
    """Зафиксировать результат синхронизации стоп-листов (фон или кнопка в админке)."""
    now = datetime.now(timezone.utc)
    msg = detail or (
        f"Стоп-листы: {'успех' if ok else 'ошибка'}"
        + (f" — {error[:300]}" if error and not ok else "")
    )
    if organization_id is not None:
        orow = await _get_or_create_org_row(db, int(organization_id))
        orow.last_stoplist_at = now
        orow.last_stoplist_ok = ok
        orow.last_stoplist_error = (error or "")[:2000]
    row = await _get_or_create_row(db)
    row.last_stoplist_at = now
    row.last_stoplist_ok = ok
    row.last_stoplist_error = (error or "")[:2000]
    await _append_integration_event(db, "stoplist_sync", ok, msg, organization_id=organization_id)


async def record_menu_sync(
    db: AsyncSession,
    ok: bool,
    error: str | None = None,
    *,
    detail: str | None = None,
    organization_id: int | None = None,
) -> None:
    """Зафиксировать результат синхронизации номенклатуры."""
    now = datetime.now(timezone.utc)
    msg = detail or (
        f"Меню: {'успех' if ok else 'ошибка'}"
        + (f" — {error[:300]}" if error and not ok else "")
    )
    if organization_id is not None:
        orow = await _get_or_create_org_row(db, int(organization_id))
        orow.last_menu_sync_at = now
        orow.last_menu_sync_ok = ok
        orow.last_menu_sync_error = (error or "")[:2000]
    row = await _get_or_create_row(db)
    row.last_menu_sync_at = now
    row.last_menu_sync_ok = ok
    row.last_menu_sync_error = (error or "")[:2000]
    await _append_integration_event(db, "menu_sync", ok, msg, organization_id=organization_id)


async def record_inventory_sync(
    db: AsyncSession,
    ok: bool,
    error: str | None = None,
    *,
    detail: str | None = None,
    organization_id: int | None = None,
) -> None:
    """Зафиксировать результат синхронизации остатков iiko Office."""
    now = datetime.now(timezone.utc)
    msg = detail or (
        f"Остатки iiko Office: {'успех' if ok else 'ошибка'}"
        + (f" — {error[:300]}" if error and not ok else "")
    )
    if organization_id is not None:
        orow = await _get_or_create_org_row(db, int(organization_id))
        orow.last_inventory_sync_at = now
        orow.last_inventory_sync_ok = ok
        orow.last_inventory_sync_error = (error or "")[:2000]
    await _append_integration_event(db, "inventory_sync", ok, msg, organization_id=organization_id)


async def list_integration_events(
    db: AsyncSession,
    limit: int = 40,
    *,
    organization_id: int | None = None,
) -> list[dict]:
    """Последние события для вкладки «Интеграции»."""
    lim = max(1, min(limit, 200))
    q = select(IntegrationEvent).order_by(IntegrationEvent.created_at.desc())
    if organization_id is not None:
        q = q.where(
            or_(
                IntegrationEvent.organization_id == organization_id,
                IntegrationEvent.organization_id.is_(None),
            ),
        )
    res = await db.execute(q.limit(lim))
    rows = res.scalars().all()
    return [
        {
            "id": r.id,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "kind": r.kind,
            "ok": bool(r.ok),
            "message": r.message or "",
        }
        for r in rows
    ]


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _neutral_slot() -> dict:
    return {"at": None, "ok": False, "error": None}


async def build_status_payload(
    db: AsyncSession,
    *,
    organization_id: int,
    iiko_configured: bool,
    whatsapp_configured: bool,
) -> dict:
    """JSON для GET /api/admin/integrations/status — слоты last_* по активному филиалу."""
    org_row = await db.get(OrganizationIntegrationSync, int(organization_id))
    if org_row is None:
        slot = _neutral_slot()
        return {
            "iiko_configured": iiko_configured,
            "whatsapp_configured": whatsapp_configured,
            "last_stoplist": {**slot},
            "last_menu_sync": {**slot},
            "last_inventory_sync": {**slot},
        }
    return {
        "iiko_configured": iiko_configured,
        "whatsapp_configured": whatsapp_configured,
        "last_stoplist": {
            "at": _iso(org_row.last_stoplist_at),
            "ok": bool(org_row.last_stoplist_ok),
            "error": org_row.last_stoplist_error or None,
        },
        "last_menu_sync": {
            "at": _iso(org_row.last_menu_sync_at),
            "ok": bool(org_row.last_menu_sync_ok),
            "error": org_row.last_menu_sync_error or None,
        },
        "last_inventory_sync": {
            "at": _iso(org_row.last_inventory_sync_at),
            "ok": bool(org_row.last_inventory_sync_ok),
            "error": org_row.last_inventory_sync_error or None,
        },
    }


async def build_inventory_sync_status(
    db: AsyncSession,
    *,
    organization_id: int,
    iiko_office_configured: bool,
    iiko_cloud_configured: bool,
) -> dict:
    """JSON для GET /api/admin/inventory/sync-status."""
    org_row = await db.get(OrganizationIntegrationSync, int(organization_id))
    slot = _neutral_slot()
    last = slot
    if org_row is not None:
        last = {
            "at": _iso(org_row.last_inventory_sync_at),
            "ok": bool(org_row.last_inventory_sync_ok),
            "error": org_row.last_inventory_sync_error or None,
        }
    return {
        "iiko_office_configured": iiko_office_configured,
        "iiko_cloud_configured": iiko_cloud_configured,
        "last_inventory_sync": last,
    }
