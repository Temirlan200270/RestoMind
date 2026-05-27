from __future__ import annotations

import asyncio
import logging
import re
import time
import warnings
from typing import Any

from pydantic import ValidationError

from app.core.ai_constants import AI_PRESETS
from app.core.config import settings
from app.schemas.ai_schemas import AIBrainResponse
from app.services.ai_engine.base import BaseAIProvider, ModelTier
from app.services.ai_engine.errors import TransientAiError
from app.services.ai_engine.prompting import build_system_prompt, format_untrusted_user_text_for_model

logger = logging.getLogger(__name__)


def _history_to_gemini_text(history: list[dict[str, str]]) -> str:
    """
    Gemini provider uses a single prompt string for maximum portability.
    We keep it short and deterministic: role labels + content.
    """
    parts: list[str] = []
    for msg in history:
        role = (msg.get("role") or "user").strip().lower()
        if role == "model":
            role = "assistant"
        if role not in ("user", "assistant", "system"):
            role = "user"
        content = (msg.get("content") or "").strip()
        if not content:
            continue
        parts.append(f"{role.upper()}: {content}")
    return "\n".join(parts)


_JSON_ONLY_INSTRUCTION = (
    "Ответь СТРОГО одним JSON-объектом, без Markdown и без пояснений.\n"
    "Формат JSON должен соответствовать Pydantic-схеме AIBrainResponse.\n"
    "Никакого текста вне JSON.\n"
    "ВАЖНО: для полей, которые к ответу не относятся, НЕ пиши null. "
    "Либо опусти ключ, либо используй дефолт из схемы: "
    "пустая строка \"\" для order_type/payment_method/delivery_address/pickup_time_note, "
    "'single' для payment_mode, false для is_preorder/is_recommendation, "
    "пустой список [] для items/order_actions."
)


# Fenced code block (``` или ```json), non-greedy, через несколько строк.
# DOTALL — чтобы '.' матчил '\n'.
_MARKDOWN_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)


def _extract_json_from_text(raw: str) -> str:
    """
    Извлекает чистый JSON-объект из возможно «обёрнутого» ответа LLM.

    Зачем: Gemini 3 Flash — thinking-модель, она склонна к добавлению преамбул,
    пояснений и Markdown-обёрток (```json ... ```), несмотря на явный системный
    запрет. Также бывают пустые хвостовые символы, которые ломают JSON-парсер.

    Эвристики (по убыванию точности):
      1) Первый Markdown code fence → содержимое фенса.
      2) Подстрока от первой '{' до последней '}' — если оба нашлись.
      3) Возврат исходной строки (пусть Pydantic упадёт с честной ошибкой).

    ВАЖНО: функция не валидирует JSON, она лишь «чистит мусор». Валидация — в
    AIBrainResponse.model_validate_json(). Так мы разделяем ответственности:
    экстрактор знает про Markdown, валидатор — про схему данных.
    """
    stripped = (raw or "").strip()
    if not stripped:
        return stripped

    fence_match = _MARKDOWN_FENCE_RE.search(stripped)
    if fence_match:
        candidate = fence_match.group(1).strip()
        if candidate:
            return candidate

    first_brace = stripped.find("{")
    last_brace = stripped.rfind("}")
    if 0 <= first_brace < last_brace:
        return stripped[first_brace : last_brace + 1]

    return stripped

# Gemini поддерживает inline-аудио: audio/wav, audio/mp3, audio/aiff, audio/aac, audio/ogg, audio/flac.
# WhatsApp обычно отдаёт audio/ogg; codecs=opus — корректно нормализуется в audio/ogg.
_GEMINI_AUDIO_MIME_BY_BASE: dict[str, str] = {
    "audio/ogg": "audio/ogg",
    "audio/opus": "audio/ogg",
    "audio/webm": "audio/ogg",
    "audio/wav": "audio/wav",
    "audio/x-wav": "audio/wav",
    "audio/wave": "audio/wav",
    "audio/mpeg": "audio/mp3",
    "audio/mp3": "audio/mp3",
    "audio/mp4": "audio/aac",
    "audio/m4a": "audio/aac",
    "audio/x-m4a": "audio/aac",
    "audio/aac": "audio/aac",
    "audio/aiff": "audio/aiff",
    "audio/flac": "audio/flac",
}


