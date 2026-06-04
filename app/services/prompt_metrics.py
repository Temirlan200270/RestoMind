"""Замер и опциональная обрезка LLM-промпта."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PromptSize:
    total_chars: int
    estimated_tokens: int
    parts: dict[str, int]


def estimate_tokens(text: str) -> int:
    """Грубая оценка: ~3.5 символа на токен для смешанного ru/en."""
    if not text:
        return 0
    return max(1, int(len(text) / 3.5))


def measure_prompt(
    *,
    menu_context: str,
    kb_context: str,
    draft_ctx: str,
    strategy_ctx: str,
    customer_ctx: str,
    current_time_ctx: str,
    history: list[dict[str, str]],
    user_text: str,
) -> PromptSize:
    history_text = "\n".join(str(h.get("content", "")) for h in history)
    parts = {
        "menu": estimate_tokens(menu_context),
        "kb": estimate_tokens(kb_context),
        "draft": estimate_tokens(draft_ctx),
        "strategy": estimate_tokens(strategy_ctx),
        "customer": estimate_tokens(customer_ctx),
        "time": estimate_tokens(current_time_ctx),
        "history": estimate_tokens(history_text),
        "user": estimate_tokens(user_text),
    }
    total_chars = sum(
        len(s or "")
        for s in (
            menu_context,
            kb_context,
            draft_ctx,
            strategy_ctx,
            customer_ctx,
            current_time_ctx,
            history_text,
            user_text,
        )
    )
    return PromptSize(
        total_chars=total_chars,
        estimated_tokens=sum(parts.values()),
        parts=parts,
    )


def trim_history_to_budget(
    history: list[dict[str, str]],
    *,
    budget_tokens: int,
    min_keep: int = 4,
) -> list[dict[str, str]]:
    """Оставляет последние реплики, укладываясь в budget_tokens."""
    if not history:
        return history
    if len(history) <= min_keep:
        return history
    out: list[dict[str, str]] = []
    used = 0
    for item in reversed(history):
        cost = estimate_tokens(str(item.get("content", "")))
        if used + cost > budget_tokens and len(out) >= min_keep:
            break
        out.append(item)
        used += cost
    return list(reversed(out))


def trim_menu_context_to_budget(menu_context: str, *, budget_tokens: int) -> str:
    """Обрезает меню по строкам (или по символам для одной длинной строки)."""
    text = (menu_context or "").strip()
    if not text or budget_tokens <= 0:
        return text
    if estimate_tokens(text) <= budget_tokens:
        return text

    max_chars = max(200, int(budget_tokens * 3.5))
    if len(text) <= max_chars and "\n" not in text:
        return text

    lines = text.split("\n")
    if len(lines) == 1 and len(text) > max_chars:
        return (
            text[:max_chars]
            + "\n... [меню сокращено; уточните блюдо или категорию — подскажу точнее]"
        )

    out: list[str] = []
    used = 0
    for line in lines:
        cost = estimate_tokens(line + "\n")
        if used + cost > budget_tokens and out:
            out.append(
                f"... [меню сокращено: {len(out)} из {len(lines)} строк; "
                "уточните блюдо или категорию — подскажу точнее]",
            )
            break
        out.append(line)
        used += cost
    return "\n".join(out)


def apply_prompt_size_controls(
    history: list[dict[str, str]],
    *,
    menu_context: str,
    kb_context: str,
    draft_ctx: str,
    strategy_ctx: str,
    customer_ctx: str,
    current_time_ctx: str,
    user_text: str,
    soft_limit: int,
    hard_limit: int,
    min_keep: int,
) -> tuple[list[dict[str, str]], str, PromptSize, PromptSize | None, bool]:
    """
    Замер промпта, обрезка history и при необходимости menu_context.

    Returns:
        (history, menu_context, size_before, size_after_or_none, trimmed)
    """
    menu_out = menu_context
    size_before = measure_prompt(
        menu_context=menu_out,
        kb_context=kb_context,
        draft_ctx=draft_ctx,
        strategy_ctx=strategy_ctx,
        customer_ctx=customer_ctx,
        current_time_ctx=current_time_ctx,
        history=history,
        user_text=user_text,
    )
    if soft_limit <= 0:
        return history, menu_out, size_before, None, False

    if size_before.estimated_tokens <= soft_limit:
        return history, menu_out, size_before, None, False

    history_budget = max(
        500,
        soft_limit - (size_before.estimated_tokens - size_before.parts["history"]),
    )
    trimmed_history = trim_history_to_budget(
        history,
        budget_tokens=history_budget,
        min_keep=min_keep,
    )
    size_after = measure_prompt(
        menu_context=menu_out,
        kb_context=kb_context,
        draft_ctx=draft_ctx,
        strategy_ctx=strategy_ctx,
        customer_ctx=customer_ctx,
        current_time_ctx=current_time_ctx,
        history=trimmed_history,
        user_text=user_text,
    )
    trimmed = True

    if size_after.estimated_tokens > soft_limit:
        non_menu = size_after.estimated_tokens - size_after.parts["menu"]
        menu_budget = max(800, soft_limit - non_menu)
        menu_out = trim_menu_context_to_budget(menu_out, budget_tokens=menu_budget)
        size_after = measure_prompt(
            menu_context=menu_out,
            kb_context=kb_context,
            draft_ctx=draft_ctx,
            strategy_ctx=strategy_ctx,
            customer_ctx=customer_ctx,
            current_time_ctx=current_time_ctx,
            history=trimmed_history,
            user_text=user_text,
        )

    if size_after.estimated_tokens > hard_limit:
        # TODO: warning в Telegram админу org при регулярном превышении hard_limit
        pass
    return trimmed_history, menu_out, size_before, size_after, trimmed
