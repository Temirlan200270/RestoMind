"""
E11 Strategy Engine — явные правила до LLM.

Слой вызывается из ``build_sales_strategy`` (и зеркально доступен тестам / будущим хукам в ``intent_router``).
Контракт ответа — тот же ``StrategyDecision``, что и в ``sales_strategy.py``.

Импорты из ``sales_strategy`` только внутри функций — избегаем циклического импорта модулей.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.sales_strategy import StrategyDecision


def rule_close_after_recommendation_trace_cap(order_meta: dict) -> StrategyDecision | None:
    """
    После двух рекомендаций в черновике — не предлагать новые допродажи (логика перенесена из монолита правил).
    """
    from app.services.sales_strategy import StrategyDecision, _trace_len

    meta = order_meta if isinstance(order_meta, dict) else {}
    if _trace_len(meta) >= 2:
        return StrategyDecision(
            goal="close_order",
            restriction=(
                "Уже было несколько рекомендаций в этом заказе. НЕ предлагай новые блюда — "
                "помоги завершить оформление (тип получения, оплата, уточнения)."
            ),
        )
    return None


def apply_engine_rules_first(order_meta: dict) -> StrategyDecision | None:
    """Первая фаза: жёсткие правила с полным перекрытием старого пайплайна."""
    hit = rule_close_after_recommendation_trace_cap(order_meta)
    if hit is not None:
        return hit
    return None
