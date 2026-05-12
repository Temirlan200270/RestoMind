"""Formal conversation state model for dialog/session transitions.

This module keeps the current persisted state vocabulary backward-compatible
while introducing a single place for transition rules and future expansion.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ConversationState(StrEnum):
    """Canonical persisted conversation states."""

    CHATTING = "chatting"
    AWAITING_ORDER_PAYMENT = "awaiting_order_payment"
    CONFIRMING_ORDER = "confirming_order"
    CONFIRMING_BOOKING = "confirming_booking"
    HUMAN_MODE = "human_mode"


@dataclass(frozen=True)
class TransitionCheck:
    allowed: bool
    from_state: ConversationState
    to_state: ConversationState
    reason: str


_ALLOWED_TRANSITIONS: dict[ConversationState, frozenset[ConversationState]] = {
    ConversationState.CHATTING: frozenset({
        ConversationState.CHATTING,
        ConversationState.AWAITING_ORDER_PAYMENT,
        ConversationState.CONFIRMING_ORDER,
        ConversationState.CONFIRMING_BOOKING,
        ConversationState.HUMAN_MODE,
    }),
    ConversationState.AWAITING_ORDER_PAYMENT: frozenset({
        ConversationState.AWAITING_ORDER_PAYMENT,
        ConversationState.CHATTING,
        ConversationState.CONFIRMING_ORDER,
        ConversationState.HUMAN_MODE,
    }),
    ConversationState.CONFIRMING_ORDER: frozenset({
        ConversationState.CONFIRMING_ORDER,
        ConversationState.CHATTING,
        ConversationState.AWAITING_ORDER_PAYMENT,
        ConversationState.HUMAN_MODE,
    }),
    ConversationState.CONFIRMING_BOOKING: frozenset({
        ConversationState.CONFIRMING_BOOKING,
        ConversationState.CHATTING,
        ConversationState.HUMAN_MODE,
    }),
    ConversationState.HUMAN_MODE: frozenset({
        ConversationState.HUMAN_MODE,
        ConversationState.CHATTING,
    }),
}


def normalize_conversation_state(value: object) -> ConversationState:
    """Parse persisted state defensively."""
    if isinstance(value, ConversationState):
        return value
    raw = str(value or "").strip().lower()
    if not raw:
        return ConversationState.CHATTING
    try:
        return ConversationState(raw)
    except ValueError:
        return ConversationState.CHATTING


def check_conversation_transition(
    from_state: object,
    to_state: object,
) -> TransitionCheck:
    """Validate an FSM transition using the current explicit rules."""
    prev = normalize_conversation_state(from_state)
    nxt = normalize_conversation_state(to_state)
    if nxt in _ALLOWED_TRANSITIONS.get(prev, frozenset()):
        return TransitionCheck(
            allowed=True,
            from_state=prev,
            to_state=nxt,
            reason="allowed",
        )
    return TransitionCheck(
        allowed=False,
        from_state=prev,
        to_state=nxt,
        reason=f"transition {prev.value} -> {nxt.value} is not allowed",
    )

