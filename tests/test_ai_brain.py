"""
Тесты AI Brain: fallback, валидация ответов (мок OpenAI).
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.schemas.ai_schemas import AIBrainResponse
from app.services.ai_brain import _FALLBACK_RESPONSE, call_openai, call_openai_with_audio


def _mock_chat_completion(content: str) -> MagicMock:
    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock(message=MagicMock(content=content))]
    return mock_resp


@pytest.mark.asyncio
async def test_fallback_response_is_escalate() -> None:
    """Fallback ответ имеет intent=escalate."""
    assert _FALLBACK_RESPONSE.intent == "escalate"
    assert "оператора" in _FALLBACK_RESPONSE.reply_text.lower() or "сложност" in _FALLBACK_RESPONSE.reply_text.lower()


@pytest.mark.asyncio
async def test_openai_returns_valid_response() -> None:
    """При корректном JSON от OpenAI — возвращается AIBrainResponse."""
    valid_json = '{"intent":"faq","reply_text":"Мы работаем с 10 до 22","items":[],"booking_details":null}'
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=_mock_chat_completion(valid_json))

    with patch("app.services.ai_brain._ensure_openai_client", return_value=mock_client):
        result = await call_openai([], "Когда вы работаете?")

    assert isinstance(result, AIBrainResponse)
    assert result.intent == "faq"
    assert "10" in result.reply_text


@pytest.mark.asyncio
async def test_openai_invalid_json_triggers_retry() -> None:
    """При невалидном JSON — retry, затем fallback."""
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(
        return_value=_mock_chat_completion("это не JSON вообще"),
    )

    with patch("app.services.ai_brain._ensure_openai_client", return_value=mock_client):
        result = await call_openai([], "Привет")

    assert result.intent == "escalate"


@pytest.mark.asyncio
async def test_openai_exception_triggers_fallback() -> None:
    """При exception от API — fallback на escalate."""
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(side_effect=RuntimeError("API down"))

    with patch("app.services.ai_brain._ensure_openai_client", return_value=mock_client):
        result = await call_openai([], "Привет")

    assert result.intent == "escalate"


@pytest.mark.asyncio
async def test_openai_empty_response_retries() -> None:
    """Пустой content → retry → fallback."""
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=_mock_chat_completion(""))

    with patch("app.services.ai_brain._ensure_openai_client", return_value=mock_client):
        result = await call_openai([], "Привет")

    assert result.intent == "escalate"


@pytest.mark.asyncio
async def test_openai_order_response_with_items() -> None:
    """AI возвращает intent=order с позициями."""
    order_json = (
        '{"intent":"order","reply_text":"Отлично, записываю!","items":'
        '[{"name":"Плов","iiko_item_id":"uuid-1","quantity":2,"modifiers_ids":[],"exclude_ingredients":[]}],'
        '"booking_details":null}'
    )
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=_mock_chat_completion(order_json))

    with patch("app.services.ai_brain._ensure_openai_client", return_value=mock_client):
        result = await call_openai([], "Хочу 2 плова")

    assert result.intent == "order"
    assert len(result.items) == 1
    assert result.items[0].name == "Плов"
    assert result.items[0].quantity == 2


@pytest.mark.asyncio
async def test_openai_menu_context_in_system_message() -> None:
    """menu_context попадает в system-сообщение."""
    valid_json = '{"intent":"faq","reply_text":"Ответ","items":[],"booking_details":null}'
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=_mock_chat_completion(valid_json))

    with patch("app.services.ai_brain._ensure_openai_client", return_value=mock_client):
        await call_openai([], "Что есть?", menu_context="- Плов: 2790 ₸")

    call_kw = mock_client.chat.completions.create.call_args.kwargs
    messages = call_kw.get("messages") or []
    system_text = next((m["content"] for m in messages if m.get("role") == "system"), "")
    assert "Плов" in system_text


@pytest.mark.asyncio
async def test_openai_with_audio_returns_valid_response() -> None:
    """Whisper + чат: парсится AIBrainResponse."""
    valid_json = (
        '{"intent":"faq","reply_text":"Слышу, помогу","items":[],"booking_details":null,'
        '"recognized_speech":"хочу столик"}'
    )
    mock_client = MagicMock()
    mock_client.audio.transcriptions.create = AsyncMock(return_value=MagicMock(text="хочу столик"))
    mock_client.chat.completions.create = AsyncMock(return_value=_mock_chat_completion(valid_json))

    with patch("app.services.ai_brain._ensure_openai_client", return_value=mock_client):
        result = await call_openai_with_audio(
            [{"role": "user", "content": "Привет"}],
            b"\x00fake",
            "audio/ogg",
        )

    assert result.intent == "faq"
    assert result.recognized_speech == "хочу столик"
