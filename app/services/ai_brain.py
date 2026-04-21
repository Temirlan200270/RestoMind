"""
AI Brain — точка входа AI-Engine v2.0.

- `call_ai(...)` — AI-агностичный диспетчер провайдеров (OpenAI/Gemini).
- `call_openai(...)` — обратная совместимость (alias на `call_ai`).
- Голос: STT и full-stack ответ — провайдер-агностичны (`AI_PROVIDER` решает: Whisper или Gemini multimodal).
"""

from __future__ import annotations

import logging
import time
from typing import Any

from openai import AsyncOpenAI

from app.core.config import settings
from app.schemas.ai_schemas import AIBrainResponse
from app.services.ai_engine.base import BaseAIProvider
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


VOICE_FROM_STT_INSTRUCTION = (
    "Клиент прислал голосовое в WhatsApp; ниже — расшифрованный текст. "
    "Определи намерение и заполни AIBrainResponse. "
    "В recognized_speech укажи дословную или слегка нормализованную формулировку речи на языке клиента. "
    "Поле detected_language должно соответствовать основному языку reply_text."
)
# Backward-compat (внешний код может импортировать старое имя).
VOICE_FROM_WHISPER_INSTRUCTION = VOICE_FROM_STT_INSTRUCTION


def normalize_audio_mime(mime: str) -> str:
    """Нормализует MIME (убирает codecs=..., дефолт для неизвестного)."""
    base = (mime or "").split(";")[0].strip().lower()
    if not base or base == "application/octet-stream":
        return "audio/ogg"
    return base


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


def get_ai_client() -> BaseAIProvider:
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


def voice_supported() -> bool:
    """
    True, если активный AI-провайдер настроен и умеет распознавать аудио.
    Используется вебхуками, чтобы корректно отвечать клиенту, если ключи не заданы.
    """
    try:
        return bool(get_ai_client().supports_voice())
    except Exception:
        return False


async def transcribe_voice(audio_bytes: bytes, audio_mime: str) -> str:
    """
    Провайдер-агностичное STT: при AI_PROVIDER=openai — Whisper,
    при AI_PROVIDER=gemini — Gemini multimodal. Возвращает "" при неудаче.
    """
    if not audio_bytes:
        return ""
    provider = get_ai_client()
    try:
        return await provider.transcribe_voice(audio_bytes=audio_bytes, audio_mime=audio_mime)
    except Exception as exc:
        # Защита от падений транспорта/SDK: пайплайн должен решить, что показать клиенту.
        logger.warning(
            "transcribe_voice провайдер=%s упал: %s",
            type(provider).__name__,
            exc,
        )
        return ""


# Backward-compat: имя сохраняем — но реализация уже провайдер-агностичная.
async def openai_transcribe_voice(audio_bytes: bytes, audio_mime: str) -> str:
    return await transcribe_voice(audio_bytes, audio_mime)


async def call_ai_with_audio(
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
    Голосовой ввод: STT (через активного провайдера) → structured chat (через того же провайдера),
    общий контракт AIBrainResponse. `recognized_speech` заполняется транскриптом, если модель
    сама его не вернула.
    """
    mime = normalize_audio_mime(audio_mime)
    provider = get_ai_client()
    logger.debug(
        "AI (голос) provider=%s: %d сообщений в истории, mime=%s, размер=%d байт",
        type(provider).__name__,
        len(history),
        mime,
        len(audio_bytes),
    )

    if not provider.supports_voice():
        logger.error(
            "AI-провайдер %s не настроен для голоса — fallback escalate",
            type(provider).__name__,
        )
        return _FALLBACK_RESPONSE

    transcript = (await transcribe_voice(audio_bytes, audio_mime) or "").strip()
    if not transcript:
        logger.warning("STT (%s) вернул пустой транскрипт — fallback", type(provider).__name__)
        return _FALLBACK_RESPONSE

    augmented = (
        f"{VOICE_FROM_STT_INSTRUCTION}\n\nТекст: {transcript}\n"
        "Ответь клиенту; при необходимости уточни recognized_speech относительно этого текста."
    )
    result = await call_ai(
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


# Backward-compat alias — старое имя продолжает работать.
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
    return await call_ai_with_audio(
        history,
        audio_bytes,
        audio_mime,
        menu_context,
        kb_context,
        draft_order_context,
        sales_strategy_context,
        customer_context=customer_context,
        current_time_context=current_time_context,
    )
