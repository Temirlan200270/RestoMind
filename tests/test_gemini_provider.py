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
    _gemini_response_schema,
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


def test_gemini_response_schema_has_required_contract() -> None:
    schema = _gemini_response_schema()

    assert schema["type"] == "object"
    assert schema["required"] == ["intent", "reply_text"]
    assert schema["properties"]["intent"]["enum"] == ["order", "book", "faq", "escalate"]
    assert schema["properties"]["payment_split"]["type"] == "object"


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
async def test_gemini_provider_does_not_failover_on_validation_error() -> None:
    """
    Gemini prod preset держим на 2.5 без автоматического переключения на preview-модели.
    Если 2.5 вернула JSON, не проходящий Pydantic (ValidationError), наружу выходит
    быстрый TransientAiError вместо долгого failover на другую модель.
    """
    provider = GeminiProvider()
    provider._ensure_configured = lambda: True  # type: ignore[method-assign]

    bad_resp = MagicMock()
    # Важно: это ВАЛИДНЫЙ JSON, но структурно неверный для схемы (intent должен быть строкой Literal).
    # Проверяем именно pydantic.ValidationError, а не json.JSONDecodeError.
    bad_resp.text = '{"intent": 123, "reply_text": "x"}'

    mock_model = MagicMock()
    mock_model.generate_content_async = AsyncMock(return_value=bad_resp)

    with patch("google.generativeai.GenerativeModel", return_value=mock_model) as mock_factory:
        with pytest.raises(TransientAiError):
            await provider.generate_response(history=[], user_text="instruction")

        assert mock_model.generate_content_async.call_count == 1
        assert mock_factory.call_count == 1


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
        # Пользовательский текст оборачивается маркерами (см. format_untrusted_user_text_for_model).
        if "<<<USER_MESSAGE>>>\nA\n<<</USER_MESSAGE>>>" in prompt:
            return resp_a
        if "<<<USER_MESSAGE>>>\nB\n<<</USER_MESSAGE>>>" in prompt:
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
async def test_gemini_provider_passes_structured_output_schema() -> None:
    provider = GeminiProvider()
    provider._ensure_configured = lambda: True  # type: ignore[method-assign]

    response = MagicMock()
    response.text = '{"intent":"faq","reply_text":"OK"}'

    mock_model = MagicMock()
    mock_model.generate_content_async = AsyncMock(return_value=response)

    with patch("google.generativeai.GenerativeModel", return_value=mock_model):
        result = await provider.generate_response(history=[], user_text="instruction")

    assert result.intent == "faq"
    generation_config = mock_model.generate_content_async.call_args.kwargs["generation_config"]
    assert generation_config.response_mime_type == "application/json"
    assert generation_config.response_schema["required"] == ["intent", "reply_text"]
    assert generation_config.response_schema["properties"]["payment_split"]["type"] == "object"


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

