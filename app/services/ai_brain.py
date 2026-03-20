"""
AI Brain — ядро интеллекта бота.
Асинхронный вызов Gemini API с гарантированным возвратом Pydantic-схемы (Structured Outputs).
Включает retry при сбоях и fallback при невалидном ответе.
"""

import logging

from google import genai
from pydantic import ValidationError

from app.core.config import settings
from app.schemas.ai_schemas import AIBrainResponse
from app.services.prompts import RESTAURANT_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

_gemini_client = genai.Client(api_key=settings.gemini_api_key)

DEFAULT_MODEL = "gemini-2.5-flash"
MAX_RETRIES = 2

_FALLBACK_RESPONSE = AIBrainResponse(
    intent="escalate",
    reply_text="Прошу прощения, у меня возникли технические сложности. Переключаю на оператора.",
)


async def call_gemini(
    history: list[dict[str, str]],
    user_text: str,
    menu_context: str = "",
) -> AIBrainResponse:
    """
    Отправляет контекст диалога в Gemini и получает структурированный ответ.
    При сбое — до MAX_RETRIES повторных попыток, затем fallback на escalate.

    Args:
        history: Предыдущие сообщения диалога [{role: "user"/"model", content: "..."}].
        user_text: Новое сообщение от пользователя.
        menu_context: Текстовое описание актуального меню с ценами.

    Returns:
        AIBrainResponse — Pydantic-объект с intent, reply_text, items, booking_details.
    """
    system_prompt = RESTAURANT_SYSTEM_PROMPT
    if menu_context:
        system_prompt += f"\n\n# Актуальное меню ресторана\n{menu_context}"

    gemini_history = []
    for msg in history:
        role = msg["role"]
        if role == "assistant":
            role = "model"
        gemini_history.append({"role": role, "parts": [{"text": msg["content"]}]})

    gemini_history.append({"role": "user", "parts": [{"text": user_text}]})

    logger.debug(
        "Запрос к Gemini: %d сообщений в контексте, новое: '%s'",
        len(gemini_history),
        user_text[:100],
    )

    last_error: Exception | None = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = await _gemini_client.aio.models.generate_content(
                model=DEFAULT_MODEL,
                contents=gemini_history,
                config={
                    "system_instruction": system_prompt,
                    "response_mime_type": "application/json",
                    "response_json_schema": AIBrainResponse.model_json_schema(),
                    "temperature": 0.3,
                },
            )

            if not response.text:
                logger.warning("Gemini вернул пустой ответ (попытка %d/%d)", attempt, MAX_RETRIES)
                last_error = ValueError("Empty response")
                continue

            result = AIBrainResponse.model_validate_json(response.text)

            logger.info(
                "Ответ Gemini: intent=%s, reply='%s'",
                result.intent,
                result.reply_text[:80],
            )
            return result

        except ValidationError as exc:
            logger.error(
                "Gemini вернул невалидный JSON (попытка %d/%d): %s",
                attempt, MAX_RETRIES, exc,
            )
            last_error = exc

        except Exception as exc:
            logger.error(
                "Ошибка при вызове Gemini (попытка %d/%d): %s",
                attempt, MAX_RETRIES, exc, exc_info=True,
            )
            last_error = exc

    logger.error(
        "Все %d попыток вызова Gemini провалились. Последняя ошибка: %s",
        MAX_RETRIES, last_error,
    )
    return _FALLBACK_RESPONSE
