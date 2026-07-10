from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_customer_reply_uses_default_baileys_with_org_id() -> None:
    from app.services.customer_reply import send_customer_text

    with patch(
        "app.services.customer_reply._try_send_via_default_baileys",
        new_callable=AsyncMock,
        return_value=True,
    ) as mock_default, patch(
        "app.services.customer_reply.send_message",
        new_callable=AsyncMock,
    ) as mock_meta:
        await send_customer_text("+77001112233", "Hello", organization_id=7)

    mock_default.assert_awaited_once_with(
        "+77001112233",
        "Hello",
        outbound_chat_log_id=None,
        organization_id=7,
    )
    mock_meta.assert_not_awaited()
