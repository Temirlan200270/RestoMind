"""
Тесты AI Brain: fallback поведение, валидация ответов.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.schemas.ai_schemas import AIBrainResponse
from app.services.ai_brain import (
    _FALLBACK_RESPONSE,
    call_openai,
    call_openai_with_audio,
)


def _completion_with_parsed(
    *,
    parsed: AIBrainResponse | None = None,
    content: str | None = None,
    refusal: str | None = None,
) -> MagicMock:
    mock_msg = MagicMock()
    mock_msg.refusal = refusal
    mock_msg.parsed = parsed
    mock_msg.content = content
    mock_choice = MagicMock()
    mock_choice.message = mock_msg
    mock_completion = MagicMock()
    mock_completion.choices = [mock_choice]
    return mock_completion


@pytest.mark.asyncio
async def test_fallback_response_is_escalate() -> None:
    """Fallback ответ имеет intent=escalate."""
    assert _FALLBACK_RESPONSE.intent == "escalate"
    assert "оператора" in _FALLBACK_RESPONSE.reply_text.lower() or "сложност" in _FALLBACK_RESPONSE.reply_text.lower()


@pytest.mark.asyncio
async def test_openai_returns_valid_response() -> None:
    """При корректном parse — возвращается AIBrainResponse."""
    parsed = AIBrainResponse.model_validate_json(
        '{"intent":"faq","reply_text":"Мы работаем с 10 до 22","items":[],"booking_details":null}',
    )
    mock_client = MagicMock()
    mock_client.beta.chat.completions.parse = AsyncMock(
        return_value=_completion_with_parsed(parsed=parsed),
    )

    with patch("app.services.ai_brain._openai_client", mock_client):
        result = await call_openai([], "Когда вы работаете?")

    assert isinstance(result, AIBrainResponse)
    assert result.intent == "faq"
    assert "10" in result.reply_text


@pytest.mark.asyncio
async def test_openai_invalid_json_triggers_retry() -> None:
    """При невалидном content — retry, затем fallback."""
    mock_client = MagicMock()
    mock_client.beta.chat.completions.parse = AsyncMock(
        return_value=_completion_with_parsed(content="это не JSON вообще"),
    )

    with patch("app.services.ai_brain._openai_client", mock_client):
        result = await call_openai([], "Привет")

    assert result.intent == "escalate"


@pytest.mark.asyncio
async def test_openai_exception_triggers_fallback() -> None:
    """При exception от API — fallback на escalate."""
    mock_client = MagicMock()
    mock_client.beta.chat.completions.parse = AsyncMock(side_effect=RuntimeError("API down"))

    with patch("app.services.ai_brain._openai_client", mock_client):
        result = await call_openai([], "Привет")

    assert result.intent == "escalate"


@pytest.mark.asyncio
async def test_openai_empty_response_retries() -> None:
    """Пустой ответ → retry → fallback."""
    mock_client = MagicMock()
    mock_client.beta.chat.completions.parse = AsyncMock(
        return_value=_completion_with_parsed(parsed=None, content=""),
    )

    with patch("app.services.ai_brain._openai_client", mock_client):
        result = await call_openai([], "Привет")

    assert result.intent == "escalate"


@pytest.mark.asyncio
async def test_openai_order_response_with_items() -> None:
    """AI возвращает intent=order с позициями."""
    parsed = AIBrainResponse.model_validate_json(
        '{"intent":"order","reply_text":"Отлично, записываю!","items":'
        '[{"name":"Плов","iiko_item_id":"uuid-1","quantity":2,"modifiers_ids":[],"exclude_ingredients":[]}],'
        '"booking_details":null}',
    )
    mock_client = MagicMock()
    mock_client.beta.chat.completions.parse = AsyncMock(
        return_value=_completion_with_parsed(parsed=parsed),
    )

    with patch("app.services.ai_brain._openai_client", mock_client):
        result = await call_openai([], "Хочу 2 плова")

    assert result.intent == "order"
    assert len(result.items) == 1
    assert result.items[0].name == "Плов"
    assert result.items[0].quantity == 2


@pytest.mark.asyncio
async def test_openai_menu_context_injected() -> None:
    """menu_context попадает в system-сообщение."""
    parsed = AIBrainResponse.model_validate_json(
        '{"intent":"faq","reply_text":"Ответ","items":[],"booking_details":null}',
    )
    mock_client = MagicMock()
    mock_client.beta.chat.completions.parse = AsyncMock(
        return_value=_completion_with_parsed(parsed=parsed),
    )

    with patch("app.services.ai_brain._openai_client", mock_client):
        await call_openai([], "What is on the menu?", menu_context="- Plov: 2790 KZT")

        call_args = mock_client.beta.chat.completions.parse.call_args
        messages = call_args.kwargs["messages"]
        system_text = messages[0]["content"]

        assert "Plov" in system_text


@pytest.mark.asyncio
async def test_openai_with_audio_returns_valid_response() -> None:
    """Голос: STT + чат; recognized_speech заполняется."""
    parsed = AIBrainResponse.model_validate_json(
        '{"intent":"faq","reply_text":"Слышу, помогу","items":[],"booking_details":null,'
        '"recognized_speech":"хочу столик"}',
    )
    mock_client = MagicMock()
    mock_client.beta.chat.completions.parse = AsyncMock(
        return_value=_completion_with_parsed(parsed=parsed),
    )
    mock_client.audio.transcriptions.create = AsyncMock(
        return_value=MagicMock(text="хочу столик"),
    )

    with patch("app.services.ai_brain._openai_client", mock_client):
        result = await call_openai_with_audio(
            [{"role": "user", "content": "Привет"}],
            b"\x00fake",
            "audio/ogg",
        )

    assert result.intent == "faq"
    assert result.recognized_speech == "хочу столик"
