"""
AI presets and strategies (AI-Engine v2.0).

Цель: централизовать выбор моделей и стратегию failover, чтобы в .env оставался
только переключатель AI_PROVIDER (gemini/openai) и ключи провайдеров.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


ProviderKey = Literal["gemini", "openai"]
StrategyKey = Literal["cascade", "single"]


@dataclass(frozen=True, slots=True)
class ProviderPreset:
    models: tuple[str, ...]
    strategy: StrategyKey


AI_PRESETS: dict[ProviderKey, ProviderPreset] = {
    "gemini": ProviderPreset(
        models=("gemini-3.1-flash-lite", "gemini-3-flash"),
        strategy="cascade",
    ),
    "openai": ProviderPreset(
        models=("gpt-5.4-mini",),
        strategy="single",
    ),
}

