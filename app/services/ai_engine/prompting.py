from __future__ import annotations

from app.services.prompts import RESTAURANT_SYSTEM_PROMPT


def build_system_prompt(
    *,
    menu_context: str = "",
    kb_context: str = "",
    draft_order_context: str = "",
    sales_strategy_context: str = "",
    customer_context: str = "",
    current_time_context: str = "",
) -> str:
    system_prompt = RESTAURANT_SYSTEM_PROMPT

    if (customer_context or "").strip():
        system_prompt += (
            "\n\n# Досье гостя (только факты с сервера; для тона и узнавания)\n"
            f"{customer_context.strip()}"
        )
    if (current_time_context or "").strip():
        system_prompt += (
            "\n\n# Текущее время заведения\n"
            f"{current_time_context.strip()}"
        )
    if (kb_context or "").strip():
        system_prompt += f"\n\n# Справочник заведения (база знаний)\n{kb_context.strip()}"
    if (menu_context or "").strip():
        system_prompt += f"\n\n# Актуальное меню ресторана\n{menu_context.strip()}"
    if (draft_order_context or "").strip():
        system_prompt += f"\n\n{draft_order_context.strip()}"
    if (sales_strategy_context or "").strip():
        system_prompt += f"\n\n{sales_strategy_context.strip()}"
    return system_prompt

