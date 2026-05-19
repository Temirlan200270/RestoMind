"""Upsell rules and packaging rules admin API (E0.1 split from _monolith.py)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MenuItem, PackagingRule, UpsellRule
from app.db.session import get_db
from app.services.intelligence_analytics import order_meta_from_items_json
from app.services.order_logic import PACKAGING_SEED, compute_fee_lines, load_packaging_rules

from .deps import (
    _menu_item_in_org,
    _order_in_org,
    _packaging_tenant_clause,
    admin_actor_key,
    admin_org_from_session,
    require_admin_session_active,
)
from .menu_schemas import menu_item_dict as _menu_item_dict

router = APIRouter(
    prefix="/admin",
    tags=["Rules"],
    dependencies=[Depends(require_admin_session_active)],
)


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _packaging_rule_dict(r: PackagingRule) -> dict:
    return {
        "id": r.id,
        "kind": r.kind,
        "name": r.name,
        "price": float(r.price),
        "iiko_product_id": r.iiko_product_id or "",
        "keywords": r.keywords or "",
        "option_key": r.option_key or "",
        "scope": getattr(r, "scope", None) or "item",
        "category_match": getattr(r, "category_match", None) or "",
        "is_active": r.is_active,
        "sort_order": r.sort_order,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    }


def _upsell_rule_dict(r: UpsellRule) -> dict:
    return {
        "id": r.id,
        "organization_id": r.organization_id,
        "trigger_mode": r.trigger_mode,
        "trigger_category": r.trigger_category,
        "suggest_category": r.suggest_category,
        "min_order_sum": float(r.min_order_sum or 0),
        "max_order_sum": float(r.max_order_sum) if r.max_order_sum is not None else None,
        "phrase_template": r.phrase_template or "",
        "sort_order": r.sort_order,
        "is_active": bool(r.is_active),
    }


# ─── Schemas ─────────────────────────────────────────────────────────────────


class UpsellRuleCreateBody(BaseModel):
    trigger_mode: str = "missing_category"
    trigger_category: str = Field(..., min_length=1, max_length=120)
    suggest_category: str = Field(..., min_length=1, max_length=120)
    min_order_sum: float = Field(0, ge=0)
    max_order_sum: float | None = Field(None, ge=0)
    phrase_template: str = ""
    sort_order: int = 0
    is_active: bool = True


class UpsellRulePatchBody(BaseModel):
    trigger_mode: str | None = None
    trigger_category: str | None = Field(None, max_length=120)
    suggest_category: str | None = Field(None, max_length=120)
    min_order_sum: float | None = Field(None, ge=0)
    max_order_sum: float | None = None
    phrase_template: str | None = None
    sort_order: int | None = None
    is_active: bool | None = None


class UpsellFeedbackBody(BaseModel):
    mode: Literal["suggest", "forbid"] = "forbid"
    trigger_category: str = Field("", max_length=120)
    suggest_category: str = Field("", max_length=120)
    item_iiko_id: str = Field("", max_length=100)
    item_name: str = Field("", max_length=255)
    phrase_template: str = Field("", max_length=1000)
    is_active: bool = True


class PackagingRuleCreateBody(BaseModel):
    kind: str = Field(..., min_length=1, max_length=60)
    name: str = Field(..., min_length=1, max_length=200)
    price: float = Field(0, ge=0)
    iiko_product_id: str | None = None
    keywords: str = ""
    option_key: str = ""
    scope: Literal["item", "category", "order"] = "item"
    category_match: str = Field("", max_length=120)
    is_active: bool = True
    sort_order: int = 0


class PackagingRulePatchBody(BaseModel):
    kind: str | None = Field(None, min_length=1, max_length=60)
    name: str | None = Field(None, min_length=1, max_length=200)
    price: float | None = Field(None, ge=0)
    iiko_product_id: str | None = None
    keywords: str | None = None
    option_key: str | None = None
    scope: Literal["item", "category", "order"] | None = None
    category_match: str | None = Field(None, max_length=120)
    is_active: bool | None = None
    sort_order: int | None = None


class PackagingPreviewBody(BaseModel):
    menu_item_id: int = Field(..., ge=1)
    quantity: int = Field(1, ge=1, le=999)
    order_type: Literal["delivery", "pickup", "hall"] = Field(...)
    packaging_plov_1kg: Literal["", "tabak", "foil_kazan"] = ""


# ─── Upsell rules ─────────────────────────────────────────────────────────────


@router.get("/upsell-rules")
async def list_upsell_rules(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    org_id = admin_org_from_session(request)
    result = await db.execute(
        select(UpsellRule)
        .where(UpsellRule.organization_id == org_id)
        .order_by(UpsellRule.sort_order.desc(), UpsellRule.id),
    )
    rows = list(result.scalars().all())
    return {"items": [_upsell_rule_dict(r) for r in rows]}


@router.post("/upsell-rules")
async def create_upsell_rule(
    request: Request,
    body: UpsellRuleCreateBody,
    db: AsyncSession = Depends(get_db),
) -> dict:
    org_id = admin_org_from_session(request)
    tmpl = (body.phrase_template or "").strip() or (
        "К заказу отлично подойдёт {item_name} ({price} ₸). Добавить?"
    )
    row = UpsellRule(
        organization_id=org_id,
        trigger_mode=(body.trigger_mode or "missing_category").strip(),
        trigger_category=body.trigger_category.strip(),
        suggest_category=body.suggest_category.strip(),
        min_order_sum=body.min_order_sum,
        max_order_sum=body.max_order_sum,
        phrase_template=tmpl,
        sort_order=body.sort_order,
        is_active=body.is_active,
    )
    db.add(row)
    await db.flush()
    return {"ok": True, "item": _upsell_rule_dict(row)}


@router.patch("/upsell-rules/{rule_id}")
async def patch_upsell_rule(
    request: Request,
    rule_id: int,
    body: UpsellRulePatchBody,
    db: AsyncSession = Depends(get_db),
) -> dict:
    org_id = admin_org_from_session(request)
    row = await db.get(UpsellRule, rule_id)
    if row is None or int(row.organization_id) != org_id:
        raise HTTPException(status_code=404, detail="Правило не найдено")
    data = body.model_dump(exclude_unset=True)
    for key in ("trigger_mode", "trigger_category", "suggest_category", "phrase_template"):
        if key in data and data[key] is not None:
            data[key] = str(data[key]).strip()
    for key, value in data.items():
        setattr(row, key, value)
    await db.flush()
    return {"ok": True, "item": _upsell_rule_dict(row)}


@router.delete("/upsell-rules/{rule_id}")
async def delete_upsell_rule(
    request: Request,
    rule_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict:
    org_id = admin_org_from_session(request)
    row = await db.get(UpsellRule, rule_id)
    if row is None or int(row.organization_id) != org_id:
        raise HTTPException(status_code=404, detail="Правило не найдено")
    await db.delete(row)
    await db.flush()
    return {"ok": True}


@router.post("/orders/{order_id}/feedback/upsell-rule")
async def create_upsell_rule_from_order_feedback(
    request: Request,
    order_id: int,
    body: UpsellFeedbackBody,
    db: AsyncSession = Depends(get_db),
) -> dict:
    org_id = admin_org_from_session(request)
    order = await _order_in_org(db, order_id, org_id, request=request)
    items_json = order.items_json if isinstance(order.items_json, dict) else {}
    foods = items_json.get("items") if isinstance(items_json.get("items"), list) else []
    first_food = next((x for x in foods if isinstance(x, dict)), {})
    meta = order_meta_from_items_json(items_json)
    trace = meta.get("recommendation_trace")
    trace_item = next((x for x in trace if isinstance(x, dict)), {}) if isinstance(trace, list) else {}

    trigger_category = (body.trigger_category or str(first_food.get("category") or "")).strip()
    suggest_category = (body.suggest_category or str(trace_item.get("category") or trace_item.get("suggest_category") or "")).strip()
    item_iiko_id = (body.item_iiko_id or str(trace_item.get("item_iiko_id") or trace_item.get("iiko_id") or "")).strip()
    item_name = (body.item_name or str(trace_item.get("item_name") or trace_item.get("name") or "")).strip()

    if body.mode == "suggest":
        if not trigger_category or not suggest_category:
            raise HTTPException(status_code=400, detail="trigger_category and suggest_category are required")
        row = UpsellRule(
            organization_id=org_id,
            trigger_mode="missing_category",
            trigger_category=trigger_category,
            suggest_category=suggest_category,
            min_order_sum=0,
            max_order_sum=None,
            phrase_template=(body.phrase_template or "").strip() or "К заказу хорошо подойдёт {item_name} ({price} ₸). Добавить?",
            sort_order=100,
            is_active=bool(body.is_active),
        )
        db.add(row)
        await db.flush()
        created: dict[str, Any] = {"kind": "upsell_rule", "rule": _upsell_rule_dict(row)}
    else:
        if not item_iiko_id and not item_name:
            raise HTTPException(status_code=400, detail="item_iiko_id or item_name is required")
        q = select(MenuItem).where(MenuItem.organization_id == org_id)
        if item_iiko_id:
            q = q.where(MenuItem.iiko_id == item_iiko_id)
        else:
            q = q.where(func.lower(MenuItem.name) == item_name.lower())
        menu_item = (await db.execute(q.limit(1))).scalar_one_or_none()
        if menu_item is None:
            raise HTTPException(status_code=404, detail="Menu item not found for anti-rule")
        tags = [t.strip() for t in (menu_item.tags or "").split(",") if t.strip()]
        if "not_upsell" not in {t.lower() for t in tags}:
            tags.append("not_upsell")
        menu_item.tags = ", ".join(tags)
        created = {"kind": "anti_rule", "menu_item_id": int(menu_item.id), "tags": menu_item.tags}

    ij = dict(items_json)
    om = dict(ij.get("order_meta") or {}) if isinstance(ij.get("order_meta"), dict) else {}
    audit = list(om.get("upsell_feedback_audit") or []) if isinstance(om.get("upsell_feedback_audit"), list) else []
    audit.append({
        "mode": body.mode,
        "at": datetime.now(timezone.utc).isoformat(),
        "actor": admin_actor_key(request),
        "result": created,
    })
    om["upsell_feedback_audit"] = audit[-25:]
    ij["order_meta"] = om
    order.items_json = ij
    await db.commit()
    return {"ok": True, "feedback": created}


# ─── Packaging rules ──────────────────────────────────────────────────────────


@router.get("/packaging-rules")
async def list_packaging_rules(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    """Правила упаковки арендатора (включая неактивные). При отсутствии — сиды для организации."""
    org_id = admin_org_from_session(request)
    result = await db.execute(
        select(PackagingRule)
        .where(_packaging_tenant_clause(org_id))
        .order_by(PackagingRule.sort_order.desc(), PackagingRule.id)
    )
    rows = list(result.scalars().all())
    if not rows:
        for seed in PACKAGING_SEED:
            db.add(PackagingRule(**{**seed, "organization_id": org_id}))
        await db.flush()
        result2 = await db.execute(
            select(PackagingRule)
            .where(_packaging_tenant_clause(org_id))
            .order_by(PackagingRule.sort_order.desc(), PackagingRule.id)
        )
        rows = list(result2.scalars().all())
    return {"items": [_packaging_rule_dict(r) for r in rows]}


@router.post("/packaging-rules/preview")
async def preview_packaging_rules(
    request: Request,
    body: PackagingPreviewBody,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    «Живой калькулятор» для вкладки упаковки: считает fee_lines тем же кодом, что и прод,
    чтобы админ видел понятную математику без дублирования логики в JS.
    """
    org_id = admin_org_from_session(request)

    menu_item = await _menu_item_in_org(db, int(body.menu_item_id), org_id)

    qty = int(body.quantity)
    price = float(menu_item.price or 0.0)
    foods_subtotal = round(price * qty, 2)
    foods = [{
        "name": menu_item.name,
        "category": menu_item.category or "",
        "quantity": qty,
        "price_per_unit": price,
        "item_total": foods_subtotal,
        "packaging_plov_1kg": body.packaging_plov_1kg,
    }]

    rules = await load_packaging_rules(db, org_id)
    fee_lines, extras = compute_fee_lines(foods, foods_subtotal, body.order_type, packaging_rules=rules)
    grand_total = round(foods_subtotal + float(extras), 2)

    return {
        "ok": True,
        "input": {
            "order_type": body.order_type,
            "quantity": qty,
            "menu_item": _menu_item_dict(menu_item),
        },
        "foods_subtotal": foods_subtotal,
        "fee_lines": fee_lines,
        "extras_total": round(float(extras), 2),
        "grand_total": grand_total,
    }


