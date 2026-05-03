"""Границы тарифов доставки (§E10)."""

from __future__ import annotations

import pytest

from app.services.order_logic import compute_fee_lines


@pytest.mark.regression
def test_delivery_fee_below_free_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.services.order_logic.settings.pricing_delivery_free_threshold", 10000.0)
    monkeypatch.setattr("app.services.order_logic.settings.pricing_delivery_fee", 700.0)
    monkeypatch.setattr("app.services.order_logic.settings.packaging_standard_unit_price", 0.0)
    foods = [{"name": "A", "quantity": 1, "item_total": 9990.0}]
    fee_lines, extras = compute_fee_lines(foods, 9990.0, "delivery")
    kinds = [x.get("kind") for x in fee_lines if isinstance(x, dict)]
    assert "delivery" in kinds
    assert extras == 700.0


@pytest.mark.regression
def test_delivery_free_at_exact_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.services.order_logic.settings.pricing_delivery_free_threshold", 10000.0)
    monkeypatch.setattr("app.services.order_logic.settings.pricing_delivery_fee", 700.0)
    monkeypatch.setattr("app.services.order_logic.settings.packaging_standard_unit_price", 0.0)
    foods = [{"name": "A", "quantity": 1, "item_total": 10000.0}]
    fee_lines, extras = compute_fee_lines(foods, 10000.0, "delivery")
    kinds = [x.get("kind") for x in fee_lines if isinstance(x, dict)]
    assert "delivery" not in kinds
    assert extras == 0.0
