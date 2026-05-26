"""
Входящий webhook от Telegram Bot API.

Telegram отправляет Update на POST /api/telegram/webhook.
Верификация: заголовок X-Telegram-Bot-Api-Secret-Token == org.telegram_webhook_secret
или глобальный TELEGRAM_WEBHOOK_SECRET.

Регистрация webhook (один раз после деплоя):
  curl -X POST "https://api.telegram.org/bot{TOKEN}/setWebhook" \
       -d "url=https://YOUR_DOMAIN/api/telegram/webhook" \
       -d "secret_token={TELEGRAM_WEBHOOK_SECRET}" \
       -d "allowed_updates=[\"message\",\"callback_query\"]"
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Request, Response

from app.core.config import settings
from app.services.telegram_customer import (
    resolve_org_for_telegram_webhook,
    telegram_webhook_authorized,
)

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
    incoming_secret = (request.headers.get("X-Telegram-Bot-Api-Secret-Token") or "").strip()
    org_id = int(settings.default_organization_id)

    from app.db.session import async_session_factory

    async with async_session_factory() as db:
        org, org_id = await resolve_org_for_telegram_webhook(
            db,
            incoming_secret,
            bot_token=settings.telegram_bot_token,
        )

    if not telegram_webhook_authorized(incoming_secret, org):
        logger.warning("Telegram webhook: неверный X-Telegram-Bot-Api-Secret-Token")
        return Response(content="Forbidden", status_code=403)  # type: ignore[return-value]

    try:
        update = await request.json()
    except Exception:
        return {"ok": False}

    background_tasks.add_task(_process_update, update, org_id)
    return {"ok": True}


async def _process_update(update: dict, organization_id: int) -> None:
    try:
        from app.services.telegram_operator import handle_telegram_update

        await handle_telegram_update(update, organization_id=organization_id)
    except Exception as exc:
        logger.exception("Telegram update processing failed: %s", exc)
