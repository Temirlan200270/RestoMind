from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Literal

from app.schemas.ai_schemas import AIBrainResponse

ModelTier = Literal["fast", "strong"]


class BaseAIProvider(ABC):
    """
    AI provider interface (AI-Engine v2.0).

    Контракт: входы — доменные (history/user_text + контексты), выход — строго
    валидированный `AIBrainResponse` (Pydantic).
    """

    @abstractmethod
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
        raise NotImplementedError

    # --- Голос (опционально; если провайдер не поддерживает — оставит дефолты) ---

    def supports_voice(self) -> bool:
        """
        True, если провайдер настроен и умеет распознавать аудио.
        По умолчанию False — конкретные провайдеры переопределяют.
        """
        return False

    async def transcribe_voice(
        self,
        *,
        audio_bytes: bytes,
        audio_mime: str,
    ) -> str:
        """
        Распознать речь в текст. Возвращает пустую строку, если распознать не удалось
        (или провайдер не поддерживает STT). Никогда не выбрасывает исключения наружу
        (для устойчивости пайплайна — звонок/вебхук обрабатывает пустой результат отдельно).
        """
        return ""

