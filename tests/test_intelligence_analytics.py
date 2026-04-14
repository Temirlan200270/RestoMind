"""Агрегаты Intelligence Dashboard (menu engineering, география)."""

from types import SimpleNamespace

from app.services.intelligence_analytics import (
    delivery_geo_rows,
    menu_engineering_rows,
    normalize_address_bucket,
)


def test_normalize_address_bucket_collapses_whitespace() -> None:
    assert normalize_address_bucket("  ул.  Абая ,  15  ") == "ул. абая, 15"


def test_menu_engineering_trace_aggregation() -> None:
    o1 = SimpleNamespace(
        items_json={
            "order_meta": {
                "recommendation_trace": [
                    {"offered": "Салат Цезарь", "offered_iiko_id": "uuid-1", "accepted": False},
                    {"offered": "Салат Цезарь", "offered_iiko_id": "uuid-1", "accepted": True, "accepted_revenue_kzt": 1500},
                ],
            },
        },
    )
    rows = menu_engineering_rows([o1])
    assert len(rows) == 1
    r = rows[0]
    assert r["offers"] == 2
    assert r["accepts"] == 1
    assert r["conversion_pct"] == 50.0
    assert r["revenue"] == 1500.0
    assert r["key"] == "iiko:uuid-1"


def test_delivery_geo_groups_by_area() -> None:
    o1 = SimpleNamespace(
        user_id=1,
        total_price=5000.0,
        items_json={
            "order_meta": {
                "order_type": "delivery",
                "delivery_address": "ул. Абая, 10",
            },
        },
    )
    o2 = SimpleNamespace(
        user_id=2,
        total_price=3000.0,
        items_json={
            "order_meta": {
                "order_type": "delivery",
                "delivery_address": "ул. Абая, 10",
            },
        },
    )
    rows = delivery_geo_rows([o1, o2])
    assert len(rows) == 1
    assert rows[0]["orders"] == 2
    assert rows[0]["unique_customers"] == 2
    assert rows[0]["orders_per_customer"] == 1.0
    assert rows[0]["revenue"] == 8000.0


def test_delivery_geo_skips_non_delivery() -> None:
    o = SimpleNamespace(
        user_id=1,
        total_price=100.0,
        items_json={
            "order_meta": {
                "order_type": "pickup",
                "delivery_address": "где-то",
            },
        },
    )
    assert delivery_geo_rows([o]) == []
