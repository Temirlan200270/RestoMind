"""Sales insights helpers and dashboard peak hours."""

from datetime import datetime, timezone

from app.services.sales_insights import (
    build_hour_buckets_local,
    operator_upsell_time_hint,
    peak_hours_from_buckets,
    peak_hours_label,
)


def test_peak_hours_from_buckets_orders_by_revenue() -> None:
    buckets = {
        12: {"hour": 12, "orders": 2, "revenue": 100.0},
        19: {"hour": 19, "orders": 5, "revenue": 500.0},
        9: {"hour": 9, "orders": 1, "revenue": 50.0},
    }
    assert peak_hours_from_buckets(buckets) == [19, 12, 9]


def test_operator_upsell_time_hint_no_dev_jargon() -> None:
    hint = operator_upsell_time_hint([19, 13], "Asia/Almaty")
    assert hint is not None
    assert "UpsellRule" not in hint
    assert "trigger_mode" not in hint
    assert "19:00" in hint
    assert "Допродажи" in hint


def test_peak_hours_label() -> None:
    assert peak_hours_label([19, 13]) == "19:00, 13:00"


def test_build_hour_buckets_local() -> None:
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("Asia/Almaty")
    rows = [
        (datetime(2026, 5, 21, 14, 0, tzinfo=timezone.utc), 1000),
        (datetime(2026, 5, 21, 15, 0, tzinfo=timezone.utc), 2000),
    ]
    buckets = build_hour_buckets_local(rows, tz=tz)
    assert sum(b["orders"] for b in buckets.values()) == 2
