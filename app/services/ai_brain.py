"""
AI Brain — ядро интеллекта бота.
Асинхронный вызов Gemini API с гарантированным возвратом Pydantic-схемы (Structured Outputs).
Включает retry при сбоях и fallback при невалидном ответе.
Голос в WhatsApp: мультимодальный запрос к Gemini (без платного Whisper).
"""

import logging
from typing import Any

from google import genai
from google.genai import types
from pydantic import ValidationError

from app.core.config import settings
from app.schemas.ai_schemas import AIBrainResponse
from app.services.order_logic import format_draft_order_context_for_prompt
from app.services.prompts import RESTAURANT_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

# Ленивая инициализация: при импорте модуля ключа может не быть (CI, тесты).
_gemini_client: Any = None


def _ensure_gemini_client() -> Any:
    """Возвращает клиент Gemini или None, если ключ не задан."""
    global _gemini_client
    if _gemini_client is not None:
        return _gemini_client
    key = settings.gemini_api_key.strip()
    if not key:
        return None
    _gemini_client = genai.Client(api_key=key)
    return _gemini_client


DEFAULT_MODEL = "gemini-2.5-flash"
MAX_RETRIES = 2

_FALLBACK_RESPONSE = AIBrainResponse(
    intent="escalate",
    reply_text="Прошу прощения, у меня возникли технические сложности. Переключаю на оператора.",
)

VOICE_CHAT_BRIDGE = (
    "Клиент прислал голосовое сообщение в WhatsApp (аудио выше). "
    "Прослушай, определи намерение и заполни JSON-схему AIBrainResponse. "
    "В поле recognized_speech укажи краткую дословную расшифровку речи клиента на его языке. "
    "Поле detected_language должно соответствовать основному языку reply_text."
)


def normalize_audio_mime(mime: str) -> str:
    """Нормализует MIME для Gemini (убирает codecs=..., дефолт для неизвестного)."""
    base = (mime or "").split(";")[0].strip().lower()
    if not base or base == "application/octet-stream":
        return "audio/ogg"
    return base


def _system_prompt_with_context(
    menu_context: str,
    kb_context: str,
    draft_order_context: str = "",
) -> str:
    system_prompt = RESTAURANT_SYSTEM_PROMPT
    if kb_context:
        system_prompt += f"\n\n# Справочник заведения (база знаний)\n{kb_context}"
    if menu_context:
        system_prompt += f"\n\n# Актуальное меню ресторана\n{menu_context}"
    if (draft_order_context or "").strip():
        system_prompt += f"\n\n{draft_order_context.strip()}"
    return system_prompt


