"""P1.5: order_meta.confidence (fuzzy / адрес доставки)."""

from app.services.order_logic import ValidatedOrder, merge_confidence_into_order_meta


def test_merge_confidence_fuzzy_below_threshold() -> None:
    v = ValidatedOrder(
        valid_items=[],
        unknown_items=[],
        total_price=0.0,
        summary_text="",
        fuzzy_match_details=[
            {"source_name": "тест", "matched_menu_name": "тестик", "similarity": 0.55},
        ],
    )
    meta: dict = {"order_type": "pickup", "delivery_address": ""}
    merge_confidence_into_order_meta(meta, v)
    assert meta["confidence"]["low_confidence"] is True
    assert "fuzzy_menu_match" in meta["confidence"]["reasons"]
    assert meta["confidence"]["details"]["fuzzy_matches"]


def test_merge_confidence_unverified_delivery_address() -> None:
    v = ValidatedOrder([], [], 0.0, "", [])
    meta = {"order_type": "delivery", "delivery_address": "ул. Тест 1"}
    merge_confidence_into_order_meta(meta, v)
    assert meta["confidence"]["low_confidence"] is True
    assert "unverified_delivery_address" in meta["confidence"]["reasons"]


def test_merge_confidence_verified_address_ok() -> None:
    v = ValidatedOrder([], [], 0.0, "", [])
    meta = {
        "order_type": "delivery",
        "delivery_address": "ул. Тест 1",
        "delivery_address_verified": True,
    }
    merge_confidence_into_order_meta(meta, v)
    assert meta["confidence"]["low_confidence"] is False
    assert meta["confidence"]["reasons"] == []
