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
from app.services.ai_engine.base import BaseAIProvider, ModelTier
from app.services.ai_engine.errors import TransientAiError
from app.services.ai_engine.gemini_p import GeminiProvider
from app.services.ai_engine.openai_p import OpenAIProvider
from app.services.ai_engine.prompting import format_untrusted_user_text_for_model

logger = logging.getLogger(__name__)

_openai_client: AsyncOpenAI | None = None
_cached_openai_key: str = ""
_cached_openai_base: str = ""

_openai_provider: OpenAIProvider | None = None
_gemini_provider: GeminiProvider | None = None

# Последнее залогированное решение о выборе провайдера.
# Кешируем, чтобы писать INFO/WARNING только при смене входных данных, а не на каждом вызове.
_provider_choice_log_state: tuple[str, bool, bool] | None = None


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

_STRONG_INTENTS = frozenset({
    "order",
    "book",
    "booking",
    "confirm",
    "confirm_order",
    "confirm_booking",
    "escalate",
    "human",
    "payment",
    "cancel",
})

_STRONG_KEYWORDS = (
    "заказ",
    "заказать",
    "оформ",
    "достав",
    "самовывоз",
    "брон",
    "стол",
    "столик",
    "оплат",
    "счёт",
    "счет",
    "корзин",
    "order",
    "book",
    "delivery",
    "pickup",
    "pay",
)

_FALLBACK_RESPONSE = AIBrainResponse(
    intent="escalate",
    reply_text="Прошу прощения, у меня возникли технические сложности. Переключаю на оператора.",
)


def is_openai_fallback_escalation_reply(reply_text: str) -> bool:
    """True, если ответ совпадает с запасным при сбое OpenAI (квота, сеть и т.д.)."""
    text = (reply_text or "").strip()
    fallback = _FALLBACK_RESPONSE.reply_text.strip()
    if text == fallback:
        return True
    lowered = text.lower()
    return "технические сложности" in lowered and "переключаю на оператора" in lowered


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


def _has_openai_key() -> bool:
    return bool((settings.openai_api_key or "").strip())


def _has_gemini_key() -> bool:
    return bool((getattr(settings, "gemini_api_key", "") or "").strip())


def _resolve_provider_name() -> str:
    """
    Определить, какого провайдера фактически отдать в рантайме.

    Логика (по убыванию приоритета):
      1. Явный `AI_PROVIDER` с валидным ключом — используется он.
      2. Явный `AI_PROVIDER` без ключа, но у альтернативы ключ есть —
         переключаемся на альтернативу с WARNING (graceful degradation).
      3. `AI_PROVIDER` пуст/неизвестен — авто-выбор по наличию ключей;
         если оба ключа или ни одного — дефолт `openai` (обратная совместимость).
      4. Если ключей нет вовсе — возвращаем запрошенного, чтобы downstream
         честно залогировал `NO_KEY` (не маскируем ошибку конфигурации).

    Логируем решение только при смене входных данных — иначе лог захлебнётся.
    """
    global _provider_choice_log_state

    requested_raw = (getattr(settings, "ai_provider", "") or "").strip().lower()
    requested = requested_raw if requested_raw in ("openai", "gemini") else ""
    has_openai = _has_openai_key()
    has_gemini = _has_gemini_key()

    if requested == "openai":
        if has_openai:
            chosen = "openai"
        elif has_gemini:
            chosen = "gemini"
        else:
            chosen = "openai"
    elif requested == "gemini":
        if has_gemini:
            chosen = "gemini"
        elif has_openai:
            chosen = "openai"
        else:
            chosen = "gemini"
    else:
        if has_gemini and not has_openai:
            chosen = "gemini"
        elif has_openai and not has_gemini:
            chosen = "openai"
        else:
            chosen = "openai"

    state = (requested_raw, has_openai, has_gemini)
    if _provider_choice_log_state != state:
        _provider_choice_log_state = state
        if requested and requested != chosen:
            logger.warning(
                "[AI] AI_PROVIDER=%s задан, но ключ отсутствует; fallback → %s "
                "(has_openai=%s, has_gemini=%s)",
                requested, chosen, has_openai, has_gemini,
            )
        elif not requested:
            logger.info(
                "[AI] AI_PROVIDER не задан; авто-выбор → %s "
                "(has_openai=%s, has_gemini=%s)",
                chosen, has_openai, has_gemini,
            )
        else:
            logger.info(
                "[AI] AI_PROVIDER=%s (has_openai=%s, has_gemini=%s)",
                chosen, has_openai, has_gemini,
            )

    return chosen


def get_ai_client() -> BaseAIProvider:
    """
    Фабрика провайдеров AI-Engine v2.0.

    Источник истины — `settings.AI_PROVIDER`, но с graceful fallback:
    если указанный провайдер не имеет ключа, а у альтернативы ключ есть —
    автоматически переключается на альтернативу (см. `_resolve_provider_name`).
    """
    global _openai_provider, _gemini_provider
    chosen = _resolve_provider_name()
    if chosen == "gemini":
        if _gemini_provider is None:
            _gemini_provider = GeminiProvider()
        return _gemini_provider
    if _openai_provider is None:
        _openai_provider = OpenAIProvider()
    return _openai_provider


def _reset_provider_choice_log() -> None:
    """Сбросить кеш логирования (нужно в тестах, когда меняются env-переменные)."""
    global _provider_choice_log_state
    _provider_choice_log_state = None


