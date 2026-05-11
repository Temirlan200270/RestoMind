"""Тарификация v2: контейнеры и доставка."""

from app.schemas.ai_schemas import AIBrainResponse, OrderItem
from app.services.order_logic import (
    ValidatedOrder,
    build_demo_order_payload,
    build_order_items_json,
    compute_fee_lines,
    finalize_order_draft,
    merge_total_into_items_json,
)


def test_delivery_fee_under_threshold() -> None:
    """Доставка + сумма блюд ниже порога — строка «Доставка» 700 ₸."""
    foods = [{"name": "X", "quantity": 1, "item_total": 1000.0, "price_per_unit": 1000}]
    lines, extra = compute_fee_lines(foods, 1000.0, "delivery")
    kinds = [x["kind"] for x in lines]
    assert "delivery" in kinds
    assert extra >= 700.0


def test_delivery_free_above_threshold() -> None:
    """Сумма блюд от 10 000 — без строки доставки (только контейнер если цена > 0)."""
    foods = [{"name": "X", "quantity": 1, "item_total": 10_000.0, "price_per_unit": 10_000}]
    lines, extra = compute_fee_lines(foods, 10_000.0, "delivery")
    assert all(x["kind"] != "delivery" for x in lines)


def test_pickup_no_delivery_fee() -> None:
    """Самовывоз — без доставки даже при малой сумме."""
    foods = [{"name": "X", "quantity": 1, "item_total": 500.0, "price_per_unit": 500}]
    lines, _extra = compute_fee_lines(foods, 500.0, "pickup")
    assert all(x["kind"] != "delivery" for x in lines)


def test_build_order_items_json_totals() -> None:
    """Итог включает блюда и наценки."""
    validated = ValidatedOrder(
        valid_items=[
            {
                "name": "Плов",
                "quantity": 2,
                "price_per_unit": 1000.0,
                "item_total": 2000.0,
                "iiko_id": "u1",
                "exclude_ingredients": [],
            },
        ],
        unknown_items=[],
        total_price=2000.0,
        summary_text="test",
    )
    ai = AIBrainResponse(
        intent="order",
        reply_text="ok",
        items=[OrderItem(name="Плов", quantity=2)],
        order_type="delivery",
        payment_method="cash",
        delivery_address="ул. Тест 1",
    )
    payload, grand = build_order_items_json(validated, ai)
    merged = merge_total_into_items_json(payload, grand)
    assert merged["foods_subtotal"] == 2000.0
    assert float(merged["total_price"]) >= 2000.0
    assert "order_meta" in merged
    assert merged["order_meta"]["order_type"] == "delivery"
    assert "confidence" in merged["order_meta"]
    assert merged["order_meta"]["confidence"]["low_confidence"] is True
    assert "unverified_delivery_address" in merged["order_meta"]["confidence"]["reasons"]


def test_build_demo_order_payload_matches_v2_shape() -> None:
    """Демо/seed: тот же состав полей, что у заказа из чата."""
    food = [
        {
            "name": "Тест",
            "quantity": 1,
            "price_per_unit": 100.0,
            "item_total": 100.0,
            "exclude_ingredients": [],
            "iiko_id": "uuid-test",
        },
    ]
    payload, grand = build_demo_order_payload(
        food, "delivery", "card", delivery_address="ул. Тест 1",
    )
    assert "fee_lines" in payload
    assert "order_meta" in payload
    assert payload["order_meta"]["order_type"] == "delivery"
    assert float(payload["total_price"]) == grand


def test_prepayment_enforced_false_skips_threshold(monkeypatch) -> None:
    """Выключение предоплаты на филиале: флаг prepayment_enforced=False в build_order_items_json."""
    monkeypatch.setattr(
        "app.services.order_logic.settings.order_prepayment_threshold_kzt",
        1000.0,
    )
    validated = ValidatedOrder(
        valid_items=[
            {
                "name": "X",
                "quantity": 1,
                "price_per_unit": 5000.0,
                "item_total": 5000.0,
                "iiko_id": "u1",
                "exclude_ingredients": [],
            },
        ],
        unknown_items=[],
        total_price=5000.0,
        summary_text="x",
    )
    ai = AIBrainResponse(
        intent="order",
        reply_text="ok",
        items=[],
        order_type="pickup",
        payment_method="cash",
    )
    payload_off, _ = build_order_items_json(validated, ai, prepayment_enforced=False)
    assert payload_off["order_meta"]["requires_order_prepayment"] is False
    payload_on, _ = build_order_items_json(validated, ai, prepayment_enforced=True)
    assert payload_on["order_meta"]["requires_order_prepayment"] is True


def test_finalize_order_draft_matches_build_merge() -> None:
    """finalize_order_draft — обёртка над build + merge."""
    validated = ValidatedOrder(
        valid_items=[
            {
                "name": "Плов",
                "quantity": 1,
                "price_per_unit": 100.0,
                "item_total": 100.0,
                "iiko_id": "u1",
                "exclude_ingredients": [],
            },
        ],
        unknown_items=[],
        total_price=100.0,
        summary_text="x",
    )
    ai = AIBrainResponse(
        intent="order",
        reply_text="ok",
        items=[OrderItem(name="Плов", quantity=1)],
        order_type="pickup",
        payment_method="cash",
    )
    merged, total = finalize_order_draft(validated, ai)
    p2, g2 = build_order_items_json(validated, ai)
    assert merged == merge_total_into_items_json(p2, g2)
    assert total == g2
