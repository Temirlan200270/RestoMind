"""Admin API: KPI офiciантов из iiko (аналитика)."""

from __future__ import annotations

import csv
import io
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin.deps import (
    admin_org_from_session,
    require_admin_session_active,
    require_staff_manager_or_admin,
)
from app.db.models import IikoSyncRun, Organization, WaiterKpiDaily, WaiterRegistry
from app.db.session import get_db
from app.services.iiko_waiter_kpi_sync import (
    SYNC_KIND_WAITER_KPI,
    org_has_waiter_kpi_source,
    sync_waiter_kpi_for_org,
)
from app.services.org_iiko_office import org_has_iiko_office_in_db, resolve_org_iiko_office_credentials
from app.services.integration_config import iiko_effective_configured

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/admin/analytics/waiter-kpi",
    tags=["Analytics"],
    dependencies=[Depends(require_admin_session_active)],
)


def _default_date_range() -> tuple[date, date]:
    today = datetime.now(timezone.utc).date()
    return today - timedelta(days=6), today


def _parse_date_range(
    date_from: date | None,
    date_to: date | None,
) -> tuple[date, date]:
    df, dt = date_from, date_to
    if df is None or dt is None:
        default_from, default_to = _default_date_range()
        df = df or default_from
        dt = dt or default_to
    if df > dt:
        raise HTTPException(status_code=400, detail="date_from не может быть позже date_to")
    if (dt - df).days > 366:
        raise HTTPException(status_code=400, detail="Максимальный период — 366 дней")
    return df, dt