def resolve_model_tier(
    user_text: str,
    *,
    has_draft: bool = False,
    draft_order_context: str = "",
    sales_strategy_context: str = "",
) -> ModelTier:
    """Heuristic pre-routing до первого LLM-вызова (intent ещё неизвестен)."""
    if not settings.ai_model_routing_enabled:
        return "strong"
    if has_draft or (draft_order_context or "").strip():
        return "strong"
    if (sales_strategy_context or "").strip():
        return "strong"
    text = (user_text or "").strip()
    if not text:
        return "strong"
    if len(text) > 120:
        return "strong"
    lower = text.lower()
    if any(kw in lower for kw in _STRONG_KEYWORDS):
        return "strong"
    return "fast"


_STRONG_RERUN_ALWAYS = frozenset({
    "payment",
    "confirm",
    "confirm_order",
    "confirm_booking",
    "cancel",
})


def _needs_strong_model_rerun(response: AIBrainResponse) -> bool:
    """Fast model → strong только когда нужна структура или ответ пустой / high-stakes."""
    if response.items:
        return True
    if response.booking_details is not None:
        return True
    if response.order_actions:
        return True
    intent = (response.intent or "").strip().lower()
    if intent not in _STRONG_INTENTS:
        return False
    if intent in _STRONG_RERUN_ALWAYS:
        return True
    reply = (response.reply_text or "").strip()
    # intent=order/book с нормальным текстом — fast достаточно (экономим 2-й вызов).
    return not reply


def _estimate_prompt_tokens(
    *,
    menu_context: str,
    kb_context: str,
    draft_order_context: str,
    sales_strategy_context: str,
    customer_context: str,
    current_time_context: str,
    history: list[dict[str, str]],
    user_text: str,
) -> int:
    from app.services.prompt_metrics import measure_prompt

    return measure_prompt(
        menu_context=menu_context,
        kb_context=kb_context,
        draft_ctx=draft_order_context,
        strategy_ctx=sales_strategy_context,
        customer_ctx=customer_context,
        current_time_ctx=current_time_context,
        history=history,
        user_text=user_text,
    ).estimated_tokens


async def _maybe_strong_rerun(
    provider: BaseAIProvider,
    fast_result: AIBrainResponse,
    *,
    history: list[dict[str, str]],
    user_text: str,
    menu_context: str,
    kb_context: str,
    draft_order_context: str,
    sales_strategy_context: str,
    customer_context: str,
    current_time_context: str,
    trace_prefix: str,
) -> AIBrainResponse:
    if not settings.ai_model_routing_enabled or not _needs_strong_model_rerun(fast_result):
        return fast_result

    est_tokens = _estimate_prompt_tokens(
        menu_context=menu_context,
        kb_context=kb_context,
        draft_order_context=draft_order_context,
        sales_strategy_context=sales_strategy_context,
        customer_context=customer_context,
        current_time_context=current_time_context,
        history=history,
        user_text=user_text,
    )
    if settings.prompt_max_tokens_soft > 0 and est_tokens > settings.prompt_max_tokens_soft:
        logger.info(
            "%s[AI] skip fast→strong: prompt oversize tokens=%d soft=%d intent=%s",
            trace_prefix,
            est_tokens,
            settings.prompt_max_tokens_soft,
            fast_result.intent,
        )
        return fast_result

    logger.info(
        "%s[AI] fast→strong rerun intent=%s items=%d",
        trace_prefix,
        fast_result.intent,
        len(fast_result.items or []),
    )
    try:
        return await provider.generate_response(
            history=history,
            user_text=user_text,
            menu_context=menu_context,
            kb_context=kb_context,
            draft_order_context=draft_order_context,
            sales_strategy_context=sales_strategy_context,
            customer_context=customer_context,
            current_time_context=current_time_context,
            model_tier="strong",
        )
    except TransientAiError as exc:
        logger.warning(
            "%s[AI] fast→strong failed (%s), keeping fast result intent=%s",
            trace_prefix,
            exc,
            fast_result.intent,
        )
        return fast_result


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
    trace_id: str | None = None,
    has_draft: bool = False,
    model_tier: ModelTier | None = None,
) -> AIBrainResponse:
    """AI-агностичный вызов: провайдер выбирается по AI_PROVIDER."""
    from app.services.trace_context import get_trace_id, trace_log_prefix

    effective_trace = trace_id or get_trace_id()
    prefix = f"[trace_id={effective_trace}] " if effective_trace else trace_log_prefix()
    provider = get_ai_client()
    tier: ModelTier = model_tier or resolve_model_tier(
        user_text,
        has_draft=has_draft,
        draft_order_context=draft_order_context,
        sales_strategy_context=sales_strategy_context,
    )
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
            model_tier=tier,
        )
        if tier == "fast":
            result = await _maybe_strong_rerun(
                provider,
                result,
                history=history,
                user_text=user_text,
                menu_context=menu_context,
                kb_context=kb_context,
                draft_order_context=draft_order_context,
                sales_strategy_context=sales_strategy_context,
                customer_context=customer_context,
                current_time_context=current_time_context,
                trace_prefix=prefix,
            )
        logger.info(
            "%s[AI] dispatch provider=%s status=SUCCESS latency_ms=%d intent=%s tier=%s",
            prefix,
            type(provider).__name__,
            int((time.perf_counter() - t0) * 1000),
            result.intent,
            tier,
        )
        return result
    except TransientAiError:
        logger.warning(
            "%s[AI] dispatch provider=%s status=TRANSIENT latency_ms=%d",
            prefix,
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
    trace_id: str | None = None,
    has_draft: bool = False,
    model_tier: ModelTier | None = None,
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
        trace_id=trace_id,
        has_draft=has_draft,
        model_tier=model_tier,
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
        f"{VOICE_FROM_STT_INSTRUCTION}\n\n"
        f"{format_untrusted_user_text_for_model(transcript)}\n"
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
