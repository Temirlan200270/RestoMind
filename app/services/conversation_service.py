from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from app.db.models import ChannelConnection, ChannelMessage
from app.db.session import async_session_factory

logger = logging.getLogger(__name__)

INBOUND_RETRY_DELAYS = (10, 30, 120, 300)


class ConversationService:
    """Canonical entrypoint from durable channel messages into the customer pipeline.

    The MVP keeps the proven WhatsApp/AI/order pipeline in place and wraps it here
    as a strangler step. Future context providers should grow behind this boundary,
    not inside provider adapters.
    """

    async def process_channel_message(self, channel_message_id: int) -> None:
        from app.api.webhooks import process_inbound_message
        from app.services.telegram_customer import customer_channel_context, reset_customer_channel_context

        async with async_session_factory() as db:
            msg = await db.get(ChannelMessage, int(channel_message_id))
            if msg is None or msg.direction != "in":
                return
            connection = await db.get(ChannelConnection, int(msg.channel_connection_id))
            if connection is None:
                msg.status = "failed"
                msg.error_code = "connection_missing"
                msg.error_message = "Channel connection not found"
                msg.failed_at = datetime.now(timezone.utc)
                await db.commit()
                return
            msg.status = "processing"
            msg.processing_at = datetime.now(timezone.utc)
            msg.attempt_count = int(msg.attempt_count or 0) + 1
            await db.commit()

        payload = msg.payload_json if isinstance(msg.payload_json, dict) else {}
        sender = payload.get("sender") if isinstance(payload.get("sender"), dict) else {}
        phone = (sender.get("phone") or sender.get("external_id") or msg.external_chat_id or "").strip()
        tokens = customer_channel_context(
            msg.provider,
            channel_connection_id=int(msg.channel_connection_id),
            external_chat_id=msg.external_chat_id,
        )
        try:
            await process_inbound_message(
                phone,
                msg.text or "",
                organization_id=int(msg.organization_id),
                channel=msg.provider,
                inbound_message_id=msg.external_message_id or "",
                trace_id=msg.trace_id or None,
                channel_connection_id=int(msg.channel_connection_id),
                external_chat_id=msg.external_chat_id,
            )
        except Exception as exc:
            logger.exception("channel inbound processing failed msg=%s", channel_message_id)
            async with async_session_factory() as db:
                row = await db.get(ChannelMessage, int(channel_message_id))
                if row is not None:
                    row.status = "retrying" if int(row.attempt_count or 0) < 5 else "failed"
                    row.error_code = exc.__class__.__name__
                    row.error_message = str(exc)[:2000]
                    row.next_attempt_at = datetime.now(timezone.utc) + timedelta(seconds=_retry_delay(row.attempt_count))
                    if row.status == "failed":
                        row.failed_at = datetime.now(timezone.utc)
                    await db.commit()
            raise
        finally:
            reset_customer_channel_context(tokens)

        async with async_session_factory() as db:
            row = await db.get(ChannelMessage, int(channel_message_id))
            if row is not None:
                row.status = "processed"
                row.next_attempt_at = None
                await db.commit()


def _retry_delay(attempt_count: int | None) -> int:
    idx = max(0, min(len(INBOUND_RETRY_DELAYS) - 1, int(attempt_count or 0)))
    return INBOUND_RETRY_DELAYS[idx]


conversation_service = ConversationService()
