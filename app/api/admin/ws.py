"""
WebSocket админки: live-события по подписанному токену (без cookie в WS).

Часть пакета ``app.api.admin`` (E0.1).
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.services.admin_tokens import AdminWsClaims, parse_admin_ws_token
from app.services.events import subscribe_events

logger = logging.getLogger(__name__)

ws_router = APIRouter(prefix="/admin", tags=["Admin Panel"])

_WS_KEEPALIVE_SEC = 25


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
        # Лог нужен, чтобы в Render Logs было видно причину "WS closed before established":
        # чаще всего это неверный SESSION_SECRET (подпись ws_token) или протухший токен.
        try:
            short = (token or "")[:16]
            logger.warning("Admin WebSocket unauthorized; token_prefix=%r", short)
        except Exception:
            logger.warning("Admin WebSocket unauthorized")
        await ws.close(code=4003, reason="Unauthorized")
        return
    await ws.accept()
    logger.info("Admin WebSocket подключён org=%s", claims.organization_id)
    keepalive_task: asyncio.Task[None] | None = None

    async def _keepalive() -> None:
        # App-level keepalive. Некоторые прокси/балансировщики (особенно на free-tier)
        # закрывают "тихий" WebSocket без трафика. Браузер не умеет отправлять ping,
        # поэтому шлём лёгкое служебное сообщение.
        while True:
            await asyncio.sleep(_WS_KEEPALIVE_SEC)
            try:
                await ws.send_text(json.dumps({"type": "ws_ping"}, ensure_ascii=False))
            except Exception:
                return

    keepalive_task = asyncio.create_task(_keepalive())
    try:
        await ws.send_text(json.dumps({"type": "ws_ready", "v": 1}, ensure_ascii=False))
    except Exception:
        if keepalive_task is not None:
            keepalive_task.cancel()
        return
    try:
        async for event_json in subscribe_events():
            if _ws_event_allowed_for_org(event_json, claims):
                await ws.send_text(event_json)
    except (WebSocketDisconnect, RuntimeError) as exc:
        logger.info("Admin WebSocket отключён: %s", exc)
    except Exception as exc:
        logger.error("WebSocket error: %s", exc)
    finally:
        if keepalive_task is not None:
            keepalive_task.cancel()
