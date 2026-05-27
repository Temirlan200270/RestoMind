"""Тесты fast→strong routing и dual-provider latency guards."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.schemas.ai_schemas import AIBrainResponse
from app.services.ai_brain import (
    _needs_strong_model_rerun,
    call_ai,
)


def test_needs_strong_rerun_with_items() -> None:
    resp = AIBrainResponse.model_validate_json(
        '{"intent":"order","reply_text":"ok","items":[{"name":"Плов","quantity":1}]}',
    )
    assert _needs_strong_model_rerun(resp) is True


def test_needs_strong_rerun_order_with_reply_skips() -> None:
    resp = AIBrainResponse.model_validate_json(
        '{"intent":"order","reply_text":"Что будете заказывать?","items":[]}',
    )
    assert _needs_strong_model_rerun(resp) is False


def test_needs_strong_rerun_empty_order_reply() -> None:
    resp_empty = AIBrainResponse.model_validate_json(
        '{"intent":"order","reply_text":"","items":[]}',
    )
    assert _needs_strong_model_rerun(resp_empty) is True


@pytest.mark.asyncio
async def test_call_ai_skips_strong_rerun_for_order_with_reply() -> None:
    fast = AIBrainResponse.model_validate_json(
        '{"intent":"order","reply_text":"Подскажите, что добавить в заказ?","items":[]}',
    )
    mock_provider = MagicMock()
    mock_provider.generate_response = AsyncMock(return_value=fast)

    with patch("app.services.ai_brain.get_ai_client", return_value=mock_provider), patch(
        "app.services.ai_brain.settings.ai_model_routing_enabled",
        True,
    ), patch(
        "app.services.ai_brain.resolve_model_tier",
        return_value="fast",
    ):
        result = await call_ai([], "хочу плов", menu_context="menu")

    assert mock_provider.generate_response.await_count == 1
    assert result.reply_text == fast.reply_text


@pytest.mark.asyncio
async def test_call_ai_skips_strong_on_oversize_prompt() -> None:
    fast = AIBrainResponse.model_validate_json(
        '{"intent":"order","reply_text":"","items":[]}',
    )
    mock_provider = MagicMock()
    mock_provider.generate_response = AsyncMock(return_value=fast)

    with patch("app.services.ai_brain.get_ai_client", return_value=mock_provider), patch(
        "app.services.ai_brain.settings.ai_model_routing_enabled",
        True,
    ), patch(
        "app.services.ai_brain.settings.prompt_max_tokens_soft",
        100,
    ), patch(
        "app.services.ai_brain.resolve_model_tier",
        return_value="fast",
    ):
        result = await call_ai([], "плов", menu_context="x" * 5000)

    assert mock_provider.generate_response.await_count == 1
    assert result is fast
