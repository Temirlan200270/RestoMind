"""
AI Brain — ядро интеллекта бота.
Асинхронный вызов OpenAI (Chat Completions + structured output) с возвратом Pydantic-схемы.
Включает retry при сбоях и fallback при невалидном ответе.
Голос: Whisper (transcriptions) + тот же structured chat; мультимодальный один запрос не используется.
"""

import io
import logging
from typing import Any

from openai import APIError, AsyncOpenAI, RateLimitError
from pydantic import ValidationError

from app.core.config import settings
from app.schemas.ai_schemas import AIBrainResponse
from app.services.order_logic import format_draft_order_context_for_prompt
from app.services.prompts import RESTAURANT_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

# Ленивая инициализация: при импорте модуля ключа может не быть (CI, тесты).
_openai_client: AsyncOpenAI | None = None


def _ensure_openai_client() -> AsyncOpenAI | None:
    """Возвращает клиент OpenAI или None, если ключ не задан."""
    global _openai_client
    if _openai_client is not None:
        return _openai_client
    key = settings.openai_api_key.strip()
    if not key:
        return None
    _openai_client = AsyncOpenAI(api_key=key)
    return _openai_client


DEFAULT_CHAT_MODEL = "gpt-4o-mini"
MAX_RETRIES = 2

_FALLBACK_RESPONSE = AIBrainResponse(
    intent="escalate",
    reply_text="Прошу прощения, у меня возникли технические сложности. Переключаю на оператора.",
)

VOICE_FROM_WHISPER_INSTRUCTION = (
    "Клиент прислал голосовое в WhatsApp; ниже — распознанный текст (Whisper). "
    "Определи намерение и заполни AIBrainResponse. "
    "В recognized_speech укажи дословную или слегка нормализованную формулировку речи на языке клиента. "
    "Поле detected_language должно соответствовать основному языку reply_text."
)


def normalize_audio_mime(mime: str) -> str:
    """Нормализует MIME (убирает codecs=..., дефолт для неизвестного)."""
    base = (mime or "").split(";")[0].strip().lower()
    if not base or base == "application/octet-stream":
        return "audio/ogg"
    return base


def _whisper_filename(mime: str) -> str:
    """Имя файла для multipart Whisper (по расширению сервер угадывает формат)."""
    base = normalize_audio_mime(mime)
    ext_map: dict[str, str] = {
        "audio/ogg": ".ogg",
        "audio/opus": ".ogg",
        "audio/webm": ".webm",
        "audio/wav": ".wav",
        "audio/x-wav": ".wav",
        "audio/wave": ".wav",
        "audio/mpeg": ".mp3",
        "audio/mp3": ".mp3",
        "audio/mp4": ".m4a",
        "audio/m4a": ".m4a",
        "audio/x-m4a": ".m4a",
    }
    return f"audio{ext_map.get(base, '.ogg')}"


def _system_prompt_with_context(
    menu_context: str,
    kb_context: str,
    draft_order_context: str = "",
    sales_strategy_context: str = "",
) -> str:
    system_prompt = RESTAURANT_SYSTEM_PROMPT
    if kb_context:
        system_prompt += f"\n\n# Справочник заведения (база знаний)\n{kb_context}"
    if menu_context:
        system_prompt += f"\n\n# Актуальное меню ресторана\n{menu_context}"
    if (draft_order_context or "").strip():
        system_prompt += f"\n\n{draft_order_context.strip()}"
    if (sales_strategy_context or "").strip():
        system_prompt += f"\n\n{sales_strategy_context.strip()}"
    return system_prompt


