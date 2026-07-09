from __future__ import annotations

import secrets
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import ChannelConnection, ChannelMessage, Organization
from app.db.session import get_db
from app.schemas.messaging import (
    ChannelConnectionCreate,
    ChannelConnectionOut,
    ChannelConnectionStatusEvent,
    ChannelDeliveryEvent,
    ChannelInboundEvent,
    ChannelMessageOut,
)
from app.services.messaging_gateway import (
    apply_connection_status,
    apply_delivery_event,
    channel_connection_to_out,
    channel_message_to_out,
    dispatch_outbound_message,
    ensure_channel_connection,
    list_due_outbound_messages,
    process_channel_message,
    record_inbound_event,
)
from app.services.channel_health import build_channel_health_summary

router = APIRouter(prefix="/channels", tags=["Messaging Channels"])


def _gateway_authorized(secret: str | None) -> bool:
    expected = (settings.messaging_gateway_secret or "").strip()
    if not expected:
        return not (settings.is_prod_like)
    return secrets.compare_digest((secret or "").strip(), expected)


def require_gateway_secret(x_restomind_gateway_secret: str | None = Header(default=None)) -> None:
    if not _gateway_authorized(x_restomind_gateway_secret):
        raise HTTPException(status_code=401, detail="Invalid messaging gateway secret")


async def _ensure_meta_connection_for_org(db: AsyncSession, org_id: int) -> None:
    org = await db.get(Organization, int(org_id))
    phone_number_id = (
        str(getattr(org, "whatsapp_phone_number_id", "") or "").strip()
        if org is not None else ""
    ) or str(settings.whatsapp_phone_number_id or "").strip()
    token_set = bool(str(settings.whatsapp_api_token or "").strip())
    if not phone_number_id and not token_set:
        return
    row = await db.scalar(
        select(ChannelConnection)
        .where(
            ChannelConnection.organization_id == int(org_id),
            ChannelConnection.provider == "whatsapp_meta",
        )
        .order_by(ChannelConnection.id.asc())
    )
    if row is None:
        row = await ensure_channel_connection(
            db,
            organization_id=int(org_id),
            provider="whatsapp_meta",
            phone=phone_number_id,
            display_name="WhatsApp Meta",
        )
    row.external_account_id = phone_number_id
    row.phone = phone_number_id
    row.display_name = row.display_name or "WhatsApp Meta"
    row.status = "connected" if phone_number_id and token_set else "error"
    row.health_json = {
        "provider": "whatsapp_meta",
        "health": "works" if row.status == "connected" else "degraded",
        "phone_number_id_set": bool(phone_number_id),
        "token_set": token_set,
    }
    if row.status == "error":
        row.last_error = "Не заданы ключ доступа или ID номера WhatsApp Meta"


async def _request_gateway_start(connection: ChannelConnection) -> None:
    base = (settings.messaging_gateway_url or "").strip().rstrip("/")
    if not base:
        return
    headers = {}
    if settings.messaging_gateway_secret:
        headers["X-RestoMind-Gateway-Secret"] = settings.messaging_gateway_secret
    payload = {
        "channel_connection_id": int(connection.id),
        "provider": connection.provider,
        "session_ref": connection.session_ref or "",
    }
    async with httpx.AsyncClient(timeout=settings.messaging_gateway_send_timeout_sec) as client:
        await client.post(f"{base}/v1/connections/start", json=payload, headers=headers)


@router.post("/inbound")
async def channel_inbound(
    event: ChannelInboundEvent,
    background_tasks: BackgroundTasks,
    _auth: None = Depends(require_gateway_secret),
    db: AsyncSession = Depends(get_db),
) -> dict:
    row, created = await record_inbound_event(db, event)
    await db.commit()
    if created:
        background_tasks.add_task(process_channel_message, int(row.id))
    return {
        "ok": True,
        "created": created,
        "channel_message_id": int(row.id),
        "status": row.status,
    }


@router.post("/gateway/connections/status", response_model=ChannelConnectionOut)
async def channel_connection_status(
    event: ChannelConnectionStatusEvent,
    _auth: None = Depends(require_gateway_secret),
) -> ChannelConnectionOut:
    try:
        return await apply_connection_status(event)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/gateway/connections", response_model=list[ChannelConnectionOut])
async def gateway_list_connections(
    _auth: None = Depends(require_gateway_secret),
    provider: str | None = Query(default=None),
    include_disabled: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
) -> list[ChannelConnectionOut]:
    stmt = select(ChannelConnection).order_by(ChannelConnection.id.asc())
    if provider:
        stmt = stmt.where(ChannelConnection.provider == provider.strip().lower())
    if not include_disabled:
        stmt = stmt.where(ChannelConnection.status != "disabled")
    rows = (await db.execute(stmt)).scalars().all()
    return [channel_connection_to_out(row) for row in rows]


@router.post("/gateway/messages/status", response_model=ChannelMessageOut | None)
async def channel_message_status(
    event: ChannelDeliveryEvent,
    _auth: None = Depends(require_gateway_secret),
) -> ChannelMessageOut | None:
    return await apply_delivery_event(event)