@router.post("/sync")
async def post_waiter_kpi_sync(
    request: Request,
    days: int = Query(1, ge=1, le=31),
    _perm: None = Depends(require_staff_manager_or_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Ручной запуск ETL KPI офiciантов из iiko."""
    org_id = admin_org_from_session(request)
    org = await db.get(Organization, org_id)
    if not org_has_waiter_kpi_source(org):
        raise HTTPException(
            status_code=400,
            detail="iiko не настроен: подключите iiko Cloud и/или iiko Office",
        )
    try:
        result = await sync_waiter_kpi_for_org(db, int(org_id), days=days)
    except Exception as exc:
        logger.exception("waiter_kpi sync failed org=%s", org_id)
        await db.rollback()
        from app.services.iiko_waiter_kpi_sync import record_waiter_kpi_sync_run

        await record_waiter_kpi_sync_run(
            db,
            int(org_id),
            ok=False,
            error_text=str(exc),
        )
        await db.commit()
        raise HTTPException(status_code=502, detail="Ошибка синхронизации KPI офiciантов") from exc

    if not result.get("ok"):
        raise HTTPException(
            status_code=400,
            detail=result.get("detail") or result.get("error") or "Синхронизация не выполнена",
        )
    return {"ok": True, **result}


@router.get("/sync-status")
async def get_waiter_kpi_sync_status(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Последний прогон ETL KPI офiciантов для активного филиала."""
    org_id = admin_org_from_session(request)
    org = await db.get(Organization, org_id)
    office_creds = await resolve_org_iiko_office_credentials(db, int(org_id))
    cloud_ok = await iiko_effective_configured(db, int(org_id))

    last_run = await db.scalar(
        select(IikoSyncRun)
        .where(
            IikoSyncRun.organization_id == int(org_id),
            IikoSyncRun.sync_kind == SYNC_KIND_WAITER_KPI,
        )
        .order_by(IikoSyncRun.finished_at.desc())
        .limit(1)
    )

    return {
        "ok": True,
        "iiko_cloud_configured": cloud_ok,
        "iiko_office_configured": office_creds is not None,
        "hall_connected": org_has_iiko_office_in_db(org),
        "delivery_connected": cloud_ok,
        "last_sync": (
            {
                "status": last_run.status,
                "rows_upserted": last_run.rows_upserted,
                "finished_at": last_run.finished_at.isoformat() if last_run.finished_at else None,
                "error_text": last_run.error_text,
            }
            if last_run is not None
            else None
        ),
    }


@router.get("")
async def get_waiter_kpi_ranking(
    request: Request,
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    location_id: int | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Рейтинг офiciантов за период (агрегация по waiter_iiko_id)."""
    org_id = admin_org_from_session(request)
    df, dt = _parse_date_range(date_from, date_to)

    filters = [
        WaiterKpiDaily.organization_id == int(org_id),
        WaiterKpiDaily.kpi_date >= df,
        WaiterKpiDaily.kpi_date <= dt,
    ]
    if location_id is not None:
        filters.append(WaiterKpiDaily.location_id == int(location_id))

    rows = (
        await db.execute(
            select(
                WaiterKpiDaily.waiter_iiko_id,
                func.sum(WaiterKpiDaily.orders_served).label("orders_served"),
                func.sum(WaiterKpiDaily.total_revenue_kzt).label("total_revenue_kzt"),
                func.sum(WaiterKpiDaily.guests_count).label("guests_count"),
                func.sum(WaiterKpiDaily.cancelled_orders).label("cancelled_orders"),
                func.avg(WaiterKpiDaily.avg_service_time_min).label("avg_service_time_min"),
            )
            .where(*filters)
            .group_by(WaiterKpiDaily.waiter_iiko_id)
            .order_by(func.sum(WaiterKpiDaily.total_revenue_kzt).desc())
        )
    ).all()

    waiter_ids = [str(r.waiter_iiko_id) for r in rows]
    names: dict[str, str] = {}
    if waiter_ids:
        reg_rows = (
            await db.execute(
                select(WaiterRegistry.waiter_iiko_id, WaiterRegistry.waiter_name).where(
                    WaiterRegistry.organization_id == int(org_id),
                    WaiterRegistry.waiter_iiko_id.in_(waiter_ids),
                )
            )
        ).all()
        names = {str(wid): str(name or wid) for wid, name in reg_rows}

    items: list[dict[str, Any]] = []
    for idx, row in enumerate(rows, start=1):
        orders = int(row.orders_served or 0)
        revenue = float(row.total_revenue_kzt or 0)
        avg_check = round(revenue / orders, 2) if orders > 0 else 0.0
        wid = str(row.waiter_iiko_id)
        items.append(
            {
                "rank": idx,
                "waiter_name": names.get(wid, wid),
                "orders_served": orders,
                "total_revenue_kzt": revenue,
                "avg_check_kzt": avg_check,
                "guests_count": int(row.guests_count or 0),
                "cancelled_orders": int(row.cancelled_orders or 0),
                "avg_service_time_min": (
                    round(float(row.avg_service_time_min), 2)
                    if row.avg_service_time_min is not None
                    else None
                ),
            }
        )

    org = await db.get(Organization, int(org_id))
    office_creds = await resolve_org_iiko_office_credentials(db, int(org_id))
    cloud_ok = await iiko_effective_configured(db, int(org_id))

    return {
        "ok": True,
        "date_from": df.isoformat(),
        "date_to": dt.isoformat(),
        "items": items,
        "hall_connected": office_creds is not None or org_has_iiko_office_in_db(org),
        "delivery_connected": cloud_ok,
    }


@router.get("/export.csv")
async def export_waiter_kpi_csv(
    request: Request,
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    location_id: int | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """CSV рейтинга офiciантов (UTF-8 BOM для Excel)."""
    payload = await get_waiter_kpi_ranking(
        request,
        date_from=date_from,
        date_to=date_to,
        location_id=location_id,
        db=db,
    )
    buf = io.StringIO()
    buf.write("\ufeff")
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(
        [
            "Место",
            "Официант",
            "Заказов",
            "Выручка KZT",
            "Средний чек KZT",
            "Гостей",
            "Отмен",
            "Ср. время обслуживания мин",
        ]
    )
    for row in payload.get("items") or []:
        writer.writerow(
            [
                row.get("rank"),
                row.get("waiter_name"),
                row.get("orders_served"),
                row.get("total_revenue_kzt"),
                row.get("avg_check_kzt"),
                row.get("guests_count"),
                row.get("cancelled_orders"),
                row.get("avg_service_time_min") or "",
            ]
        )
    body = buf.getvalue()
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="waiter_kpi_export.csv"'},
    )
