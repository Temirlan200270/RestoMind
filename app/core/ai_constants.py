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
        # Primary: Gemini 3 Flash (preview) — флагманский Flash с reasoning уровня Pro,
        # поддерживает большое окно контекста (1M) и длинный output (≈66K токенов).
        # Fallback: Gemini 2.5 Flash (GA, стабильная) — страхует, если preview временно
        # недоступен / разлогинен / Google снёс preview-алиас. GA-модель гарантированно
        # жива до ≥2026-10-16, поэтому это безопасная страховка.
        models=("gemini-3-flash-preview", "gemini-2.5-flash"),
        strategy="cascade",
    ),
    "openai": ProviderPreset(
        models=("gpt-5.4-mini",),
        strategy="single",
    ),
}

