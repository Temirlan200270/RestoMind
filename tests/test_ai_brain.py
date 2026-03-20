"""
Тесты AI Brain: fallback поведение, валидация ответов.
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from app.schemas.ai_schemas import AIBrainResponse
from app.services.ai_brain import _FALLBACK_RESPONSE, call_gemini


@pytest.mark.asyncio
async def test_fallback_response_is_escalate() -> None:
    """Fallback ответ имеет intent=escalate."""
    assert _FALLBACK_RESPONSE.intent == "escalate"
    assert "оператора" in _FALLBACK_RESPONSE.reply_text.lower() or "сложност" in _FALLBACK_RESPONSE.reply_text.lower()


@pytest.mark.asyncio
async def test_gemini_returns_valid_response() -> None:
    """При корректном ответе Gemini — возвращается AIBrainResponse."""
    valid_json = '{"intent":"faq","reply_text":"Мы работаем с 10 до 22","items":[],"booking_details":null}'

    mock_response = MagicMock()
    mock_response.text = valid_json

    with patch("app.services.ai_brain._gemini_client") as mock_client:
        mock_client.aio.models.generate_content = AsyncMock(return_value=mock_response)
        result = await call_gemini([], "Когда вы работаете?")

    assert isinstance(result, AIBrainResponse)
    assert result.intent == "faq"
    assert "10" in result.reply_text


@pytest.mark.asyncio
async def test_gemini_invalid_json_triggers_retry() -> None:
    """При невалидном JSON — retry, затем fallback."""
    mock_response = MagicMock()
    mock_response.text = "это не JSON вообще"

    with patch("app.services.ai_brain._gemini_client") as mock_client:
        mock_client.aio.models.generate_content = AsyncMock(return_value=mock_response)
        result = await call_gemini([], "Привет")

    assert result.intent == "escalate"


@pytest.mark.asyncio
async def test_gemini_exception_triggers_fallback() -> None:
    """При exception от Gemini API — fallback на escalate."""
    with patch("app.services.ai_brain._gemini_client") as mock_client:
        mock_client.aio.models.generate_content = AsyncMock(side_effect=RuntimeError("API down"))
        result = await call_gemini([], "Привет")

    assert result.intent == "escalate"


@pytest.mark.asyncio
async def test_gemini_empty_response_retries() -> None:
    """Пустой ответ (text=None) → retry → fallback."""
    mock_response = MagicMock()
    mock_response.text = None

    with patch("app.services.ai_brain._gemini_client") as mock_client:
        mock_client.aio.models.generate_content = AsyncMock(return_value=mock_response)
        result = await call_gemini([], "Привет")

    assert result.intent == "escalate"


@pytest.mark.asyncio
async def test_gemini_order_response_with_items() -> None:
    """AI возвращает intent=order с позициями."""
    order_json = (
        '{"intent":"order","reply_text":"Отлично, записываю!","items":'
        '[{"name":"Плов","iiko_item_id":"uuid-1","quantity":2,"modifiers_ids":[],"exclude_ingredients":[]}],'
        '"booking_details":null}'
    )
    mock_response = MagicMock()
    mock_response.text = order_json

    with patch("app.services.ai_brain._gemini_client") as mock_client:
        mock_client.aio.models.generate_content = AsyncMock(return_value=mock_response)
        result = await call_gemini([], "Хочу 2 плова")

    assert result.intent == "order"
    assert len(result.items) == 1
    assert result.items[0].name == "Плов"
    assert result.items[0].quantity == 2


@pytest.mark.asyncio
async def test_gemini_menu_context_injected() -> None:
    """menu_context добавляется в system_instruction."""
    valid_json = '{"intent":"faq","reply_text":"Ответ","items":[],"booking_details":null}'
    mock_response = MagicMock()
    mock_response.text = valid_json

    with patch("app.services.ai_brain._gemini_client") as mock_client:
        mock_client.aio.models.generate_content = AsyncMock(return_value=mock_response)
        await call_gemini([], "Что есть?", menu_context="- Плов: 2790 ₸")

        call_args = mock_client.aio.models.generate_content.call_args
        config = call_args.kwargs.get("config") or call_args[1].get("config", {})
        system_text = config.get("system_instruction", "")

        assert "Плов" in system_text