def _history_to_openai_messages(history: list[dict[str, str]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for msg in history:
        role = msg["role"]
        if role == "model":
            role = "assistant"
        if role not in ("user", "assistant", "system"):
            role = "user"
        out.append({"role": role, "content": msg["content"]})
    return out


def _chat_model() -> str:
    m = (settings.openai_model or "").strip()
    return m if m else DEFAULT_CHAT_MODEL


def _transcription_model() -> str:
    m = (settings.openai_transcription_model or "").strip()
    return m if m else "whisper-1"


async def call_openai(
    history: list[dict[str, str]],
    user_text: str,
    menu_context: str = "",
    kb_context: str = "",
    draft_order_context: str = "",
    sales_strategy_context: str = "",
) -> AIBrainResponse:
    """
    Отправляет контекст диалога в OpenAI и получает структурированный ответ.
    При сбое — до MAX_RETRIES повторных попыток, затем fallback на escalate.
    """
    system_prompt = _system_prompt_with_context(
        menu_context, kb_context, draft_order_context, sales_strategy_context,
    )
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    messages.extend(_history_to_openai_messages(history))
    messages.append({"role": "user", "content": user_text})

    logger.debug(
        "Запрос к OpenAI: %d сообщений в контексте, новое: '%s'",
        len(messages),
        user_text[:100],
    )

    client = _ensure_openai_client()
    if client is None:
        logger.error("OPENAI_API_KEY не задан — ответ недоступен, используем fallback")
        return _FALLBACK_RESPONSE

    last_error: Exception | None = None
    model = _chat_model()

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            completion = await client.beta.chat.completions.parse(
                model=model,
                messages=messages,
                response_format=AIBrainResponse,
                temperature=0.3,
            )
            msg = completion.choices[0].message
            if getattr(msg, "refusal", None):
                logger.warning(
                    "OpenAI отказ (попытка %d/%d): %s",
                    attempt, MAX_RETRIES, msg.refusal,
                )
                last_error = ValueError("Model refusal")
                continue
            parsed = msg.parsed
            if parsed is not None:
                logger.info(
                    "Ответ OpenAI: intent=%s, reply='%s'",
                    parsed.intent,
                    parsed.reply_text[:80],
                )
                return parsed
            raw = (msg.content or "").strip()
            if raw:
                result = AIBrainResponse.model_validate_json(raw)
                logger.info(
                    "Ответ OpenAI (из content): intent=%s, reply='%s'",
                    result.intent,
                    result.reply_text[:80],
                )
                return result
            logger.warning("OpenAI вернул пустой ответ (попытка %d/%d)", attempt, MAX_RETRIES)
            last_error = ValueError("Empty response")

        except ValidationError as exc:
            logger.error(
                "OpenAI: невалидная схема (попытка %d/%d): %s",
                attempt, MAX_RETRIES, exc,
            )
            last_error = exc

        except (APIError, RateLimitError) as exc:
            logger.error(
                "Ошибка API OpenAI (попытка %d/%d): %s",
                attempt, MAX_RETRIES, exc,
                exc_info=True,
            )
            last_error = exc

        except Exception as exc:
            logger.error(
                "Ошибка при вызове OpenAI (попытка %d/%d): %s",
                attempt, MAX_RETRIES, exc,
                exc_info=True,
            )
            last_error = exc

    logger.error(
        "Все %d попыток вызова OpenAI провалились. Последняя ошибка: %s",
        MAX_RETRIES, last_error,
    )
    return _FALLBACK_RESPONSE


async def openai_transcribe_voice(audio_bytes: bytes, audio_mime: str) -> str:
    """
    Расшифровка голоса (Whisper): для Да/Нет, HUMAN_MODE и логов.
    """
    client = _ensure_openai_client()
    if client is None:
        return ""
    if not audio_bytes:
        return ""
    fname = _whisper_filename(audio_mime)
    stt_model = _transcription_model()

    for attempt in range(1, MAX_RETRIES + 1):
        buf = io.BytesIO(audio_bytes)
        try:
            tr = await client.audio.transcriptions.create(
                model=stt_model,
                file=(fname, buf),
            )
            text = getattr(tr, "text", None) or ""
            return text.strip()
        except Exception as exc:
            logger.warning(
                "openai_transcribe_voice попытка %d/%d: %s",
                attempt, MAX_RETRIES, exc,
            )
    return ""


async def call_openai_with_audio(
    history: list[dict[str, str]],
    audio_bytes: bytes,
    audio_mime: str,
    menu_context: str = "",
    kb_context: str = "",
    draft_order_context: str = "",
    sales_strategy_context: str = "",
) -> AIBrainResponse:
    """
    Голосовой ввод: Whisper → structured chat с тем же контрактом AIBrainResponse.
    Поле recognized_speech заполняется транскриптом (источник STT).
    """
    mime = normalize_audio_mime(audio_mime)
    logger.debug(
        "Запрос к OpenAI (голос): %d сообщений в истории, mime=%s, размер=%d байт",
        len(history),
        mime,
        len(audio_bytes),
    )

    client = _ensure_openai_client()
    if client is None:
        logger.error("OPENAI_API_KEY не задан — голос недоступен, fallback")
        return _FALLBACK_RESPONSE

    transcript = await openai_transcribe_voice(audio_bytes, audio_mime)
    transcript = (transcript or "").strip()
    if not transcript:
        logger.warning("Whisper вернул пустой транскрипт — fallback")
        return _FALLBACK_RESPONSE

    augmented = (
        f"{VOICE_FROM_WHISPER_INSTRUCTION}\n\nТекст: {transcript}\n"
        "Ответь клиенту; при необходимости уточни recognized_speech относительно этого текста."
    )
    result = await call_openai(
        history,
        augmented,
        menu_context,
        kb_context,
        draft_order_context,
        sales_strategy_context,
    )
    if result.recognized_speech is None or not str(result.recognized_speech).strip():
        return result.model_copy(update={"recognized_speech": transcript})
    return result
