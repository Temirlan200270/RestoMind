"""Summarize an iiko nomenclature JSON dump.

Usage:
  python scripts/_summarize_nomenclature.py [path] [--out report.txt]
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any


def _walk_groups(nodes: list[Any]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for group in nodes:
        if not isinstance(group, dict):
            continue
        group_id = str(group.get("id") or "")
        name = str(group.get("name") or "").strip()
        out.append((group_id, name))
        kids = group.get("childGroups") or group.get("childgroups") or []
        if isinstance(kids, list):
            out.extend(_walk_groups(kids))
    return out


def _category_refs(product: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    parent_group = product.get("parentGroup")
    if parent_group:
        if isinstance(parent_group, dict) and parent_group.get("id"):
            refs.append(str(parent_group["id"]))
        elif isinstance(parent_group, str):
            refs.append(parent_group)
    for key in ("groupId", "productCategoryId"):
        value = product.get(key)
        if not value:
            continue
        if isinstance(value, dict) and value.get("id"):
            refs.append(str(value["id"]))
        elif not isinstance(value, dict):
            refs.append(str(value))
    return refs


def _display_category(product: dict[str, Any], group_map: dict[str, str]) -> str:
    for uid in _category_refs(product):
        group_name = group_map.get(uid, "")
        if group_name:
            return group_name
    parent_group = product.get("parentGroup")
    if isinstance(parent_group, dict) and parent_group.get("name"):
        return str(parent_group["name"]).strip()
    for key in ("productCategoryName", "groupName", "categoryName"):
        value = product.get(key)
        if value and str(value).strip():
            return str(value).strip()
    return ""


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    args = sys.argv[1:]
    if any(arg in {"-h", "--help"} for arg in args):
        print(__doc__.strip())
        return

    out_path: Path | None = None
    if "--out" in args:
        idx = args.index("--out")
        try:
            out_path = Path(args[idx + 1])
        except IndexError as exc:
            raise SystemExit("--out requires a path") from exc
        del args[idx : idx + 2]

    dump_path = Path(args[0]) if args else root / "nomenclature.json"

    with dump_path.open(encoding="utf-8-sig") as file:
        data = json.load(file)

    groups = data.get("groups") or []
    products = data.get("products") or []
    group_map = {gid: name for gid, name in _walk_groups(groups) if gid}

    keywords = ("суши", "ролл", "саши", "нигири")
    group_hits = sorted({name for name in group_map.values() if any(k in name.lower() for k in keywords)})

    category_distribution: Counter[str] = Counter()
    keyword_rows: Counter[str] = Counter()
    for product in products:
        if not isinstance(product, dict):
            continue
        category_name = _display_category(product, group_map) or "(empty)"
        category_distribution[category_name] += 1
        blob = f"{product.get('name') or ''} {category_name}".lower()
        for keyword in keywords:
            if keyword in blob:
                keyword_rows[keyword] += 1

    keyword_categories = sorted(
        [(name, count) for name, count in category_distribution.items() if any(k in name.lower() for k in keywords)],
        key=lambda item: (-item[1], item[0]),
    )

    ids = [str(product.get("id") or "") for product in products if isinstance(product, dict)]
    unique_ids = {item_id for item_id in ids if item_id}
    product_types = Counter(str(product.get("type") or "(none)") for product in products if isinstance(product, dict))

    lines = [
        f"=== {dump_path.name} ===",
        f"correlationId: {data.get('correlationId')}",
        f"top-level groups (array): {len(groups)}",
        f"flattened group ids (nested walk): {len(group_map)}",
        f"products (array length): {len(products)}",
        f"unique product ids: {len(unique_ids)}",
        f"duplicate rows (len - unique): {len(ids) - len(unique_ids)}",
        "",
        "product.type:",
    ]
    lines.extend(f"  {product_type}: {count}" for product_type, count in product_types.most_common())
    lines.extend(["", "Groups with name containing суши/ролл/саши/нигири:"])
    lines.extend(f"  - {name}" for name in group_hits) if group_hits else lines.append("  (none)")
    lines.extend(["", "Product counts by resolved category (same keywords in category name):"])
    lines.extend(f"  {count:4}  {name}" for name, count in keyword_categories) if keyword_categories else lines.append("  (none)")
    lines.extend(["", f"Rows (name or resolved category) matching keywords {keywords}: {dict(keyword_rows)}", ""])
    lines.append("Top 25 categories by product row count:")
    lines.extend(f"  {count:4}  {name}" for name, count in category_distribution.most_common(25))

    report = "\n".join(lines) + "\n"
    if out_path is not None:
        out_path.write_text(report, encoding="utf-8")
    print(report, end="")


if __name__ == "__main__":
    main()
