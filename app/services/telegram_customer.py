"""Telegram customer channel — inbound routing and outbound replies."""

from __future__ import annotations

import contextvars
import hashlib
import logging
import secrets
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import Organization, User

logger = logging.getLogger(__name__)

CUSTOMER_CHANNELS = frozenset({"whatsapp", "telegram", "operator", "voice"})

_active_customer_channel: contextvars.ContextVar[str] = contextvars.ContextVar(
    "active_customer_channel",
    default="whatsapp",
)
_active_telegram_chat_id: contextvars.ContextVar[int] = contextvars.ContextVar(
    "active_telegram_chat_id",
    default=0,
)


def telegram_synthetic_phone(telegram_user_id: int) -> str:
    """Stable pseudo-phone for dialog_mgr / Redis keys when guest has no MSISDN."""
    return f"tg:{int(telegram_user_id)}"


def normalize_customer_channel(channel: str | None) -> str:
    key = (channel or "whatsapp").strip().lower()
    return key if key in CUSTOMER_CHANNELS else "whatsapp"


def telegram_bot_token_fingerprint(token: str) -> str:
    """SHA256 hex prefix for MVP org↔bot token mapping in ``telegram_webhook_secret``."""
    raw = (token or "").strip()
    if not raw:
        return ""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def customer_channel_for_user(user: User | None) -> str:
    """Infer preferred outbound channel from RestoMind user row."""
    if user is None:
        return "whatsapp"
    if getattr(user, "telegram_user_id", None):
        return "telegram"
    phone = (user.phone or "").strip().lower()
    if phone.startswith("tg:"):
        return "telegram"
    return "whatsapp"


def telegram_chat_id_for_user(user: User | None) -> int:
    """Private Telegram chat_id equals ``telegram_user_id`` for customer DMs."""
    if user is None:
        return 0
    tg_uid = getattr(user, "telegram_user_id", None)
    if tg_uid:
        return int(tg_uid)
    phone = (user.phone or "").strip().lower()
    if phone.startswith("tg:"):
        try:
            return int(phone[3:])
        except ValueError:
            return 0
    return 0


async def resolve_org_for_telegram_webhook(
    db: AsyncSession,
    incoming_secret: str,
    *,
    bot_token: str | None = None,
) -> tuple[Organization | None, int]:
    """
    Resolve tenant org from ``X-Telegram-Bot-Api-Secret-Token`` or bot token fingerprint.
    Falls back to ``settings.default_organization_id``.
    """
    secret = (incoming_secret or "").strip()
    if secret:
        org = await db.scalar(
            select(Organization)
            .where(Organization.telegram_webhook_secret == secret)
            .limit(1)
        )
        if org is not None:
            return org, int(org.id)

        global_secret = (settings.telegram_webhook_secret or "").strip()
        if global_secret and secrets.compare_digest(secret, global_secret):
            oid = int(settings.default_organization_id)
            org = await db.get(Organization, oid)
            return org, oid

    token = (bot_token or settings.telegram_bot_token or "").strip()
    if token:
        fp = telegram_bot_token_fingerprint(token)
        if fp:
            org = await db.scalar(
                select(Organization)
                .where(Organization.telegram_webhook_secret == fp)
                .limit(1)
            )
            if org is not None:
                return org, int(org.id)

    oid = int(settings.default_organization_id)
    org = await db.get(Organization, oid)
    return org, oid


def telegram_webhook_authorized(incoming_secret: str, org: Organization | None) -> bool:
    """Verify webhook secret against org-specific or global env secret."""
    incoming = (incoming_secret or "").strip()
    global_secret = (settings.telegram_webhook_secret or "").strip()
    org_secret = ((getattr(org, "telegram_webhook_secret", None) or "").strip() if org else "")

    if not incoming:
        return not global_secret and not org_secret

    if org_secret and secrets.compare_digest(incoming, org_secret):
        return True
    if global_secret and secrets.compare_digest(incoming, global_secret):
        return True
    return False


