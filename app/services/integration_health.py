"""
Состояние интеграций для админки: последние успехи/ошибки синхронизации с iiko.
Глобальная строка (legacy) и отдельно по каждому филиалу (organization_integration_sync).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete as sql_delete, func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import IikoSyncRun, IntegrationEvent, IntegrationHealth, Organization, OrganizationIntegrationSync

logger = logging.getLogger(__name__)

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
    slot = _neutral_slot()
    neutral = {
        "iiko_configured": iiko_configured,
        "whatsapp_configured": whatsapp_configured,
        "last_stoplist": {**slot},
        "last_menu_sync": {**slot},
        "last_inventory_sync": {**slot},
    }
    try:
        org_row = await db.get(OrganizationIntegrationSync, int(organization_id))
    except SQLAlchemyError as exc:
        logger.warning(
            "organization_integration_sync read failed org=%s: %s",
            organization_id,
            exc,
        )
        return neutral
    if org_row is None:
        olap_status = await _build_olap_status(db, organization_id=organization_id)
        neutral["olap"] = olap_status
        return neutral
    payload = {
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
            "at": _iso(getattr(org_row, "last_inventory_sync_at", None)),
            "ok": bool(getattr(org_row, "last_inventory_sync_ok", False)),
            "error": getattr(org_row, "last_inventory_sync_error", None) or None,
        },
    }
    payload["olap"] = await _build_olap_status(db, organization_id=organization_id)
    return payload


async def _build_olap_status(db: AsyncSession, *, organization_id: int) -> dict:
    org = await db.get(Organization, int(organization_id))
    latest = (
        await db.execute(
            select(IikoSyncRun)
            .where(
                IikoSyncRun.organization_id == int(organization_id),
                IikoSyncRun.sync_kind == "sales_olap_iiko",
            )
            .order_by(IikoSyncRun.finished_at.desc(), IikoSyncRun.id.desc())
            .limit(1),
        )
    ).scalar_one_or_none()
    from app.services.iiko_sales_factory import org_sales_data_source

    configured_source = org_sales_data_source(org)
    error = (latest.error_text or "") if latest is not None else ""
    olap_allowed = None
    if latest is not None:
        olap_allowed = not ("olap_not_allowed" in error or "reports/olap is not allowed" in error)
    freshness = "never"
    if latest is not None and latest.finished_at is not None:
        age = datetime.now(tz=timezone.utc) - _as_aware_utc(latest.finished_at)
        freshness = "fresh" if age <= timedelta(hours=3) and latest.status == "ok" else "stale"
    return {
        "source": "server" if configured_source == "server" else "cloud",
        "server": configured_source == "server",
        "olap_allowed": olap_allowed,
        "deliveries_fallback": olap_allowed is False,
        "sync_freshness": freshness,
        "last_sync": {
            "at": _iso(latest.finished_at) if latest is not None else None,
            "ok": latest.status == "ok" if latest is not None else False,
            "rows": int(latest.rows_upserted or 0) if latest is not None else 0,
            "error": error or None,
        },
    }


def _as_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def build_inventory_sync_status(
    db: AsyncSession,
    *,
    organization_id: int,
    iiko_office_configured: bool,
    iiko_cloud_configured: bool,
) -> dict:
    """JSON для GET /api/admin/inventory/sync-status."""
    slot = _neutral_slot()
    last = slot
    try:
        org_row = await db.get(OrganizationIntegrationSync, int(organization_id))
    except SQLAlchemyError as exc:
        logger.warning(
            "organization_integration_sync read failed org=%s: %s",
            organization_id,
            exc,
        )
        org_row = None
    if org_row is not None:
        last = {
            "at": _iso(getattr(org_row, "last_inventory_sync_at", None)),
            "ok": bool(getattr(org_row, "last_inventory_sync_ok", False)),
            "error": getattr(org_row, "last_inventory_sync_error", None) or None,
        }
    return {
        "iiko_office_configured": iiko_office_configured,
        "iiko_cloud_configured": iiko_cloud_configured,
        "last_inventory_sync": last,
    }
