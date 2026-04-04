"""
AI Brain — ядро интеллекта бота.
Асинхронные вызовы OpenAI Chat Completions (JSON object) + Whisper для STT.
Retry при сбоях и fallback при невалидном ответе.
"""

from __future__ import annotations

import io
import logging
from typing import Any

from openai import AsyncOpenAI
from pydantic import ValidationError

from app.core.config import settings
from app.schemas.ai_schemas import AIBrainResponse
from app.services.prompts import RESTAURANT_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

_openai_client: AsyncOpenAI | None = None
_cached_openai_key: str = ""


def _ensure_openai_client() -> AsyncOpenAI | None:
    """Клиент OpenAI или None, если ключ не задан."""
    global _openai_client, _cached_openai_key
    key = (settings.openai_api_key or "").strip()
    if not key:
        return None
    if _openai_client is None or _cached_openai_key != key:
        _cached_openai_key = key
        kw: dict[str, Any] = {"api_key": key}
        base = (settings.openai_base_url or "").strip()
        if base:
            kw["base_url"] = base
        _openai_client = AsyncOpenAI(**kw)
    return _openai_client


DEFAULT_CHAT_MODEL = "gpt-4o-mini"
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
    """Нормализует MIME для Whisper (убирает codecs=..., дефолт для неизвестного)."""
    base = (mime or "").split(";")[0].strip().lower()
    if not base or base == "application/octet-stream":
        return "audio/ogg"
    return base


def _mime_to_filename(mime: str) -> str:
    m = normalize_audio_mime(mime)
    if m in ("audio/wav", "audio/x-wav", "audio/wave"):
        return "audio.wav"
    if m in ("audio/mpeg", "audio/mp3"):
        return "audio.mp3"
    if m in ("audio/webm",):
        return "audio.webm"
    if m in ("audio/mp4", "audio/m4a", "audio/x-m4a"):
        return "audio.m4a"
    return "audio.ogg"


def _system_prompt_with_context(
    menu_context: str,
    kb_context: str,
    draft_order_context: str = "",
    sales_strategy_context: str = "",
    customer_context: str = "",
) -> str:
    system_prompt = RESTAURANT_SYSTEM_PROMPT
    if (customer_context or "").strip():
        system_prompt += (
            "\n\n# Досье гостя (только факты с сервера; для тона и узнавания)\n"
            f"{customer_context.strip()}"
        )
    if kb_context:
        system_prompt += f"\n\n# Справочник заведения (база знаний)\n{kb_context}"
    if menu_context:
        system_prompt += f"\n\n# Актуальное меню ресторана\n{menu_context}"
    if (draft_order_context or "").strip():
        system_prompt += f"\n\n{draft_order_context.strip()}"
    if (sales_strategy_context or "").strip():
        system_prompt += f"\n\n{sales_strategy_context.strip()}"
    return system_prompt


def _history_to_chat_messages(
    system_prompt: str,
    history: list[dict[str, str]],
    user_text: str,
) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
    for msg in history:
        role = msg.get("role") or "user"
        content = msg.get("content") or ""
        if role == "assistant":
            messages.append({"role": "assistant", "content": content})
        else:
            messages.append({"role": "user", "content": content})
    messages.append({"role": "user", "content": user_text})
    return messages


def _chat_model() -> str:
    m = (settings.openai_model or "").strip()
    return m if m else DEFAULT_CHAT_MODEL


async def call_openai(
    history: list[dict[str, str]],
    user_text: str,
    menu_context: str = "",
    kb_context: str = "",
    draft_order_context: str = "",
    sales_strategy_context: str = "",
    customer_context: str = "",
) -> AIBrainResponse:
    """
    Отправляет контекст в OpenAI и получает структурированный ответ (JSON → AIBrainResponse).
    """
    system_prompt = _system_prompt_with_context(
        menu_context,
        kb_context,
        draft_order_context,
        sales_strategy_context,
        customer_context=customer_context,
    )
    messages = _history_to_chat_messages(system_prompt, history, user_text)

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
            response = await client.chat.completions.create(
                model=model,
                messages=messages,
                response_format={"type": "json_object"},
                temperature=0.3,
            )
            raw = (response.choices[0].message.content or "").strip()
            if not raw:
                logger.warning("OpenAI вернул пустой ответ (попытка %d/%d)", attempt, MAX_RETRIES)
                last_error = ValueError("Empty response")
                continue

            result = AIBrainResponse.model_validate_json(raw)

            logger.info(
                "Ответ OpenAI: intent=%s, reply='%s'",
                result.intent,
                result.reply_text[:80],
            )
            return result

        except ValidationError as exc:
            logger.error(
                "OpenAI вернул невалидный JSON (попытка %d/%d): %s",
                attempt, MAX_RETRIES, exc,
            )
            last_error = exc

        except Exception as exc:
            logger.error(
                "Ошибка при вызове OpenAI (попытка %d/%d): %s",
                attempt, MAX_RETRIES, exc, exc_info=True,
            )
            last_error = exc

    logger.error(
        "Все %d попыток вызова OpenAI провалились. Последняя ошибка: %s",
        MAX_RETRIES, last_error,
    )
    return _FALLBACK_RESPONSE


async def openai_transcribe_voice(audio_bytes: bytes, audio_mime: str) -> str:
    """Расшифровка голоса через Whisper (без JSON): Да/Нет, HUMAN_MODE, логи."""
    client = _ensure_openai_client()
    if client is None:
        return ""
    filename = _mime_to_filename(audio_mime)
    buf = io.BytesIO(audio_bytes)
    buf.name = filename

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            buf.seek(0)
            tr = await client.audio.transcriptions.create(
                model="whisper-1",
                file=buf,
            )
            return (tr.text or "").strip()
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
    customer_context: str = "",
) -> AIBrainResponse:
    """
    Голосовое: сначала Whisper, затем тот же чат с текстом + инструкция VOICE_CHAT_BRIDGE.
    """
    transcript = await openai_transcribe_voice(audio_bytes, audio_mime)
    if not transcript.strip():
        logger.warning("Whisper: пустая расшифровка — fallback")
        return _FALLBACK_RESPONSE

    user_payload = f"{VOICE_CHAT_BRIDGE}\n\nРаспознанная речь клиента:\n{transcript}"
    return await call_openai(
        history,
        user_payload,
        menu_context=menu_context,
        kb_context=kb_context,
        draft_order_context=draft_order_context,
        sales_strategy_context=sales_strategy_context,
        customer_context=customer_context,
    )
