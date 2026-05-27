"""Тесты prompt_metrics: замер и обрезка истории."""

from app.services.prompt_metrics import (
    apply_prompt_size_controls,
    estimate_tokens,
    measure_prompt,
    trim_history_to_budget,
)


def test_estimate_tokens_empty() -> None:
    assert estimate_tokens("") == 0


def test_estimate_tokens_russian() -> None:
    assert estimate_tokens("привет") >= 1


def test_trim_history_keeps_min_keep() -> None:
    history = [{"role": "user", "content": f"msg {i} " * 20} for i in range(10)]
    out = trim_history_to_budget(history, budget_tokens=50, min_keep=4)
    assert len(out) >= 4
    assert out[-1]["content"].startswith("msg 9")


def test_measure_prompt_sums_parts() -> None:
    history = [{"role": "user", "content": "hello"}]
    size = measure_prompt(
        menu_context="menu",
        kb_context="kb",
        draft_ctx="",
        strategy_ctx="",
        customer_ctx="",
        current_time_ctx="time",
        history=history,
        user_text="user",
    )
    assert size.total_chars > 0
    assert size.estimated_tokens == sum(size.parts.values())


def test_apply_prompt_size_controls_trims_when_over_soft() -> None:
    long_hist = [{"role": "user", "content": "x" * 4000} for _ in range(8)]
    history, menu_out, before, after, trimmed = apply_prompt_size_controls(
        long_hist,
        menu_context="m" * 5000,
        kb_context="k" * 5000,
        draft_ctx="",
        strategy_ctx="",
        customer_ctx="",
        current_time_ctx="t" * 500,
        user_text="u" * 200,
        soft_limit=8000,
        hard_limit=12000,
        min_keep=4,
    )
    assert trimmed is True
    assert after is not None
    assert len(history) <= len(long_hist)
    assert after.estimated_tokens <= before.estimated_tokens


def test_trim_menu_context_when_history_empty() -> None:
    huge_menu = "x" * 40_000
    history, menu_out, before, after, trimmed = apply_prompt_size_controls(
        [],
        menu_context=huge_menu,
        kb_context="kb",
        draft_ctx="",
        strategy_ctx="",
        customer_ctx="",
        current_time_ctx="time",
        user_text="плов",
        soft_limit=8000,
        hard_limit=12000,
        min_keep=4,
    )
    assert trimmed is True
    assert after is not None
    assert len(menu_out) < len(huge_menu)
    assert "меню сокращено" in menu_out
    assert after.estimated_tokens <= before.estimated_tokens
