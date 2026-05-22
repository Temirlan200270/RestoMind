"""Sales-by-hour insights for owner dashboard (Order-based MVP, not iiko OLAP)."""

from __future__ import annotations

from datetime import datetime
from typing import Any


def peak_hours_from_buckets(hour_buckets: dict[int, dict[str, Any]]) -> list[int]:
    """Top-3 local hours by revenue (ties by order count)."""
    rows = [
        {"hour": int(hh), **row}
        for hh, row in hour_buckets.items()
        if int(row.get("orders") or 0) > 0
    ]
    rows.sort(
        key=lambda row: (float(row.get("revenue") or 0), int(row.get("orders") or 0)),
        reverse=True,
    )
    return [int(row["hour"]) for row in rows[:3]]


def peak_hours_label(hours: list[int]) -> str:
    if not hours:
        return ""
    return ", ".join(f"{h:02d}:00" for h in hours)


def operator_upsell_time_hint(peak_hours: list[int], timezone_name: str) -> str | None:
    if not peak_hours:
        return None
    hours_label = peak_hours_label(peak_hours)
    tz = (timezone_name or "UTC").strip() or "UTC"
    return (
        f"Пики продаж по локальному времени ({tz}): {hours_label}. "
        "Настройте допродажи на эти часы в разделе «Настройки → Допродажи»."
    )


def build_hour_buckets_local(
    rows: list[tuple[datetime | None, float | int | None]],
    *,
    tz,
) -> dict[int, dict[str, Any]]:
    buckets: dict[int, dict[str, Any]] = {
        h: {"hour": h, "orders": 0, "revenue": 0.0} for h in range(24)
    }
    for created_at, total_price in rows:
        if created_at is None:
            continue
        dt = created_at
        if dt.tzinfo is None:
            from datetime import timezone

            dt = dt.replace(tzinfo=timezone.utc)
        hh = int(dt.astimezone(tz).hour)
        buckets[hh]["orders"] = int(buckets[hh]["orders"]) + 1
        buckets[hh]["revenue"] = float(buckets[hh]["revenue"]) + float(total_price or 0)
    return buckets
