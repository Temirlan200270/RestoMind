#!/usr/bin/env python3
"""CI helper: flag SELECTs on tenant models without organization_id in nearby scope."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCAN_DIRS = (
    ROOT / "app" / "api" / "admin",
    ROOT / "app" / "services",
)

TENANT_MODELS = ("Order", "ChatLog", "Booking", "MenuItem", "SystemEvent")

SCOPE_MARKERS = (
    "organization_id",
    "orders_tenant_clause",
    "org_orders",
    "_orders_tenant_clause",
    "orders_location_filter",
    "tenant-scope-ok",
    "org_id",
)


def _scan_file(path: Path) -> list[str]:
    hits: list[str] = []
    lines = path.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        if not any(f"select({m}" in line.replace(" ", "") or f"select({m}," in line for m in TENANT_MODELS):
            continue
        window = "\n".join(lines[i : min(i + 8, len(lines))])
        if any(marker in window for marker in SCOPE_MARKERS):
            continue
        hits.append(f"{path.relative_to(ROOT)}:{i + 1}: {line.strip()}")
    return hits


def main() -> int:
    strict = os.environ.get("TENANT_SCOPE_STRICT", "").strip() in ("1", "true", "yes")
    findings: list[str] = []
    for base in SCAN_DIRS:
        for path in base.rglob("*.py"):
            findings.extend(_scan_file(path))
    if findings:
        print("Potential tenant-scope violations (review required):")
        for f in findings[:40]:
            print(f"  - {f}")
        if len(findings) > 40:
            print(f"  ... and {len(findings) - 40} more")
        return 1 if strict else 0
    print("check_tenant_scope: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
