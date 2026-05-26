"""CSV import of menu item cost prices (Menu Profit Lab v2)."""

from __future__ import annotations

import csv
import io
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MenuItem


def _norm_name(name: str) -> str:
    return " ".join((name or "").strip().lower().split())


def _parse_cost(raw: str) -> float | None:
    text = (raw or "").strip().replace(" ", "").replace(",", ".")
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    return value if value >= 0 else None


def _preview_row(item: MenuItem, cost: float, *, match_by: str) -> dict[str, Any]:
    old_cost = float(item.cost_price) if item.cost_price is not None else None
    return {
        "menu_item_id": int(item.id),
        "name": item.name,
        "iiko_id": item.iiko_id,
        "current_cost_price": old_cost,
        "new_cost_price": cost,
        "changed": old_cost is None or round(old_cost, 2) != round(cost, 2),
        "match_by": match_by,
    }


async def _menu_lookups(
    db: AsyncSession,
    organization_id: int,
) -> tuple[dict[str, MenuItem], dict[str, MenuItem]]:
    org_id = int(organization_id)
    menu_rows = (
        await db.execute(
            select(MenuItem).where(MenuItem.organization_id == org_id),
        )
    ).scalars().all()
    by_iiko: dict[str, MenuItem] = {}
    by_name: dict[str, MenuItem] = {}
    for item in menu_rows:
        if item.iiko_id:
            by_iiko[str(item.iiko_id).strip().lower()] = item
        by_name[_norm_name(item.name)] = item
    return by_iiko, by_name


def _parse_csv_rows(
    csv_text: str,
    by_iiko: dict[str, MenuItem],
    by_name: dict[str, MenuItem],
) -> dict[str, Any]:
    reader = csv.DictReader(io.StringIO(csv_text))
    if reader.fieldnames is None:
        return {
            "ok": False,
            "error": "empty_csv",
            "updated": 0,
            "skipped": 0,
            "errors": ["CSV пуст или без заголовка"],
            "rows": [],
        }

    updated = 0
    skipped = 0
    errors: list[str] = []
    preview_rows: list[dict[str, Any]] = []

    for idx, row in enumerate(reader, start=2):
        if not isinstance(row, dict):
            skipped += 1
            continue
        iiko_key = str(row.get("iiko_id") or row.get("uuid") or "").strip().lower()
        name_key = _norm_name(str(row.get("name") or ""))
        cost = _parse_cost(str(row.get("cost_price") or row.get("cost") or ""))
        if cost is None:
            skipped += 1
            errors.append(f"Строка {idx}: некорректная себестоимость")
            continue

        item = by_iiko.get(iiko_key) if iiko_key else None
        match_by = "iiko_id"
        if item is None and name_key:
            item = by_name.get(name_key)
            match_by = "name"
        if item is None:
            skipped += 1
            errors.append(f"Строка {idx}: позиция не найдена")
            continue

        preview = _preview_row(item, cost, match_by=match_by)
        preview_rows.append(preview)
        if preview["changed"]:
            updated += 1
        else:
            skipped += 1

    return {
        "ok": True,
        "updated": updated,
        "skipped": skipped,
        "errors": errors[:20],
        "rows_total": updated + skipped + len(errors),
        "rows": preview_rows[:200],
    }


async def preview_menu_costs_from_csv(
    db: AsyncSession,
    organization_id: int,
    csv_text: str,
) -> dict[str, Any]:
    """
    Предпросмотр импорта себестоимости из CSV без записи в БД.

    Колонки: ``iiko_id`` или ``name`` + ``cost_price`` (или ``cost``).
    """
    by_iiko, by_name = await _menu_lookups(db, organization_id)
    return _parse_csv_rows(csv_text, by_iiko, by_name)


async def import_menu_costs_from_csv(
    db: AsyncSession,
    organization_id: int,
    csv_text: str,
) -> dict[str, Any]:
    """
    Импорт себестоимости из CSV.

    Колонки: ``iiko_id`` или ``name`` + ``cost_price`` (или ``cost``).
    Совпадение по iiko_id в приоритете, иначе по нормализованному названию.
    """
    by_iiko, by_name = await _menu_lookups(db, organization_id)
    parsed = _parse_csv_rows(csv_text, by_iiko, by_name)
    if not parsed.get("ok"):
        return parsed

    applied = 0
    for preview in parsed.get("rows") or []:
        if not preview.get("changed"):
            continue
        item_id = int(preview["menu_item_id"])
        item = by_iiko.get(str(preview.get("iiko_id") or "").strip().lower())
        if item is None or int(item.id) != item_id:
            item = by_name.get(_norm_name(str(preview.get("name") or "")))
        if item is None or int(item.id) != item_id:
            continue
        item.cost_price = float(preview["new_cost_price"])
        applied += 1

    await db.flush()
    return {
        "ok": True,
        "updated": applied,
        "skipped": int(parsed.get("skipped") or 0),
        "errors": parsed.get("errors") or [],
        "rows_total": int(parsed.get("rows_total") or 0),
        "rows": parsed.get("rows") or [],
    }
