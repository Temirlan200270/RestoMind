"""
Агрегаты для Intelligence Dashboard: Menu Engineering (ИИ-допродажи), география доставки.

Единый разбор recommendation_trace / recommendation из order_meta (без дублирования в admin).
"""

from __future__ import annotations

import re
from typing import Any


def order_meta_from_items_json(items_json: dict[str, Any] | None) -> dict[str, Any]:
    """Метаданные заказа (v2) из items_json."""
    if not isinstance(items_json, dict):
        return {}
    raw = items_json.get("order_meta")
    return raw if isinstance(raw, dict) else {}


def list_recommendation_events(items_json: dict[str, Any] | None) -> list[dict[str, Any]]:
    """
    События допродажи из заказа: элементы trace с offered/offered_iiko_id
    или одно событие из recommendation.
    """
    if not isinstance(items_json, dict):
        return []
    meta = order_meta_from_items_json(items_json)
    trace = meta.get("recommendation_trace")
    out: list[dict[str, Any]] = []
    if isinstance(trace, list) and trace:
        for ev in trace:
            if isinstance(ev, dict) and (ev.get("offered") or ev.get("offered_iiko_id")):
                out.append(ev)
        return out
    rec = meta.get("recommendation")
    if isinstance(rec, dict) and (rec.get("offered") or rec.get("offered_iiko_id")):
        out.append(rec)
    return out


def upsell_stats_from_items_json(items_json: dict[str, Any] | None) -> tuple[int, int, float]:
    """
    Число предложений допродаж, принятий и сумма по accepted_revenue_kzt за заказ.
    Считает события в recommendation_trace; без трассы — fallback на recommendation + имя в позициях.
    """
    if not isinstance(items_json, dict):
        return 0, 0, 0.0
    meta = order_meta_from_items_json(items_json)
    events = list_recommendation_events(items_json)
    if not events:
        return 0, 0, 0.0
    trace = meta.get("recommendation_trace")
    if isinstance(trace, list) and trace:
        offers = 0
        accepts = 0
        revenue = 0.0
        for ev in events:
            if ev.get("offered") or ev.get("offered_iiko_id"):
                offers += 1
            if ev.get("accepted") is True:
                accepts += 1
                revenue += float(ev.get("accepted_revenue_kzt") or 0)
        return offers, accepts, revenue
    rec = events[0]
    offers = 1
    accepts = 0
    revenue = 0.0
    if rec.get("accepted") is True:
        accepts = 1
        revenue += float(rec.get("accepted_revenue_kzt") or 0)
    else:
        off = str(rec.get("offered") or "").strip().lower()
        items = items_json.get("items")
        if off and isinstance(items, list):
            for it in items:
                if not isinstance(it, dict):
                    continue
                nm = str(it.get("name") or "").lower()
                if off and off in nm:
                    accepts = 1
                    break
    return offers, accepts, revenue


def normalize_address_bucket(addr: str) -> str:
    """
    Грубая нормализация адреса для группировки (улица/район без точного номера квартиры).
    Не геокодирование — только обрезка и приведение к одному виду.
    """
    s = (addr or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    parts = [p.strip() for p in s.split(",") if p.strip()]
    if len(parts) >= 2:
        s = ", ".join(parts[:2])
    if len(s) > 96:
        s = s[:96].rsplit(" ", 1)[0] if " " in s[:96] else s[:96]
    return s or "?"


def menu_engineering_rows(orders: list[Any]) -> list[dict[str, Any]]:
    """
    По recommendation_trace / recommendation: какие позиции ИИ предлагал чаще всего и конверсия.

    Ключ: offered_iiko_id (если есть) иначе нормализованное имя offered.
    """
    stats: dict[str, dict[str, Any]] = {}

    def ensure(k: str, label: str) -> dict[str, Any]:
        if k not in stats:
            stats[k] = {"key": k, "label": label, "offers": 0, "accepts": 0, "revenue": 0.0}
        return stats[k]

    for o in orders:
        ij = o.items_json if isinstance(o.items_json, dict) else None
        if not ij:
            continue
        for ev in list_recommendation_events(ij):
            oid = (ev.get("offered_iiko_id") or "").strip()
            off = (ev.get("offered") or "").strip()
            if oid:
                k = f"iiko:{oid}"
                label = off or oid
            elif off:
                k = f"name:{off.lower()[:200]}"
                label = off
            else:
                continue
            st = ensure(k, label)
            st["offers"] = int(st["offers"]) + 1
            if ev.get("accepted") is True:
                st["accepts"] = int(st["accepts"]) + 1
                st["revenue"] = float(st["revenue"]) + float(ev.get("accepted_revenue_kzt") or 0)

    rows: list[dict[str, Any]] = []
    for st in stats.values():
        off = int(st["offers"])
        acc = int(st["accepts"])
        conv = round(100.0 * acc / off, 1) if off else 0.0
        rows.append(
            {
                "key": st["key"],
                "label": st["label"],
                "offers": off,
                "accepts": acc,
                "conversion_pct": conv,
                "revenue": round(float(st["revenue"]), 2),
            }
        )
    rows.sort(key=lambda x: (-x["offers"], -float(x["revenue"])))
    return rows[:30]


def delivery_geo_rows(orders: list[Any]) -> list[dict[str, Any]]:
    """
    Заказы с типом delivery и непустым адресом: район → заказы, выручка, уникальные клиенты.

    «Лояльность» района — orders_per_customer (среднее число заказов на клиента в бакете).
    """
    buckets: dict[str, dict[str, Any]] = {}

    for o in orders:
        ij = o.items_json if isinstance(o.items_json, dict) else None
        meta = order_meta_from_items_json(ij)
        if (meta.get("order_type") or "").strip().lower() != "delivery":
            continue
        addr = (meta.get("delivery_address") or "").strip()
        if len(addr) < 4:
            continue
        key = normalize_address_bucket(addr)
        if key not in buckets:
            buckets[key] = {"orders": 0, "revenue": 0.0, "users": set()}
        b = buckets[key]
        b["orders"] = int(b["orders"]) + 1
        b["revenue"] = float(b["revenue"]) + float(o.total_price or 0)
        b["users"].add(int(o.user_id))

    rows: list[dict[str, Any]] = []
    for key, b in buckets.items():
        nu = len(b["users"])
        no = int(b["orders"])
        label = key if len(key) <= 56 else key[:53] + "…"
        rows.append(
            {
                "area_key": key,
                "display_label": label,
                "orders": no,
                "revenue": round(float(b["revenue"]), 2),
                "unique_customers": nu,
                "orders_per_customer": round(no / nu, 2) if nu else 0.0,
            }
        )
    rows.sort(key=lambda x: (-x["revenue"], -x["orders"]))
    return rows[:25]