def _normalize_audio_mime_for_gemini(mime: str) -> str:
    base = (mime or "").split(";")[0].strip().lower()
    if not base:
        return "audio/ogg"
    return _GEMINI_AUDIO_MIME_BY_BASE.get(base, "audio/ogg")


_STT_PROMPT = (
    "Ты — система точного распознавания речи (ASR). "
    "Распознай речь из этого аудио ДОСЛОВНО на исходном языке "
    "(возможны: русский, казахский, английский, узбекский, киргизский, смешанные фразы). "
    "Не переводи. Не добавляй комментарии и пояснения. "
    "Если речи нет или ничего не слышно — ответь пустой строкой."
)

_STT_MAX_RETRIES = 2


# Ошибки уровня провайдера (биллинг, аутентификация, общий лимит).
# Для них нет смысла делать каскадный failover между моделями одного провайдера —
# у них общий ключ и общий billing-аккаунт, ответ будет идентичным.
# Сравниваем по имени класса, чтобы не тянуть жёсткий импорт google.api_core
# (сохраняем lazy-философию модуля: провайдер работает даже если SDK не стоит).
_PROVIDER_LEVEL_ERROR_NAMES: frozenset[str] = frozenset({
    "ResourceExhausted",   # 429 — кончились кредиты / превышен tokens-per-minute / RPM
    "PermissionDenied",    # 403 — ключ валиден, но нет доступа к модели/региону
    "Unauthenticated",     # 401 — ключ невалиден/отозван
})


def _lazy_import_genai() -> Any:
    """
    Ленивый импорт `google.generativeai` с подавлением известного FutureWarning.

    Пакет переведён в maintenance и при импорте пишет FutureWarning в stderr —
    он попадает в прод-логи Render и «шумит» в SRE-дашборде. Миграция на
    `google-genai` — отдельная задача, а сейчас глушим именно ЭТОТ warning,
    максимально узко (по категории и тексту), чтобы другие FutureWarning
    продолжали быть видимыми.
    """
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=r"(?s).*google\.generativeai.*",
            category=FutureWarning,
        )
        import google.generativeai as genai  # type: ignore
    return genai


def _format_validation_error(exc: ValidationError) -> str:
    """
    Компактное человекочитаемое представление ошибок Pydantic для одной строки лога.

    Стандартный `str(exc)` разворачивается на много строк и плохо ищется
    grep'ом в Render/Datadog. Сворачиваем до `field=<loc> type=<error_type>`,
    перечисляя все ошибки через `;`.
    """
    parts: list[str] = []
    for err in exc.errors()[:5]:  # защита от «простыни» при массовой невалидности
        loc = ".".join(str(x) for x in err.get("loc", ())) or "<root>"
        parts.append(f"{loc}[{err.get('type', '?')}]")
    if len(exc.errors()) > 5:
        parts.append(f"(+{len(exc.errors()) - 5} more)")
    return ",".join(parts) or "unknown"


def _is_provider_level_error(exc: BaseException) -> bool:
    """
    True, если ошибка указывает на проблему с провайдером в целом (биллинг/ключ/квоты),
    а не на проблему конкретной модели.

    Пример: `ResourceExhausted: Your prepayment credits are depleted` — переключение
    на другую модель того же провайдера бессмысленно, билинг общий. Уходим в
    TransientAiError немедленно, экономим latency и не тратим квоту «вхолостую».
    """
    return type(exc).__name__ in _PROVIDER_LEVEL_ERROR_NAMES


