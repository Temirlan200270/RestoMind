"""
AI Brain — точка входа AI-Engine v2.0.

- `call_ai(...)` — AI-агностичный диспетчер провайдеров (OpenAI/Gemini).
- `call_openai(...)` — обратная совместимость (alias на `call_ai`).
- Голос (Whisper STT) пока реализован через OpenAI (опциональный компонент).
"""

from __future__ import annotations

import io
import logging
import time
from typing import Any

from openai import AsyncOpenAI

from app.core.config import settings
from app.schemas.ai_schemas import AIBrainResponse
from app.services.ai_engine.errors import TransientAiError
from app.services.ai_engine.gemini_p import GeminiProvider
from app.services.ai_engine.openai_p import OpenAIProvider

logger = logging.getLogger(__name__)

_openai_client: AsyncOpenAI | None = None
_cached_openai_key: str = ""
_cached_openai_base: str = ""

_openai_provider: OpenAIProvider | None = None
_gemini_provider: GeminiProvider | None = None


def _ensure_openai_client() -> AsyncOpenAI | None:
    """Клиент OpenAI или None, если ключ не задан. Пересоздаётся при смене ключа или base_url."""
    global _openai_client, _cached_openai_key, _cached_openai_base
    key = (settings.openai_api_key or "").strip()
    if not key:
        return None
    base = (settings.openai_base_url or "").strip()
    if _openai_client is None or _cached_openai_key != key or _cached_openai_base != base:
        _cached_openai_key = key
        _cached_openai_base = base
        kw: dict[str, Any] = {"api_key": key}
        if base:
            kw["base_url"] = base
        _openai_client = AsyncOpenAI(**kw)
    return _openai_client


MAX_RETRIES = 2

_FALLBACK_RESPONSE = AIBrainResponse(
    intent="escalate",
    reply_text="Прошу прощения, у меня возникли технические сложности. Переключаю на оператора.",
)


def is_openai_fallback_escalation_reply(reply_text: str) -> bool:
    """True, если ответ совпадает с запасным при сбое OpenAI (квота, сеть и т.д.)."""
    return (reply_text or "").strip() == _FALLBACK_RESPONSE.reply_text.strip()


VOICE_FROM_WHISPER_INSTRUCTION = (
    "Клиент прислал голосовое в WhatsApp; ниже — распознанный текст (Whisper). "
    "Определи намерение и заполни AIBrainResponse. "
    "В recognized_speech укажи дословную или слегка нормализованную формулировку речи на языке клиента. "
    "Поле detected_language должно соответствовать основному языку reply_text."
)


def normalize_audio_mime(mime: str) -> str:
    """Нормализует MIME для Whisper (убирает codecs=..., дефолт для неизвестного)."""
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
    customer_context: str = "",
    current_time_context: str = "",
) -> str:
    # Backward-compat: keep signature used by callers/tests, but delegate to shared builder.
    from app.services.ai_engine.prompting import build_system_prompt

    return build_system_prompt(
        menu_context=menu_context,
        kb_context=kb_context,
        draft_order_context=draft_order_context,
        sales_strategy_context=sales_strategy_context,
        customer_context=customer_context,
        current_time_context=current_time_context,
    )


def _transcription_model() -> str:
    m = (settings.openai_transcription_model or "").strip()
    return m if m else "whisper-1"


def get_ai_client() -> object:
    """
    Фабрика провайдеров AI-Engine v2.0.
    Выбор — только по settings.AI_PROVIDER (gemini|openai).
    """
    global _openai_provider, _gemini_provider
    prov = (getattr(settings, "ai_provider", "") or "").strip().lower()
    if prov == "gemini":
        if _gemini_provider is None:
            _gemini_provider = GeminiProvider()
        return _gemini_provider
    if _openai_provider is None:
        _openai_provider = OpenAIProvider()
    return _openai_provider


async def call_ai(
    history: list[dict[str, str]],
    user_text: str,
    menu_context: str = "",
    kb_context: str = "",
    draft_order_context: str = "",
    sales_strategy_context: str = "",
    customer_context: str = "",
    current_time_context: str = "",
    *,
    raise_on_transient: bool = True,
) -> AIBrainResponse:
    """AI-агностичный вызов: провайдер выбирается по AI_PROVIDER."""
    provider = get_ai_client()
    t0 = time.perf_counter()
    try:
        result = await provider.generate_response(
            history=history,
            user_text=user_text,
            menu_context=menu_context,
            kb_context=kb_context,
            draft_order_context=draft_order_context,
            sales_strategy_context=sales_strategy_context,
            customer_context=customer_context,
            current_time_context=current_time_context,
        )
        logger.info(
            "[AI] dispatch provider=%s status=SUCCESS latency_ms=%d intent=%s",
            type(provider).__name__,
            int((time.perf_counter() - t0) * 1000),
            result.intent,
        )
        return result
    except TransientAiError:
        logger.warning(
            "[AI] dispatch provider=%s status=TRANSIENT latency_ms=%d",
            type(provider).__name__,
            int((time.perf_counter() - t0) * 1000),
        )
        if raise_on_transient:
            raise
        return _FALLBACK_RESPONSE


async def call_openai(
    history: list[dict[str, str]],
    user_text: str,
    menu_context: str = "",
    kb_context: str = "",
    draft_order_context: str = "",
    sales_strategy_context: str = "",
    customer_context: str = "",
    current_time_context: str = "",
    *,
    raise_on_transient: bool = True,
) -> AIBrainResponse:
    """Backward-compat alias (AI-Engine v2.0)."""
    return await call_ai(
        history,
        user_text,
        menu_context,
        kb_context,
        draft_order_context,
        sales_strategy_context,
        customer_context=customer_context,
        current_time_context=current_time_context,
        raise_on_transient=raise_on_transient,
    )


async def openai_transcribe_voice(audio_bytes: bytes, audio_mime: str) -> str:
    """Расшифровка голоса (Whisper): для Да/Нет, HUMAN_MODE и логов."""
    client = _ensure_openai_client()
    if client is None or not audio_bytes:
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
                attempt,
                MAX_RETRIES,
                exc,
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
    customer_context: str = "",
    current_time_context: str = "",
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
        customer_context=customer_context,
        current_time_context=current_time_context,
        raise_on_transient=False,
    )
    if result.recognized_speech is None or not str(result.recognized_speech).strip():
        return result.model_copy(update={"recognized_speech": transcript})
    return result