def _history_to_gemini_contents(history: list[dict[str, str]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for msg in history:
        role = msg["role"]
        if role == "assistant":
            role = "model"
        out.append({"role": role, "parts": [{"text": msg["content"]}]})
    return out


def _structured_output_config(system_prompt: str) -> dict[str, Any]:
    return {
        "system_instruction": system_prompt,
        "response_mime_type": "application/json",
        "response_json_schema": AIBrainResponse.model_json_schema(),
        "temperature": 0.3,
    }


async def call_gemini(
    history: list[dict[str, str]],
    user_text: str,
    menu_context: str = "",
    kb_context: str = "",
    draft_order_context: str = "",
) -> AIBrainResponse:
    """
    Отправляет контекст диалога в Gemini и получает структурированный ответ.
    При сбое — до MAX_RETRIES повторных попыток, затем fallback на escalate.

    Args:
        history: Предыдущие сообщения диалога [{role: "user"/"model", content: "..."}].
        user_text: Новое сообщение от пользователя.
        menu_context: Текстовое описание актуального меню с ценами.
        kb_context: Блок базы знаний (справочник заведения).
        draft_order_context: «Память корзины»: текстовый снимок активного DRAFT из БД
            (`format_draft_order_context_for_prompt` в `webhooks` / test-bot). Нужен для дельт `order_actions`.

    Returns:
        AIBrainResponse — Pydantic-объект с intent, reply_text, items, booking_details.
    """
    system_prompt = _system_prompt_with_context(menu_context, kb_context, draft_order_context)
    contents = _history_to_gemini_contents(history)
    contents.append({"role": "user", "parts": [{"text": user_text}]})

    logger.debug(
        "Запрос к Gemini: %d сообщений в контексте, новое: '%s'",
        len(contents),
        user_text[:100],
    )

    client = _ensure_gemini_client()
    if client is None:
        logger.error("GEMINI_API_KEY не задан — ответ недоступен, используем fallback")
        return _FALLBACK_RESPONSE

    last_error: Exception | None = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = await client.aio.models.generate_content(
                model=DEFAULT_MODEL,
                contents=contents,
                config=_structured_output_config(system_prompt),
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


async def gemini_transcribe_voice(audio_bytes: bytes, audio_mime: str) -> str:
    """
    Лёгкая расшифровка голоса (без JSON): для Да/Нет, HUMAN_MODE и логов.
    """
    client = _ensure_gemini_client()
    if client is None:
        return ""
    mime = normalize_audio_mime(audio_mime)
    contents = [
        types.Content(
            role="user",
            parts=[
                types.Part.from_bytes(data=audio_bytes, mime_type=mime),
                types.Part.from_text(
                    text=(
                        "Распознай речь. Выведи только текст сказанного, без кавычек и комментариев. "
                        "Если слышно нечётко — угадай по контексту или верни краткое «не разобрал»."
                    ),
                ),
            ],
        ),
    ]
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = await client.aio.models.generate_content(
                model=DEFAULT_MODEL,
                contents=contents,
                config={"temperature": 0.2},
            )
            return (response.text or "").strip()
        except Exception as exc:
            logger.warning(
                "gemini_transcribe_voice попытка %d/%d: %s",
                attempt, MAX_RETRIES, exc,
            )
    return ""


async def call_gemini_with_audio(
    history: list[dict[str, str]],
    audio_bytes: bytes,
    audio_mime: str,
    menu_context: str = "",
    kb_context: str = "",
    draft_order_context: str = "",
) -> AIBrainResponse:
    """Диалог + голосовое в одном запросе (structured output)."""
    system_prompt = _system_prompt_with_context(menu_context, kb_context, draft_order_context)
    mime = normalize_audio_mime(audio_mime)

    gemini_contents: list[Any] = []
    for msg in history:
        role = msg["role"]
        if role == "assistant":
            role = "model"
        gemini_contents.append(
            types.Content(
                role=role,
                parts=[types.Part.from_text(text=msg["content"])],
            ),
        )
    gemini_contents.append(
        types.Content(
            role="user",
            parts=[
                types.Part.from_bytes(data=audio_bytes, mime_type=mime),
                types.Part.from_text(text=VOICE_CHAT_BRIDGE),
            ],
        ),
    )

    logger.debug(
        "Запрос к Gemini (голос): %d сообщений в контексте, mime=%s, размер=%d байт",
        len(gemini_contents),
        mime,
        len(audio_bytes),
    )

    client = _ensure_gemini_client()
    if client is None:
        logger.error("GEMINI_API_KEY не задан — голос недоступен, fallback")
        return _FALLBACK_RESPONSE

    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = await client.aio.models.generate_content(
                model=DEFAULT_MODEL,
                contents=gemini_contents,
                config=_structured_output_config(system_prompt),
            )
            if not response.text:
                logger.warning("Gemini (голос) пустой ответ (попытка %d/%d)", attempt, MAX_RETRIES)
                last_error = ValueError("Empty response")
                continue
            result = AIBrainResponse.model_validate_json(response.text)
            logger.info(
                "Ответ Gemini (голос): intent=%s, reply='%s'",
                result.intent,
                result.reply_text[:80],
            )
            return result
        except ValidationError as exc:
            logger.error(
                "Gemini (голос) невалидный JSON (попытка %d/%d): %s",
                attempt, MAX_RETRIES, exc,
            )
            last_error = exc
        except Exception as exc:
            logger.error(
                "Ошибка Gemini (голос) попытка %d/%d: %s",
                attempt, MAX_RETRIES, exc,
                exc_info=True,
            )
            last_error = exc

    logger.error(
        "Все попытки call_gemini_with_audio провалились: %s",
        last_error,
    )
    return _FALLBACK_RESPONSE