@router.post("/packaging-rules")
async def create_packaging_rule(
    request: Request,
    body: PackagingRuleCreateBody,
    db: AsyncSession = Depends(get_db),
) -> dict:
    org_id = admin_org_from_session(request)
    k = body.kind.strip()
    existing = await db.scalar(
        select(PackagingRule).where(
            PackagingRule.organization_id == org_id,
            PackagingRule.kind == k,
        ),
    )
    if existing:
        raise HTTPException(status_code=409, detail=f"Правило с kind='{k}' уже есть у этой организации")
    row = PackagingRule(
        organization_id=org_id,
        kind=k,
        name=body.name.strip(),
        price=body.price,
        iiko_product_id=(body.iiko_product_id or "").strip() or None,
        keywords=(body.keywords or "").strip(),
        option_key=(body.option_key or "").strip(),
        scope=body.scope,
        category_match=(body.category_match or "").strip(),
        is_active=body.is_active,
        sort_order=body.sort_order,
    )
    db.add(row)
    await db.flush()
    return {"ok": True, "item": _packaging_rule_dict(row)}


@router.patch("/packaging-rules/{rule_id}")
async def patch_packaging_rule(
    request: Request,
    rule_id: int,
    body: PackagingRulePatchBody,
    db: AsyncSession = Depends(get_db),
) -> dict:
    org_id = admin_org_from_session(request)
    row = await db.get(PackagingRule, rule_id)
    if row is None or int(row.organization_id) != org_id:
        raise HTTPException(status_code=404, detail="Правило не найдено")
    data = body.model_dump(exclude_unset=True)
    for key in ("kind", "name", "keywords", "option_key", "category_match"):
        if key in data and data[key] is not None:
            data[key] = data[key].strip()
    if "iiko_product_id" in data:
        data["iiko_product_id"] = (data["iiko_product_id"] or "").strip() or None
    if "kind" in data and data["kind"] is not None:
        dup = await db.scalar(
            select(PackagingRule).where(
                PackagingRule.organization_id == org_id,
                PackagingRule.kind == data["kind"],
                PackagingRule.id != row.id,
            ),
        )
        if dup:
            raise HTTPException(status_code=409, detail="Такой kind уже занят у организации")
    for key, value in data.items():
        setattr(row, key, value)
    await db.flush()
    return {"ok": True, "item": _packaging_rule_dict(row)}


@router.delete("/packaging-rules/{rule_id}")
async def delete_packaging_rule(
    request: Request,
    rule_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict:
    org_id = admin_org_from_session(request)
    row = await db.get(PackagingRule, rule_id)
    if row is None or int(row.organization_id) != org_id:
        raise HTTPException(status_code=404, detail="Правило не найдено")
    await db.delete(row)
    await db.flush()
    return {"ok": True}
