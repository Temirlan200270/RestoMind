import pytest
from sqlalchemy import func, select

from app.api.webhooks import _save_inbound_chat_log
from app.db.models import ChatLog


@pytest.mark.asyncio
async def test_save_inbound_chat_log_is_idempotent_by_whatsapp_message_id(db_with_menu) -> None:
    first_user_id, first_log_id, first_created = await _save_inbound_chat_log(
        db_with_menu,
        "+77050001122",
        "Самовывоз",
        organization_id=1,
        whatsapp_message_id="wamid.same-message",
        trace_id="trace-1",
        conversation_id="conv-1",
    )
    second_user_id, second_log_id, second_created = await _save_inbound_chat_log(
        db_with_menu,
        "+77050001122",
        "Самовывоз",
        organization_id=1,
        whatsapp_message_id="wamid.same-message",
        trace_id="trace-1",
        conversation_id="conv-1",
    )

    assert first_created is True
    assert second_created is False
    assert second_user_id == first_user_id
    assert second_log_id == first_log_id

    count = await db_with_menu.scalar(
        select(func.count(ChatLog.id)).where(
            ChatLog.organization_id == 1,
            ChatLog.role == "user",
            ChatLog.provider_message_id == "wamid.same-message",
        )
    )
    assert count == 1
