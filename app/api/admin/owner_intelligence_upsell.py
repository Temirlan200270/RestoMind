"""Owner Intelligence — атрибуция upsell (Revenue Copilot v2/v3)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin.deps import (
    _session_is_superadmin,
    _session_staff_user,
    admin_org_from_session,
    require_admin_session_active,
)
from app.db.session import get_db
from app.services.menu_profit_lab import get_copilot_candidate_lists
from app.services.tenant_scope import allowed_location_ids_for_staff
from app.services.upsell_attribution import build_upsell_impact_summary
from app.services.upsell_experiments import build_experiment_stats
from app.services.upsell_pair_mining import flatten_top_mined_pairs

router = APIRouter(
    prefix="/admin/owner-intelligence",
    tags=["Owner Intelligence"],
    dependencies=[Depends(require_admin_session_active)],
)


async def _location_scope_for_request(
    request: Request,
    db: AsyncSession,
    org_id: int,
    location_id: int | None,
) -> tuple[set[int] | None, bool]:
    staff = await _session_staff_user(request, db)
    is_super = await _session_is_superadmin(request, db)
    allowed = await allowed_location_ids_for_staff(
        db,
        staff=staff,
        org_id=org_id,
        is_superadmin=is_super,
    )
    if location_id is not None and allowed is not None and int(location_id) not in allowed:
        raise HTTPException(status_code=403, detail="Location is not allowed")
    return allowed, bool(location_id is not None or allowed is not None)


def _merge_best_variants(
    event_variants: list[dict],
    experiment_variants: list[dict],
) -> list[dict]:
    """Объединяет best_variants из событий и экспериментов (эксперименты приоритетнее)."""
    merged: dict[str, dict] = {}
    for item in event_variants:
        key = str(item.get("variant") or item.get("variant_key") or "").strip()
        if not key:
            continue
        merged[key] = dict(item)
    for item in experiment_variants:
        key = str(item.get("variant_key") or item.get("variant") or "").strip()
        if not key:
            continue
        merged[key] = {
            "variant": key,
            "variant_key": key,
            "rule_id": item.get("rule_id"),
            "shown": int(item.get("shown") or 0),
            "accepted": int(item.get("accepted") or 0),
            "conversion_rate": float(item.get("conversion_rate") or 0),
            "added_revenue": float(item.get("added_revenue") or 0),
            "source": "experiment",
        }
    out = list(merged.values())
    out.sort(
        key=lambda row: (
            -float(row.get("conversion_rate") or 0),
            -float(row.get("added_revenue") or 0),
            -int(row.get("shown") or 0),
        ),
    )
    return out[:10]


@router.get("/upsell-impact")
async def get_upsell_impact(
    request: Request,
    db: AsyncSession = Depends(get_db),
    period: str = Query("today"),
    location_id: Annotated[int | None, Query(ge=1)] = None,
) -> dict:
    org_id = admin_org_from_session(request)
    allowed_location_ids, _ = await _location_scope_for_request(
        request,
        db,
        org_id,
        location_id,
    )
    summary = await build_upsell_impact_summary(
        db,
        org_id,
        period,
        location_id=location_id,
        allowed_location_ids=allowed_location_ids,
    )
    experiment_stats = await build_experiment_stats(db, org_id)
    summary["experiment_stats"] = experiment_stats
    summary["best_variants"] = _merge_best_variants(
        list(summary.get("best_variants") or []),
        list(experiment_stats.get("best_variants") or []),
    )

    if not summary.get("worst_offers"):
        summary["worst_offers"] = []

    try:
        summary["best_pairs"] = await flatten_top_mined_pairs(
            db,
            org_id,
            period="30d" if period in {"month", "30d"} else "7d",
            location_id=location_id,
            allowed_location_ids=allowed_location_ids,
            limit=10,
        )
    except Exception:
        summary["best_pairs"] = []

    try:
        copilot = await get_copilot_candidate_lists(
            db,
            org_id,
            period="7d",
            location_id=location_id,
            allowed_location_ids=allowed_location_ids,
        )
        summary["promote_today_candidates"] = list(copilot.get("promote_today_candidates") or [])
    except Exception:
        summary["promote_today_candidates"] = []

    summary.setdefault("offered", summary.get("shown", 0))
    return summary
