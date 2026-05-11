"""
Входящий webhook от Telegram Bot API.

Telegram отправляет Update на POST /api/telegram/webhook.
Верификация: заголовок X-Telegram-Bot-Api-Secret-Token == TELEGRAM_WEBHOOK_SECRET.

Регистрация webhook (один раз после деплоя):
  curl -X POST "https://api.telegram.org/bot{TOKEN}/setWebhook" \
       -d "url=https://YOUR_DOMAIN/api/telegram/webhook" \
       -d "secret_token={TELEGRAM_WEBHOOK_SECRET}" \
       -d "allowed_updates=[\"message\",\"callback_query\"]"
"""

from __future__ import annotations

import logging
import secrets

from fastapi import APIRouter, BackgroundTasks, Request, Response

from app.core.config import settings

router = APIRouter(prefix="/telegram", tags=["Telegram Webhook"])
logger = logging.getLogger(__name__)


@router.post("/webhook")
async def telegram_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict:
    """
    Входящий Update от Telegram.
    Мгновенно возвращает 200 OK, обработку делегирует в BackgroundTask.
    """
    # Верификация по секрету (опционально, но рекомендуется)
    webhook_secret = (settings.telegram_webhook_secret or "").strip()
    if webhook_secret:
        incoming = (request.headers.get("X-Telegram-Bot-Api-Secret-Token") or "").strip()
        if not incoming or not secrets.compare_digest(incoming, webhook_secret):
            logger.warning("Telegram webhook: неверный X-Telegram-Bot-Api-Secret-Token")
            return Response(content="Forbidden", status_code=403)  # type: ignore[return-value]

    try:
        update = await request.json()
    except Exception:
        return {"ok": False}

    background_tasks.add_task(_process_update, update)
    return {"ok": True}


async def _process_update(update: dict) -> None:
    try:
        from app.services.telegram_operator import handle_telegram_update
        await handle_telegram_update(update)
    except Exception as exc:
        logger.exception("Telegram update processing failed: %s", exc)