@router.get("/gateway/outbound/pending", response_model=list[ChannelMessageOut])
async def channel_outbound_pending(
    _auth: None = Depends(require_gateway_secret),
    connection_id: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
) -> list[ChannelMessageOut]:
    return await list_due_outbound_messages(limit=limit, connection_id=connection_id)


@router.post("/gateway/outbound/{channel_message_id}/dispatch")
async def channel_outbound_dispatch(
    channel_message_id: int,
    background_tasks: BackgroundTasks,
    _auth: None = Depends(require_gateway_secret),
) -> dict:
    background_tasks.add_task(dispatch_outbound_message, int(channel_message_id))
    return {"ok": True, "channel_message_id": int(channel_message_id)}


admin_router = APIRouter(prefix="/channel-connections", tags=["Admin Messaging Channels"])


@admin_router.get("", response_model=list[ChannelConnectionOut])
async def admin_list_channel_connections(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> list[ChannelConnectionOut]:
    from app.api.admin.deps import admin_org_from_session

    org_id = admin_org_from_session(request)
    await _ensure_meta_connection_for_org(db, int(org_id))
    await db.commit()
    rows = (
        await db.execute(
            select(ChannelConnection)
            .where(ChannelConnection.organization_id == int(org_id))
            .order_by(ChannelConnection.id.asc())
        )
    ).scalars().all()
    return [channel_connection_to_out(r) for r in rows]


@admin_router.post("", response_model=ChannelConnectionOut)
async def admin_create_channel_connection(
    request: Request,
    body: ChannelConnectionCreate,
    db: AsyncSession = Depends(get_db),
) -> ChannelConnectionOut:
    from app.api.admin.deps import admin_org_from_session

    org_id = admin_org_from_session(request)
    row = await ensure_channel_connection(
        db,
        organization_id=int(org_id),
        provider=body.provider,
        phone=body.phone,
        display_name=body.display_name,
    )
    await db.commit()
    await db.refresh(row)
    try:
        await _request_gateway_start(row)
    except Exception:
        row.status = "error"
        row.last_error = "Не удалось связаться с Messaging Gateway"
        await db.commit()
        await db.refresh(row)
    return channel_connection_to_out(row)


@admin_router.post("/{connection_id}/reconnect", response_model=ChannelConnectionOut)
async def admin_reconnect_channel_connection(
    request: Request,
    connection_id: int,
    db: AsyncSession = Depends(get_db),
) -> ChannelConnectionOut:
    from app.api.admin.deps import admin_org_from_session

    org_id = admin_org_from_session(request)
    row = await db.get(ChannelConnection, int(connection_id))
    if row is None or int(row.organization_id) != int(org_id):
        raise HTTPException(status_code=404, detail="Connection not found")
    row.status = "qr_required"
    row.last_error = ""
    row.last_qr = ""
    row.health_json = {"health": "needs_reconnect", "requested_at": datetime.now(timezone.utc).isoformat()}
    await db.commit()
    await db.refresh(row)
    try:
        await _request_gateway_start(row)
    except Exception:
        row.status = "error"
        row.last_error = "Не удалось связаться с Messaging Gateway"
        await db.commit()
        await db.refresh(row)
    return channel_connection_to_out(row)


@admin_router.get("/health")
async def admin_channel_connections_health(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    from app.api.admin.deps import admin_org_from_session

    org_id = admin_org_from_session(request)
    await _ensure_meta_connection_for_org(db, int(org_id))
    await db.commit()
    return await build_channel_health_summary(db, organization_id=int(org_id))


@admin_router.post("/{connection_id}/disable", response_model=ChannelConnectionOut)
async def admin_disable_channel_connection(
    request: Request,
    connection_id: int,
    db: AsyncSession = Depends(get_db),
) -> ChannelConnectionOut:
    from app.api.admin.deps import admin_org_from_session

    org_id = admin_org_from_session(request)
    row = await db.get(ChannelConnection, int(connection_id))
    if row is None or int(row.organization_id) != int(org_id):
        raise HTTPException(status_code=404, detail="Connection not found")
    row.status = "disabled"
    row.last_error = ""
    await db.commit()
    await db.refresh(row)
    return channel_connection_to_out(row)


@admin_router.get("/{connection_id}/messages", response_model=list[ChannelMessageOut])
async def admin_channel_connection_messages(
    request: Request,
    connection_id: int,
    limit: int = Query(default=25, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> list[ChannelMessageOut]:
    from app.api.admin.deps import admin_org_from_session

    org_id = admin_org_from_session(request)
    conn = await db.get(ChannelConnection, int(connection_id))
    if conn is None or int(conn.organization_id) != int(org_id):
        raise HTTPException(status_code=404, detail="Connection not found")
    rows = (
        await db.execute(
            select(ChannelMessage)
            .where(ChannelMessage.channel_connection_id == int(connection_id))
            .order_by(ChannelMessage.id.desc())
            .limit(int(limit))
        )
    ).scalars().all()
    return [channel_message_to_out(r) for r in rows]
