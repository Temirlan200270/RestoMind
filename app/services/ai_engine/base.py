from __future__ import annotations

from abc import ABC, abstractmethod

from app.schemas.ai_schemas import AIBrainResponse


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
    ) -> AIBrainResponse:
        raise NotImplementedError

