"""Валидатор смешанной оплаты (§E10)."""

from __future__ import annotations

import pytest

from app.schemas.ai_schemas import AIBrainResponse, PaymentSplit
from app.services.order_logic import validate_mixed_payment_total


@pytest.mark.regression
def test_mixed_payment_within_tolerance_ok() -> None:
    ai = AIBrainResponse(
        intent="order",
        reply_text="ok",
        items=[],
        payment_mode="mixed",
        payment_split=PaymentSplit(cash=3000, card=3000, remote=4000),
        order_type="delivery",
        delivery_address="x",
    )
    assert validate_mixed_payment_total(ai, 10000.0, tol=1.0) is None


@pytest.mark.regression
def test_mixed_payment_outside_tolerance_rejected() -> None:
    ai = AIBrainResponse(
        intent="order",
        reply_text="ok",
        items=[],
        payment_mode="mixed",
        payment_split=PaymentSplit(cash=1000, card=1000, remote=1000),
        order_type="delivery",
        delivery_address="x",
    )
    err = validate_mixed_payment_total(ai, 10000.0, tol=1.0)
    assert err is not None
    assert "не совпадает" in err
