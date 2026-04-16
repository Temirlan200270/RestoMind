from __future__ import annotations

import logging
import time

from pydantic import ValidationError

from app.core.ai_constants import AI_PRESETS
from app.core.config import settings
from app.schemas.ai_schemas import AIBrainResponse
from app.services.ai_engine.base import BaseAIProvider
from app.services.ai_engine.errors import TransientAiError
from app.services.ai_engine.prompting import build_system_prompt

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
    "Никакого текста вне JSON."
)


class GeminiProvider(BaseAIProvider):
    def __init__(self) -> None:
        self._configured_key: str | None = None

    def _ensure_configured(self) -> bool:
        key = (getattr(settings, "gemini_api_key", "") or "").strip()
        if not key:
            return False
        if self._configured_key != key:
            # Lazy import to keep local/test environments working when Gemini isn't used.
            import google.generativeai as genai  # type: ignore

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
    ) -> AIBrainResponse:
        if not self._ensure_configured():
            logger.error("[AI] provider=gemini status=NO_KEY")
            return AIBrainResponse(
                intent="escalate",
                reply_text="Прошу прощения, у меня возникли технические сложности. Переключаю на оператора.",
            )

        preset = AI_PRESETS["gemini"]
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
        prompt = (
            f"{system_prompt}\n\n"
            f"{_JSON_ONLY_INSTRUCTION}\n\n"
            f"{history_block}\n\n"
            f"USER: {user_text.strip()}\n"
        ).strip()

        last_error: Exception | None = None
        for idx, model_name in enumerate(preset.models, start=1):
            t0 = time.perf_counter()
            try:
                import google.generativeai as genai  # type: ignore

                model = genai.GenerativeModel(model_name)
                # Async API is available in google-generativeai; use it to avoid blocking.
                resp = await model.generate_content_async(
                    prompt,
                    generation_config=genai.types.GenerationConfig(
                        temperature=0.3,
                    ),
                )
                text = (getattr(resp, "text", None) or "").strip()
                if not text:
                    raise ValueError("Empty response")
                parsed = AIBrainResponse.model_validate_json(text)
                logger.info(
                    "[AI] provider=gemini model=%s attempt=%d/%d status=SUCCESS latency_ms=%d intent=%s",
                    model_name,
                    idx,
                    len(preset.models),
                    int((time.perf_counter() - t0) * 1000),
                    parsed.intent,
                )
                return parsed

            except ValidationError as exc:
                last_error = exc
                logger.warning(
                    "[AI] provider=gemini model=%s attempt=%d/%d status=VALIDATION_ERROR latency_ms=%d err=%s",
                    model_name,
                    idx,
                    len(preset.models),
                    int((time.perf_counter() - t0) * 1000),
                    type(exc).__name__,
                )
                if idx < len(preset.models):
                    logger.warning(
                        "[FAILOVER] Model %s failed (Reason: JSON_ERROR), trying %s",
                        model_name,
                        preset.models[idx],
                    )
                continue

            except Exception as exc:
                last_error = exc
                logger.warning(
                    "[AI] provider=gemini model=%s attempt=%d/%d status=ERROR latency_ms=%d err=%s",
                    model_name,
                    idx,
                    len(preset.models),
                    int((time.perf_counter() - t0) * 1000),
                    type(exc).__name__,
                    exc_info=True,
                )
                if idx < len(preset.models):
                    logger.warning(
                        "[FAILOVER] Model %s failed (Reason: API_ERROR), trying %s",
                        model_name,
                        preset.models[idx],
                    )
                continue

        if last_error is not None:
            # Treat as transient: caller may retry webhook job, but we still return fallback for UX safety.
            raise TransientAiError(str(last_error)) from last_error

        return AIBrainResponse(
            intent="escalate",
            reply_text="Прошу прощения, у меня возникли технические сложности. Переключаю на оператора.",
        )