class GeminiProvider(BaseAIProvider):
    def __init__(self) -> None:
        self._configured_key: str | None = None

    def _ensure_configured(self) -> bool:
        key = (getattr(settings, "gemini_api_key", "") or "").strip()
        if not key:
            return False
        if self._configured_key != key:
            genai = _lazy_import_genai()
            genai.configure(api_key=key)
            self._configured_key = key
        return True

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
        model_tier: ModelTier = "strong",
    ) -> AIBrainResponse:
        if not self._ensure_configured():
            logger.error("[AI] provider=gemini status=NO_KEY")
            return AIBrainResponse(
                intent="escalate",
                reply_text="Прошу прощения, у меня возникли технические сложности. Переключаю на оператора.",
            )

        preset = AI_PRESETS["gemini"]
        if model_tier == "fast" and settings.ai_model_routing_enabled:
            fast = (settings.ai_fast_model_gemini or "").strip()
            model_names: tuple[str, ...] = (fast,) if fast else (preset.models[0],)
        else:
            strong_override = (getattr(settings, "ai_strong_model_gemini", "") or "").strip()
            model_names = (strong_override,) if strong_override else preset.models
        system_prompt = build_system_prompt(
            menu_context=menu_context,
            kb_context=kb_context,
            draft_order_context=draft_order_context,
            sales_strategy_context=sales_strategy_context,
            customer_context=customer_context,
            current_time_context=current_time_context,
        )
        history_block = _history_to_gemini_text(history)

        # Single prompt payload: system + history + user + hard JSON-only constraints.
        user_wrapped = format_untrusted_user_text_for_model(user_text)
        prompt = (
            f"{system_prompt}\n\n"
            f"{_JSON_ONLY_INSTRUCTION}\n\n"
            f"{history_block}\n\n"
            f"{user_wrapped}\n"
        ).strip()

        last_error: Exception | None = None
        # Храним сырой ответ модели между try-блоками, чтобы при ValidationError
        # логировать первые N символов — это главный инсайт для отладки схемы.
        raw_text_for_log: str = ""
        for idx, model_name in enumerate(model_names, start=1):
            t0 = time.perf_counter()
            raw_text_for_log = ""
            try:
                genai = _lazy_import_genai()
                model = genai.GenerativeModel(model_name)
                # Async API is available in google-generativeai; use it to avoid blocking.
                # response_mime_type="application/json" — нативный JSON-режим Gemini:
                # модель обязуется вернуть валидный JSON без Markdown-обёртки. Это
                # защита №1 (работает на уровне API и снижает ValidationError-каскад).
                # Схему (response_schema=AIBrainResponse) не передаём: AIBrainResponse
                # слишком сложна (nullable Unions, nested Pydantic) — SDK-конвертер
                # в JSON Schema может упасть на InvalidArgument для некоторых preview
                # моделей. Валидируем через Pydantic на нашей стороне — это надёжнее.
                resp = await asyncio.wait_for(
                    model.generate_content_async(
                        prompt,
                        generation_config=genai.types.GenerationConfig(
                            temperature=0.3,
                            response_mime_type="application/json",
                        ),
                    ),
                    timeout=float(settings.ai_llm_timeout_sec),
                )
                text = (getattr(resp, "text", None) or "").strip()
                raw_text_for_log = text
                if not text:
                    raise ValueError("Empty response")
                # Защита №2: thinking-модели иногда всё равно оборачивают JSON в
                # ```json ... ``` или добавляют преамбулу. Экстрактор — безопасный
                # no-op, когда ответ уже чистый.
                json_text = _extract_json_from_text(text)
                # Защита №3: финальная проверка схемы.
                parsed = AIBrainResponse.model_validate_json(json_text)
                logger.info(
                    "[AI] provider=gemini model=%s attempt=%d/%d status=SUCCESS latency_ms=%d intent=%s",
                    model_name,
                    idx,
                    len(model_names),
                    int((time.perf_counter() - t0) * 1000),
                    parsed.intent,
                )
                return parsed

            except asyncio.TimeoutError as exc:
                last_error = exc
                logger.warning(
                    "[AI] provider=gemini model=%s attempt=%d/%d status=TIMEOUT latency_ms=%d limit_sec=%.0f",
                    model_name,
                    idx,
                    len(model_names),
                    int((time.perf_counter() - t0) * 1000),
                    float(settings.ai_llm_timeout_sec),
                )
                break

            except ValidationError as exc:
                last_error = exc
                # Первые 240 символов достаточно, чтобы увидеть структуру ответа
                # (intent/reply_text/…), но не раздувают лог и не утекут PII-данных
                # клиента целиком. При необходимости можно поднять лимит точечно.
                raw_preview = raw_text_for_log[:240].replace("\n", " ")
                logger.warning(
                    "[AI] provider=gemini model=%s attempt=%d/%d status=VALIDATION_ERROR "
                    "latency_ms=%d err=%s fields=%s raw=%r",
                    model_name,
                    idx,
                    len(model_names),
                    int((time.perf_counter() - t0) * 1000),
                    type(exc).__name__,
                    _format_validation_error(exc),
                    raw_preview,
                )
                if idx < len(model_names):
                    logger.warning(
                        "[FAILOVER] Model %s failed (Reason: JSON_ERROR), trying %s",
                        model_name,
                        model_names[idx],
                    )
                continue

            except Exception as exc:
                last_error = exc
                is_provider_level = _is_provider_level_error(exc)
                logger.warning(
                    "[AI] provider=gemini model=%s attempt=%d/%d status=%s latency_ms=%d err=%s",
                    model_name,
                    idx,
                    len(model_names),
                    "QUOTA_EXHAUSTED" if is_provider_level else "ERROR",
                    int((time.perf_counter() - t0) * 1000),
                    type(exc).__name__,
                    # Полный traceback — только для неожиданных ошибок; для квоты/биллинга
                    # он бесполезен (причина и так ясна из текста) и лишь засоряет логи.
                    exc_info=not is_provider_level,
                )
                if is_provider_level:
                    # Прерываем каскад: другая модель того же провайдера упадёт так же.
                    break
                if idx < len(model_names):
                    logger.warning(
                        "[FAILOVER] Model %s failed (Reason: API_ERROR), trying %s",
                        model_name,
                        model_names[idx],
                    )
                continue

        if last_error is not None:
            # Treat as transient: caller may retry webhook job, but we still return fallback for UX safety.
            raise TransientAiError(str(last_error)) from last_error

        return AIBrainResponse(
            intent="escalate",
            reply_text="Прошу прощения, у меня возникли технические сложности. Переключаю на оператора.",
        )

    # --- Голос (Gemini multimodal STT) ---

    def supports_voice(self) -> bool:
        return bool((getattr(settings, "gemini_api_key", "") or "").strip())

    async def transcribe_voice(
        self,
        *,
        audio_bytes: bytes,
        audio_mime: str,
    ) -> str:
        """
        Распознать речь через Gemini multimodal (одним инлайн‑файлом).
        Возвращает дословный транскрипт на языке оригинала или "" при неудаче.
        """
        if not audio_bytes:
            return ""
        if not self._ensure_configured():
            logger.error("[AI] provider=gemini stt status=NO_KEY")
            return ""

        mime = _normalize_audio_mime_for_gemini(audio_mime)
        preset = AI_PRESETS["gemini"]

        last_error: Exception | None = None
        # Если словили provider-level ошибку (квота/биллинг/ключ) — обе петли прерываются:
        # ретраить бессмысленно, переключаться на другую модель того же провайдера — тоже.
        provider_level_failure = False
        # Каскад моделей тот же, что для чата: если первая упала по 4xx/недоступна — пробуем следующую.
        for idx, model_name in enumerate(preset.models, start=1):
            for attempt in range(1, _STT_MAX_RETRIES + 1):
                t0 = time.perf_counter()
                try:
                    genai = _lazy_import_genai()
                    model = genai.GenerativeModel(model_name)
                    resp = await model.generate_content_async(
                        [
                            _STT_PROMPT,
                            {"mime_type": mime, "data": audio_bytes},
                        ],
                        generation_config=genai.types.GenerationConfig(
                            temperature=0.0,
                        ),
                    )
                    text = (getattr(resp, "text", None) or "").strip()
                    logger.info(
                        "[AI] provider=gemini stt model=%s attempt=%d/%d status=%s latency_ms=%d len=%d",
                        model_name,
                        attempt,
                        _STT_MAX_RETRIES,
                        "SUCCESS" if text else "EMPTY",
                        int((time.perf_counter() - t0) * 1000),
                        len(text),
                    )
                    if text:
                        return text
                    # Пустой ответ — нет смысла ретраить, выходим из попыток в этой модели
                    break
                except Exception as exc:
                    last_error = exc
                    is_provider_level = _is_provider_level_error(exc)
                    logger.warning(
                        "[AI] provider=gemini stt model=%s attempt=%d/%d status=%s err=%s",
                        model_name,
                        attempt,
                        _STT_MAX_RETRIES,
                        "QUOTA_EXHAUSTED" if is_provider_level else "ERROR",
                        type(exc).__name__,
                    )
                    if is_provider_level:
                        provider_level_failure = True
                        break
            if provider_level_failure:
                break
            if idx < len(preset.models):
                logger.warning(
                    "[FAILOVER] STT model %s failed, trying %s",
                    model_name,
                    preset.models[idx],
                )

        if last_error is not None:
            final_status = "QUOTA_EXHAUSTED" if provider_level_failure else "FAILED"
            logger.error(
                "[AI] provider=gemini stt status=%s err=%s",
                final_status,
                type(last_error).__name__,
            )
        return ""

