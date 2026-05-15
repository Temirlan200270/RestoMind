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


def rule_session_rejection_cap(order_meta: dict) -> StrategyDecision | None:
    """
    Если в текущей сессии клиент проигнорировал/отклонил 2+ предложенных позиции
    (есть в trace, но нет в корзине) — перестать делать upsell, помочь закрыть заказ.
    """
    from app.services.sales_strategy import StrategyDecision

    meta = order_meta if isinstance(order_meta, dict) else {}
    trace = meta.get("recommendation_trace")
    if not isinstance(trace, list) or not trace:
        return None

    offered_ids = {
        str(t.get("offered_iiko_id") or "").strip().lower()
        for t in trace
        if isinstance(t, dict) and (t.get("offered_iiko_id") or "")
    }
    offered_ids.discard("")
    if not offered_ids:
        return None

    cart_ids = {
        str(i.get("iiko_id") or i.get("iiko_item_id") or "").strip().lower()
        for i in (meta.get("items") or [])
        if isinstance(i, dict)
    }
    cart_ids.discard("")

    rejected_count = len(offered_ids - cart_ids)
    if rejected_count >= 2:
        return StrategyDecision(
            goal="close_order",
            restriction=(
                "Клиент уже проигнорировал несколько предложений в этом заказе. "
                "НЕ предлагай новые блюда — помоги оформить заказ (тип получения, оплата)."
            ),
        )
    return None


def apply_engine_rules_first(order_meta: dict) -> StrategyDecision | None:
    """Первая фаза: жёсткие правила с полным перекрытием старого пайплайна."""
    for rule_fn in (rule_session_rejection_cap, rule_close_after_recommendation_trace_cap):
        hit = rule_fn(order_meta)
        if hit is not None:
            return hit
    return None
