"""E4.1: идемпотентность дельт по action_id (дедупликация)."""

from app.schemas.ai_schemas import OrderAction
from app.services.order_logic import (
    filter_order_actions_idempotent,
    merge_cart_actions,
)


def test_filter_skips_same_explicit_action_id() -> None:
    a1 = OrderAction(action_id="idem-1", item_id="uuid-a", action="add", quantity=2)
    a2 = OrderAction(action_id="idem-1", item_id="uuid-a", action="add", quantity=9)
    actions, new_ids = filter_order_actions_idempotent(
        [a1, a2],
        applied=set(),
        inbound_message_id="wa-1",
    )
    assert len(actions) == 1
    assert actions[0].quantity == 2
    assert new_ids == ["idem-1"]


def test_filter_skips_already_applied_action_id() -> None:
    act = OrderAction(action_id="done-1", item_id="uuid-x", action="add", quantity=1)
    actions, new_ids = filter_order_actions_idempotent(
        [act],
        applied={"done-1"},
        inbound_message_id="wa-2",
    )
    assert actions == []
    assert new_ids == []


def test_merge_same_action_id_repeat_add_does_not_double_quantity() -> None:
    """
    Тот же action_id в двух add к одной строке — после filter остаётся одна дельта;
    merge_cart_actions не должен удваивать порцию при одном проходе.
    """
    actions, _ = filter_order_actions_idempotent(
        [
            OrderAction(action_id="dup-a", item_id="uuid-plov", action="add", quantity=1),
            OrderAction(action_id="dup-a", item_id="uuid-plov", action="add", quantity=9),
        ],
        applied=set(),
        inbound_message_id="",
    )
    merged = merge_cart_actions([], actions)
    assert len(merged) == 1
    assert int(merged[0].get("quantity", 0)) == 1


def test_when_action_id_already_applied_merge_is_skipped() -> None:
    actions, _ = filter_order_actions_idempotent(
        [OrderAction(action_id="done-cap", item_id="uuid-cappuccino", action="add", quantity=5)],
        applied={"done-cap"},
        inbound_message_id="m1",
    )
    assert actions == []
