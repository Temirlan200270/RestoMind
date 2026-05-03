"""Фикстуры для регрессионных сценариев §E10 / plan §4.7."""

from __future__ import annotations

from typing import Any

import pytest

from app.schemas.ai_schemas import AIBrainResponse, OrderItem


@pytest.fixture
def fake_ai_response():
    """Детерминированная сборка ответа «как от LLM» для регресс-тестов."""

    def _factory(
        *,
        intent: str = "order",
        reply_text: str = "Принято.",
        items: list[OrderItem] | None = None,
        **extra: Any,
    ) -> AIBrainResponse:
        payload: dict[str, Any] = {
            "intent": intent,
            "reply_text": reply_text,
            "items": items or [],
            **extra,
        }
        return AIBrainResponse.model_validate(payload)

    return _factory
