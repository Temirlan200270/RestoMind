"""Сводка nomenclature.json (поддерживает UTF-8 BOM). Запуск: python scripts/_summarize_nomenclature.py [путь]"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    p = root / "nomenclature.json"
    if len(sys.argv) > 1:
        p = Path(sys.argv[1])

    out_path = root / "scripts" / "_nomenclature_summary.txt"

    with p.open(encoding="utf-8-sig") as f:
        d = json.load(f)

    groups = d.get("groups") or []
    products = d.get("products") or []

    lines: list[str] = []

    def pr(msg: str = "") -> None:
        lines.append(msg)

    def walk(nodes: list) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        for g in nodes:
            if not isinstance(g, dict):
                continue
            gid = str(g.get("id") or "")
            name = str(g.get("name") or "").strip()
            out.append((gid, name))
            kids = g.get("childGroups") or g.get("childgroups") or []
            if isinstance(kids, list):
                out.extend(walk(kids))
        return out

    grp_map: dict[str, str] = {}
    for gid, nm in walk(groups):
        if gid:
            grp_map[gid] = nm

    def cat_uuids(pr_: dict) -> list[str]:
        refs: list[str] = []
        pg = pr_.get("parentGroup")
        if pg:
            if isinstance(pg, dict) and pg.get("id"):
                refs.append(str(pg["id"]))
            elif isinstance(pg, str):
                refs.append(pg)
        for k in ("groupId", "productCategoryId"):
            v = pr_.get(k)
            if v:
                if isinstance(v, dict) and v.get("id"):
                    refs.append(str(v["id"]))
                elif not isinstance(v, dict):
                    refs.append(str(v))
        return refs

    def display_cat(pr_: dict) -> str:
        for uid in cat_uuids(pr_):
            g = grp_map.get(uid, "")
            if g:
                return g
        pg = pr_.get("parentGroup")
        if isinstance(pg, dict) and pg.get("name"):
            return str(pg["name"]).strip()
        for k in ("productCategoryName", "groupName", "categoryName"):
            v = pr_.get(k)
            if v and str(v).strip():
                return str(v).strip()
        return ""

    keywords = ("суши", "ролл", "саши", "нигир")
    group_hits = sorted({nm for nm in grp_map.values() if any(k in nm.lower() for k in keywords)})

    cat_dist: Counter[str] = Counter()
    kw_rows: Counter[str] = Counter()
    for prod in products:
        if not isinstance(prod, dict):
            continue
        cn = display_cat(prod) or "(empty)"
        cat_dist[cn] += 1
        blob = ((prod.get("name") or "") + " " + cn).lower()
        for k in keywords:
            if k in blob:
                kw_rows[k] += 1

    sushi_cats = sorted(
        [(n, c) for n, c in cat_dist.items() if any(k in n.lower() for k in keywords)],
        key=lambda x: (-x[1], x[0]),
    )

    ids = [str(prod.get("id") or "") for prod in products if isinstance(prod, dict)]
    unique = {i for i in ids if i}

    types = Counter(str(prod.get("type") or "(none)") for prod in products if isinstance(prod, dict))

    pr(f"=== {p.name} ===")
    pr(f"correlationId: {d.get('correlationId')}")
    pr(f"top-level groups (array): {len(groups)}")
    pr(f"flattened group ids (nested walk): {len(grp_map)}")
    pr(f"products (array length): {len(products)}")
    pr(f"unique product ids: {len(unique)}")
    pr(f"duplicate rows (len - unique): {len(ids) - len(unique)}")
    pr("")
    pr("product.type:")
    for t, n in types.most_common():
        pr(f"  {t}: {n}")
    pr("")
    pr("Groups with name containing суши/ролл/саши/нигир:")
    if not group_hits:
        pr("  (none)")
    else:
        for nm in group_hits:
            pr(f"  - {nm}")
    pr("")
    pr("Product counts by resolved category (same keywords in category name):")
    if not sushi_cats:
        pr("  (none)")
    else:
        for nm, cnt in sushi_cats:
            pr(f"  {cnt:4}  {nm}")
    pr("")
    pr(f"Rows (name or resolved category) matching keywords {keywords}: {dict(kw_rows)}")
    pr("")
    pr("Top 25 categories by product row count:")
    for nm, cnt in cat_dist.most_common(25):
        pr(f"  {cnt:4}  {nm}")
    pr("")

    report = "\n".join(lines) + "\n"
    out_path.write_text(report, encoding="utf-8")

    print(report, end="")


if __name__ == "__main__":
    main()
