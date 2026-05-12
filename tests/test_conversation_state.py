from app.services.conversation_state import check_conversation_transition


def test_human_mode_can_only_return_to_chatting() -> None:
    blocked = check_conversation_transition("human_mode", "confirming_order")
    assert blocked.allowed is False
    assert "not allowed" in blocked.reason

    allowed = check_conversation_transition("human_mode", "chatting")
    assert allowed.allowed is True
    assert allowed.from_state.value == "human_mode"
    assert allowed.to_state.value == "chatting"


def test_confirming_order_can_go_back_to_payment() -> None:
    result = check_conversation_transition("confirming_order", "awaiting_order_payment")
    assert result.allowed is True
