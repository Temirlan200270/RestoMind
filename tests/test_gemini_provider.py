from __future__ import annotations

import logging
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.schemas.ai_schemas import AIBrainResponse
from app.services.ai_engine.errors import TransientAiError
from app.services.ai_engine.gemini_p import (
    GeminiProvider,
    _extract_json_from_text,
    _is_provider_level_error,
)

# FutureWarning от `google.generativeai` (пакет переведён в maintenance)
# глушится глобально в pytest.ini — здесь ничего локально подавлять не нужно.


@pytest.mark.parametrize(
    ("raw_input", "expected_output"),
    [
        # 1) Чистый JSON
        ('{"intent":"faq"}', '{"intent":"faq"}'),
        # 2) Markdown блок (с префиксом json)
        ("Sure! Here it is: ```json\n{\"intent\":\"order\"}\n```", '{"intent":"order"}'),
        # 2.1) Иногда модель присылает только fence без текста снаружи
        ("```json\n{\"intent\":\"faq\"}\n```", '{"intent":"faq"}'),
        # 3) Markdown блок (без префикса)
        ("```\n{\"intent\":\"book\"}\n```", '{"intent":"book"}'),
        # 4) Thinking-преамбула от модели
        ("I have analyzed the request. { \"intent\": \"escalate\" }", '{ "intent": "escalate" }'),
        # 5) Грязный хвост после JSON
        ('{"intent":"faq"} ...hope this helps!', '{"intent":"faq"}'),
        # 6) Thinking-псевдотеги + JSON
        ("<thinking>analysis</thinking>\n{\"intent\":\"faq\",\"reply_text\":\"hi\"}", '{"intent":"faq","reply_text":"hi"}'),
        # 7) Пробелы и переносы
        (" \n{\"x\":1}\n  ", '{"x":1}'),
        # 8) Пустая строка
        ("", ""),
    ],
)
def test_extract_json_logic(raw_input: str, expected_output: str) -> None:
    assert _extract_json_from_text(raw_input).strip() == expected_output.strip()


@pytest.mark.asyncio
async def test_gemini_provider_smart_cascade_on_quota(caplog: pytest.LogCaptureFixture) -> None:
    """
    При ResourceExhausted (provider-level) каскад должен остановиться на первой модели:
    - ровно 1 вызов generate_content_async
    - вторая модель не создаётся вообще
    - в логах есть status=QUOTA_EXHAUSTED (контракт наблюдаемости)
    - наружу выходит TransientAiError
    """

    class ResourceExhausted(Exception):
        pass

    quota_error = ResourceExhausted("Prepayment credits depleted")

    provider = GeminiProvider()

    # Обходим настройки и genai.configure: юнит-тестирует логику каскада, не SDK-конфигурацию.
    provider._ensure_configured = lambda: True  # type: ignore[method-assign]

    mock_model = MagicMock()
    mock_model.generate_content_async = AsyncMock(side_effect=quota_error)

    caplog.set_level(logging.WARNING)
    with patch("google.generativeai.GenerativeModel", return_value=mock_model) as mock_factory:
        with pytest.raises(TransientAiError):
            await provider.generate_response(history=[], user_text="instruction")

        assert mock_model.generate_content_async.call_count == 1
        assert mock_factory.call_count == 1
        assert "status=QUOTA_EXHAUSTED" in caplog.text


@pytest.mark.asyncio
async def test_gemini_provider_failover_on_validation_error() -> None:
    """
    Если первая модель вернула JSON, не проходящий Pydantic (ValidationError),
    каскад должен попробовать вторую модель того же провайдера.
    """
    provider = GeminiProvider()
    provider._ensure_configured = lambda: True  # type: ignore[method-assign]

    bad_resp = MagicMock()
    # Важно: это ВАЛИДНЫЙ JSON, но структурно неверный для схемы (intent должен быть строкой Literal).
    # Проверяем именно pydantic.ValidationError, а не json.JSONDecodeError.
    bad_resp.text = '{"intent": 123, "reply_text": "x"}'

    # Минимально валидный ответ: AIBrainResponse требует intent + reply_text.
    good_resp = MagicMock()
    good_resp.text = '{"intent":"faq","reply_text":"OK"}'

    mock_model = MagicMock()
    mock_model.generate_content_async = AsyncMock(side_effect=[bad_resp, good_resp])

    with patch("google.generativeai.GenerativeModel", return_value=mock_model) as mock_factory:
        result = await provider.generate_response(history=[], user_text="instruction")

        assert isinstance(result, AIBrainResponse)
        assert result.intent == "faq"
        assert mock_model.generate_content_async.call_count == 2
        assert mock_factory.call_count == 2


@pytest.mark.asyncio
async def test_gemini_provider_is_stateless_across_concurrent_calls() -> None:
    """
    Invariant: провайдер не должен протаскивать состояние одного запроса в другой.
    Простой concurrency-тест: два одновременных вызова с разными USER-текстами
    должны вернуть корректные независимые ответы.
    """
    provider = GeminiProvider()
    provider._ensure_configured = lambda: True  # type: ignore[method-assign]

    resp_a = MagicMock()
    resp_a.text = '{"intent":"faq","reply_text":"A"}'
    resp_b = MagicMock()
    resp_b.text = '{"intent":"faq","reply_text":"B"}'

    async def _side_effect(prompt: str, *_args: object, **_kwargs: object) -> MagicMock:
        # В prompt GeminiProvider пишет "USER: {user_text}"
        if "USER: A" in prompt:
            return resp_a
        if "USER: B" in prompt:
            return resp_b
        raise RuntimeError("Unexpected prompt")

    mock_model = MagicMock()
    mock_model.generate_content_async = AsyncMock(side_effect=_side_effect)

    with patch("google.generativeai.GenerativeModel", return_value=mock_model) as mock_factory:
        r1, r2 = await asyncio.gather(
            provider.generate_response(history=[], user_text="A"),
            provider.generate_response(history=[], user_text="B"),
        )

        assert {r1.reply_text, r2.reply_text} == {"A", "B"}
        assert mock_model.generate_content_async.call_count == 2
        # В обоих вызовах модель создаётся (preset начинается с первого model_name).
        assert mock_factory.call_count == 2


@pytest.mark.asyncio
async def test_gemini_no_key_returns_escalate_without_api_call() -> None:
    """
    Без ключа GeminiProvider должен вернуть fallback escalate и не трогать SDK.
    """
    provider = GeminiProvider()
    provider._ensure_configured = lambda: False  # type: ignore[method-assign]

    with patch("google.generativeai.GenerativeModel") as mock_factory:
        result = await provider.generate_response(history=[], user_text="hi")

        assert result.intent == "escalate"
        assert mock_factory.call_count == 0


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (type("ResourceExhausted", (Exception,), {})("x"), True),
        (type("PermissionDenied", (Exception,), {})("x"), True),
        (type("Unauthenticated", (Exception,), {})("x"), True),
        (RuntimeError("boom"), False),
    ],
)
def test_is_provider_level_error(exc: BaseException, expected: bool) -> None:
    assert _is_provider_level_error(exc) is expected

