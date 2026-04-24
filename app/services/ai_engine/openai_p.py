from __future__ import annotations

import io
import logging
import time
from typing import Any

from openai import APIConnectionError, APIError, APITimeoutError, AsyncOpenAI, RateLimitError
from pydantic import ValidationError

from app.core.ai_constants import AI_PRESETS
from app.core.config import settings
from app.schemas.ai_schemas import AIBrainResponse
from app.services.ai_engine.base import BaseAIProvider
from app.services.ai_engine.errors import TransientAiError
from app.services.ai_engine.prompting import build_system_prompt

logger = logging.getLogger(__name__)


_STT_MAX_RETRIES = 2

_AUDIO_EXT_BY_MIME: dict[str, str] = {
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


def _normalize_audio_mime(mime: str) -> str:
    base = (mime or "").split(";")[0].strip().lower()
    if not base or base == "application/octet-stream":
        return "audio/ogg"
    return base


def _whisper_filename(mime: str) -> str:
    base = _normalize_audio_mime(mime)
    return f"audio{_AUDIO_EXT_BY_MIME.get(base, '.ogg')}"


def _history_to_openai_messages(history: list[dict[str, str]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for msg in history:
        role = msg.get("role", "user")
        if role == "model":
            role = "assistant"
        if role not in ("user", "assistant", "system"):
            role = "user"
        out.append({"role": role, "content": msg.get("content", "")})
    return out


class OpenAIProvider(BaseAIProvider):
    def __init__(self) -> None:
        self._client: AsyncOpenAI | None = None
        self._cached_key: str = ""
        self._cached_base: str = ""

    def _ensure_client(self) -> AsyncOpenAI | None:
        # Reuse legacy helper for backward-compatible tests/patching.
        # tests patch `app.services.ai_brain._ensure_openai_client` directly.
        try:
            from app.services.ai_brain import _ensure_openai_client  # local import avoids cycles

            return _ensure_openai_client()
        except Exception:
            # Fallback to local implementation if ai_brain import isn't available.
            key = (settings.openai_api_key or "").strip()
            if not key:
                return None
            base = (settings.openai_base_url or "").strip()
            if self._client is None or self._cached_key != key or self._cached_base != base:
                self._cached_key = key
                self._cached_base = base
                kw: dict[str, Any] = {"api_key": key}
                if base:
                    kw["base_url"] = base
                self._client = AsyncOpenAI(**kw)
            return self._client

    @property
    def _model(self) -> str:
        # Model selection is preset-driven (env model names are intentionally ignored).
        return AI_PRESETS["openai"].models[0]

    async def generate_response(
        self,
        *,
        history: list[dict[str, str]],
        user_text: str,
        menu_context: str = "",
        kb_context: str = "",
        draft_order_context: str = "",
        sales_strategy_context: str = "",
        customer_context: str = "",
        current_time_context: str = "",
    ) -> AIBrainResponse:
        client = self._ensure_client()
        if client is None:
            logger.error("[AI] provider=openai status=NO_KEY")
            return AIBrainResponse(
                intent="escalate",
                reply_text="Прошу прощения, у меня возникли технические сложности. Переключаю на оператора.",
            )

        system_prompt = build_system_prompt(
            menu_context=menu_context,
            kb_context=kb_context,
            draft_order_context=draft_order_context,
            sales_strategy_context=sales_strategy_context,
            customer_context=customer_context,
            current_time_context=current_time_context,
        )
        messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        messages.extend(_history_to_openai_messages(history))
        messages.append({"role": "user", "content": user_text})

        max_retries = 2
        last_error: Exception | None = None
        model = self._model

        for attempt in range(1, max_retries + 1):
            t0 = time.perf_counter()
            try:
                completion = await client.beta.chat.completions.parse(
                    model=model,
                    messages=messages,
                    response_format=AIBrainResponse,
                    temperature=0.3,
                )
                msg = completion.choices[0].message
                if getattr(msg, "refusal", None):
                    last_error = ValueError("Model refusal")
                    logger.warning(
                        "[AI] provider=openai model=%s attempt=%d status=REFUSAL latency_ms=%d",
                        model,
                        attempt,
                        int((time.perf_counter() - t0) * 1000),
                    )
                    continue

                parsed = msg.parsed
                if parsed is not None:
                    try:
                        us = getattr(completion, "usage", None)
                        if us is not None:
                            pt = getattr(us, "prompt_tokens", None)
                            ct = getattr(us, "completion_tokens", None)
                            tt = getattr(us, "total_tokens", None)
                            logger.info(
                                "[AI] provider=openai model=%s usage prompt=%s completion=%s total=%s",
                                model,
                                pt,
                                ct,
                                tt,
                            )
                    except Exception:
                        pass
                    logger.info(
                        "[AI] provider=openai model=%s attempt=%d status=SUCCESS latency_ms=%d intent=%s",
                        model,
                        attempt,
                        int((time.perf_counter() - t0) * 1000),
                        parsed.intent,
                    )
                    return parsed

                raw = (msg.content or "").strip()
                if raw:
                    result = AIBrainResponse.model_validate_json(raw)
                    logger.info(
                        "[AI] provider=openai model=%s attempt=%d status=SUCCESS_FROM_CONTENT latency_ms=%d intent=%s",
                        model,
                        attempt,
                        int((time.perf_counter() - t0) * 1000),
                        result.intent,
                    )
                    return result

                last_error = ValueError("Empty response")
                logger.warning(
                    "[AI] provider=openai model=%s attempt=%d status=EMPTY latency_ms=%d",
                    model,
                    attempt,
                    int((time.perf_counter() - t0) * 1000),
                )

            except ValidationError as exc:
                last_error = exc
                logger.warning(
                    "[AI] provider=openai model=%s attempt=%d status=VALIDATION_ERROR latency_ms=%d err=%s",
                    model,
                    attempt,
                    int((time.perf_counter() - t0) * 1000),
                    type(exc).__name__,
                )

            except (APIError, RateLimitError, APIConnectionError, APITimeoutError) as exc:
                last_error = exc
                logger.warning(
                    "[AI] provider=openai model=%s attempt=%d status=API_ERROR latency_ms=%d err=%s",
                    model,
                    attempt,
                    int((time.perf_counter() - t0) * 1000),
                    type(exc).__name__,
                    exc_info=True,
                )

            except Exception as exc:
                last_error = exc
                logger.warning(
                    "[AI] provider=openai model=%s attempt=%d status=ERROR latency_ms=%d err=%s",
                    model,
                    attempt,
                    int((time.perf_counter() - t0) * 1000),
                    type(exc).__name__,
                    exc_info=True,
                )

        if isinstance(last_error, (APIError, RateLimitError, APIConnectionError, APITimeoutError)):
            raise TransientAiError(str(last_error)) from last_error

        logger.error(
            "[AI] provider=openai model=%s status=FAILED err=%s",
            model,
            type(last_error).__name__ if last_error else "Unknown",
        )
        return AIBrainResponse(
            intent="escalate",
            reply_text="Прошу прощения, у меня возникли технические сложности. Переключаю на оператора.",
        )

    # --- Голос (Whisper STT) ---

    def supports_voice(self) -> bool:
        # Сознательно идём через _ensure_client(): тесты патчат `_ensure_openai_client`,
        # и это обеспечивает один источник истины о доступности OpenAI (ключ или прокси).
        return self._ensure_client() is not None

    async def transcribe_voice(
        self,
        *,
        audio_bytes: bytes,
        audio_mime: str,
    ) -> str:
        if not audio_bytes:
            return ""
        client = self._ensure_client()
        if client is None:
            logger.error("[AI] provider=openai stt status=NO_KEY")
            return ""

        fname = _whisper_filename(audio_mime)
        stt_model = (settings.openai_transcription_model or "").strip() or "whisper-1"

        for attempt in range(1, _STT_MAX_RETRIES + 1):
            buf = io.BytesIO(audio_bytes)
            try:
                tr = await client.audio.transcriptions.create(
                    model=stt_model,
                    file=(fname, buf),
                )
                text = (getattr(tr, "text", None) or "").strip()
                if text:
                    return text
                logger.warning(
                    "[AI] provider=openai stt model=%s attempt=%d status=EMPTY",
                    stt_model,
                    attempt,
                )
            except Exception as exc:
                logger.warning(
                    "[AI] provider=openai stt model=%s attempt=%d/%d status=ERROR err=%s",
                    stt_model,
                    attempt,
                    _STT_MAX_RETRIES,
                    type(exc).__name__,
                )
        return ""

