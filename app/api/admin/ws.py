"""
WebSocket админки: live-события по подписанному токену (без cookie в WS).

Часть пакета ``app.api.admin`` (E0.1).
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.services.admin_tokens import AdminWsClaims, parse_admin_ws_token
from app.services.events import subscribe_events

logger = logging.getLogger(__name__)

ws_router = APIRouter(prefix="/admin", tags=["Admin Panel"])


def _ws_event_allowed_for_org(event_json: str, claims: AdminWsClaims) -> bool:
    """События с чужим или неизвестным organization_id не отправляем подписчику."""
    try:
        payload = json.loads(event_json)
    except json.JSONDecodeError:
        return False
    data = payload.get("data")
    if not isinstance(data, dict):
        return False
    if "organization_id" not in data:
        return False
    oid = data.get("organization_id")
    if oid is None:
        return False
    try:
        return int(oid) == int(claims.organization_id)
    except (TypeError, ValueError):
        return False


@ws_router.websocket("/ws")
async def admin_websocket(ws: WebSocket, token: str = "") -> None:
    """
    WebSocket для real-time уведомлений в админке.
    Авторизация: query ?token= — подписанный токен из POST /auth/login или GET /auth/me.
    """
    claims = parse_admin_ws_token(token)
    if claims is None:
        await ws.close(code=4003, reason="Unauthorized")
        return
    await ws.accept()
    logger.info("Admin WebSocket подключён org=%s", claims.organization_id)
    try:
        await ws.send_text(json.dumps({"type": "ws_ready", "v": 1}, ensure_ascii=False))
    except Exception:
        return
    try:
        async for event_json in subscribe_events():
            if _ws_event_allowed_for_org(event_json, claims):
                await ws.send_text(event_json)
    except (WebSocketDisconnect, RuntimeError) as exc:
        logger.info("Admin WebSocket отключён: %s", exc)
    except Exception as exc:
        logger.error("WebSocket error: %s", exc)
