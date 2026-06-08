"""CSV export endpoints for admin (E0.1 tail)."""

from __future__ import annotations

import csv
import io
from datetime import date, datetime, time as dt_time, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import ChatLog, Order, User
from app.db.session import get_db
from app.services.intelligence_analytics import order_meta_from_items_json
from app.services.tenant_scope import orders_tenant_clause as _orders_tenant_clause

from .deps import admin_org_from_session, require_admin_session_active

export_router = APIRouter(dependencies=[Depends(require_admin_session_active)])

MAX_CSV_EXPORT_ROWS = 50_000


def _dt_as_utc(dt: datetime) -> datetime:
    """Naive datetime интерпретируем как UTC (единая ось графиков)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _sql_dt_for_filter(dt: datetime) -> datetime:
    """Для SQL-фильтров всегда используем UTC-aware datetime."""
    u = _dt_as_utc(dt)
    return u


def _export_range_utc(date_from: date | None, date_to: date | None) -> tuple[datetime, datetime]:
    """
    Полуинтервал [lo, hi) в UTC для фильтрации по created_at.
    По умолчанию — последние 90 суток.
    """
    today = datetime.now(timezone.utc).date()
    df = date_from or (today - timedelta(days=90))
    dt_end = date_to or today
    if df > dt_end:
        raise HTTPException(status_code=400, detail="date_from не может быть позже date_to")
    lo = datetime.combine(df, dt_time.min, tzinfo=timezone.utc)
    hi_excl = datetime.combine(dt_end, dt_time.min, tzinfo=timezone.utc) + timedelta(days=1)
    return lo, hi_excl


@export_router.get("/export/orders")
async def export_orders_csv(
    request: Request,
    date_from: date | None = Query(None, description="Начало периода (UTC, дата)"),
    date_to: date | None = Query(None, description="Конец периода включительно (UTC, дата)"),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """CSV заказов за период (UTF-8 с BOM для Excel)."""
    org_id = admin_org_from_session(request)
    lo, hi_excl = _export_range_utc(date_from, date_to)
    lo_sql = _sql_dt_for_filter(lo)
    hi_sql = _sql_dt_for_filter(hi_excl)

    res = await db.execute(
        select(Order, User.phone, User.name)
        .join(User, Order.user_id == User.id)
        .where(
            _orders_tenant_clause(org_id),
            User.organization_id == org_id,
            Order.created_at >= lo_sql,
            Order.created_at < hi_sql,
        )
        .order_by(Order.id.asc())
        .limit(MAX_CSV_EXPORT_ROWS + 1),
    )
    rows = res.all()
    if len(rows) > MAX_CSV_EXPORT_ROWS:
        raise HTTPException(
            status_code=400,
            detail=f"Слишком много строк (> {MAX_CSV_EXPORT_ROWS}). Сузьте период.",
        )

    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(
        [
            "order_id",
            "user_id",
            "user_phone",
            "user_name",
            "status",
            "total_price",
            "order_type",
            "created_at_utc",
            "updated_at_utc",
        ],
    )
    for o, phone, name in rows:
        meta = order_meta_from_items_json(o.items_json if isinstance(o.items_json, dict) else None)
        w.writerow(
            [
                o.id,
                o.user_id,
                phone or "",
                name or "",
                o.status or "",
                float(o.total_price),
                meta.get("order_type") or "",
                o.created_at.isoformat() if o.created_at else "",
                o.updated_at.isoformat() if o.updated_at else "",
            ],
        )

    return Response(
        content=buf.getvalue().encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="restomind_orders_export.csv"',
        },
    )


@export_router.get("/export/chats")
async def export_chats_csv(
    request: Request,
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """CSV сообщений chat_logs за период (роль, телефон клиента)."""
    org_id = admin_org_from_session(request)
    lo, hi_excl = _export_range_utc(date_from, date_to)
    lo_sql = _sql_dt_for_filter(lo)
    hi_sql = _sql_dt_for_filter(hi_excl)

    res = await db.execute(
        select(ChatLog, User.phone)
        .join(User, ChatLog.user_id == User.id)
        .where(
            User.organization_id == org_id,
            ChatLog.created_at >= lo_sql,
            ChatLog.created_at < hi_sql,
        )
        .order_by(ChatLog.id.asc())
        .limit(MAX_CSV_EXPORT_ROWS + 1),
    )
    rows = res.all()
    if len(rows) > MAX_CSV_EXPORT_ROWS:
        raise HTTPException(
            status_code=400,
            detail=f"Слишком много строк (> {MAX_CSV_EXPORT_ROWS}). Сузьте период.",
        )

    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["log_id", "user_id", "user_phone", "role", "created_at_utc", "content"])
    for cl, phone in rows:
        w.writerow(
            [
                cl.id,
                cl.user_id,
                phone or "",
                cl.role or "",
                cl.created_at.isoformat() if cl.created_at else "",
                (cl.content or "").replace("\r\n", "\n").replace("\r", "\n"),
            ],
        )

    return Response(
        content=buf.getvalue().encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="restomind_chats_export.csv"',
        },
    )