@dataclass(frozen=True)
class CustomerChannelTokens:
    channel: contextvars.Token[str]
    telegram_chat_id: contextvars.Token[int]


def customer_channel_context(
    channel: str,
    *,
    telegram_chat_id: int | None = None,
) -> CustomerChannelTokens:
    """Set reply channel for ``send_customer_text`` for the duration of a pipeline."""
    return CustomerChannelTokens(
        _active_customer_channel.set(normalize_customer_channel(channel)),
        _active_telegram_chat_id.set(int(telegram_chat_id or 0)),
    )


def reset_customer_channel_context(tokens: CustomerChannelTokens) -> None:
    _active_customer_channel.reset(tokens.channel)
    _active_telegram_chat_id.reset(tokens.telegram_chat_id)


def current_customer_channel() -> str:
    return normalize_customer_channel(_active_customer_channel.get())


def current_telegram_chat_id() -> int:
    return int(_active_telegram_chat_id.get() or 0)


def _tg_api_url(method: str) -> str:
    token = (settings.telegram_bot_token or "").strip()
    return f"https://api.telegram.org/bot{token}/{method}"


async def send_telegram_customer_text(
    chat_id: int,
    text: str,
    *,
    parse_mode: str | None = None,
) -> dict[str, Any]:
    """
    Send bot reply to Telegram customer chat.
    Returns Telegram API JSON (``ok``, ``result``); no-op if token missing.
    """
    token = (settings.telegram_bot_token or "").strip()
    if not token or not chat_id:
        logger.debug("telegram_customer: skip send (no token or chat_id)")
        return {"ok": False, "skipped": True}

    payload: dict[str, Any] = {
        "chat_id": int(chat_id),
        "text": (text or "")[:4000],
        "disable_web_page_preview": True,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(_tg_api_url("sendMessage"), json=payload)
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, dict) else {"ok": True}
    except Exception as exc:
        logger.warning("send_telegram_customer_text failed chat_id=%s: %s", chat_id, exc)
        return {"ok": False, "error": str(exc)}


async def ensure_telegram_user(
    telegram_user_id: int,
    organization_id: int,
    *,
    display_name: str | None = None,
) -> str:
    """Bind Telegram user to RestoMind ``users`` row; returns synthetic phone."""
    from app.db.session import async_session_factory
    from app.services.intent_router import get_or_create_user

    phone = telegram_synthetic_phone(telegram_user_id)
    async with async_session_factory() as db:
        user = await get_or_create_user(db, phone, organization_id)
        if user.telegram_user_id != int(telegram_user_id):
            user.telegram_user_id = int(telegram_user_id)
        if display_name and not (user.name or "").strip():
            user.name = display_name.strip()[:255]
        await db.commit()
    return phone


async def handle_telegram_customer_message(msg: dict, *, organization_id: int | None = None) -> bool:
    """
    Route private customer text into shared inbound pipeline.
    Returns True if handled as customer message.
    """
    chat = msg.get("chat") or {}
    if (chat.get("type") or "").lower() != "private":
        return False

    from_user = msg.get("from") or {}
    telegram_user_id = from_user.get("id")
    chat_id = chat.get("id")
    if not telegram_user_id or not chat_id:
        return False

    text = (msg.get("text") or "").strip()
    if not text or text.startswith("/"):
        return False

    org_id = int(organization_id) if organization_id is not None else int(settings.default_organization_id)
    display_name = " ".join(
        x for x in [(from_user.get("first_name") or "").strip(), (from_user.get("last_name") or "").strip()] if x
    ) or None

    phone = await ensure_telegram_user(int(telegram_user_id), org_id, display_name=display_name)
    message_id = str(msg.get("message_id") or "")

    from app.api.webhooks import process_inbound_message

    ctx_token = customer_channel_context("telegram", telegram_chat_id=int(chat_id))
    try:
        await process_inbound_message(
            phone,
            text,
            organization_id=org_id,
            channel="telegram",
            inbound_message_id=message_id,
        )
    finally:
        reset_customer_channel_context(ctx_token)

    return True
