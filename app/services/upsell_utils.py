from __future__ import annotations

from typing import Any, Mapping


def cart_iiko_ids(items: list[dict[str, Any]]) -> set[str]:
    out: set[str] = set()
    for it in items:
        if not isinstance(it, dict):
            continue
        iid = str(it.get("iiko_id") or "").strip().lower()
        if iid:
            out.add(iid)
    return out


def rejected_upsell_iiko_ids(meta: Mapping[str, Any]) -> set[str]:
    raw = meta.get("upsell_rejected_iiko_ids")
    if not isinstance(raw, list):
        return set()
    return {str(x).strip().lower() for x in raw if str(x).strip()}

