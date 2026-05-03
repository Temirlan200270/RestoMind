"""Цепочки merge корзины (§E10)."""

from __future__ import annotations

import pytest

from app.schemas.ai_schemas import OrderAction
from app.services.order_logic import classify_packaging_kind, compute_fee_lines, merge_cart_actions


@pytest.mark.regression
def test_merge_chain_add_remove_set_quantity() -> None:
    cur: list[dict] = []
    a1 = [
        OrderAction(item_id="uuid-a", action="add", quantity=2),
        OrderAction(item_id="uuid-b", action="add", quantity=1),
    ]
    cur = merge_cart_actions(cur, a1)
    cur = merge_cart_actions(
        cur,
        [OrderAction(item_id="uuid-a", action="remove", quantity=1)],
    )
    cur = merge_cart_actions(
        cur,
        [OrderAction(item_id="uuid-b", action="set_quantity", quantity=3)],
    )
    by_key = {_line_key(x): int(x.get("quantity", 0)) for x in cur}
    assert by_key.get("uuid-a", 0) == 1
    assert by_key.get("uuid-b", 0) == 3


def _line_key(line: dict) -> str:
    return str(line.get("iiko_id") or line.get("name") or "").strip()


@pytest.mark.regression
def test_remove_second_distinct_line_by_iiko_id() -> None:
    """«Убери вторую позицию» моделируется как remove по идентификатору второй строки."""
    cur = merge_cart_actions(
        [],
        [
            OrderAction(item_id="id1", action="add", quantity=1),
            OrderAction(item_id="id2", action="add", quantity=1),
            OrderAction(item_id="id3", action="add", quantity=1),
            OrderAction(item_id="id4", action="add", quantity=1),
            OrderAction(item_id="id5", action="add", quantity=1),
        ],
    )
    assert str(cur[1].get("name") or "") == "id2"
    cur = merge_cart_actions(
        cur,
        [OrderAction(item_id="id2", action="remove", quantity=1)],
    )
    names = [str(x.get("name") or "") for x in cur]
    assert "id2" not in names
    assert len(cur) == 4


@pytest.mark.regression
def test_remove_plov_1kg_drops_tabak_packaging_fees() -> None:
    """После удаления строки плова 1 кг не должно остаться packaging_plov_1kg_* в fee_lines."""
    line = {
        "name": "Плов 1 кг",
        "category": "Горячее",
        "quantity": 1,
        "iiko_id": "uuid-plov-1kg",
        "packaging_plov_1kg": "tabak",
    }
    merged = merge_cart_actions([line], [OrderAction(item_id="uuid-plov-1kg", action="remove", quantity=1)])
    assert merged == []
    fee_lines, extras = compute_fee_lines([], 0.0, "delivery")
    kinds = [str(x.get("kind", "")) for x in fee_lines if isinstance(x, dict)]
    assert not any("plov_1kg" in k for k in kinds)
    assert extras >= 0


@pytest.mark.regression
def test_classify_packaging_plov_1kg_for_fee_rules() -> None:
    assert classify_packaging_kind("Плов 1 кг", "Горячее") == "plov_1kg"
