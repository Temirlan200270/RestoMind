"""Create upsell rule command."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Order, UpsellRule
from app.services.agent_commands.base import AgentCommandSpec, CommandContext, command_public


class UpsellRuleCreateCommand:
    spec = AgentCommandSpec(
        action_type="upsell_rule_create",
        command_name="CreateUpsellRuleCommand",
        risk_level="medium",
        required_role="manager",
        requires_preview=True,
    )

    def validate(self, payload: dict[str, Any] | None) -> dict[str, Any]:
        data = dict(payload or {})
        trigger_category = str(data.get("trigger_category") or "").strip()
        suggest_category = str(data.get("suggest_category") or "").strip()
        if not trigger_category or not suggest_category:
            raise ValueError("upsell_categories_required")
        data["trigger_category"] = trigger_category[:120]
        data["suggest_category"] = suggest_category[:120]
        data["trigger_mode"] = str(data.get("trigger_mode") or "missing_category").strip()[:40]
        if data.get("phrase_template") is not None:
            data["phrase_template"] = str(data.get("phrase_template") or "").strip()[:1000]
        data["_command"] = command_public(self.spec)
        return data

    async def preview(self, db: AsyncSession, ctx: CommandContext) -> dict[str, Any]:
        trigger = str(ctx.payload.get("trigger_category") or "")
        suggest = str(ctx.payload.get("suggest_category") or "")
        existing = (
            await db.execute(
                select(UpsellRule).where(
                    UpsellRule.organization_id == ctx.organization_id,
                    UpsellRule.trigger_category == trigger,
                    UpsellRule.suggest_category == suggest,
                    UpsellRule.is_active.is_(True),
                ).limit(1),
            )
        ).scalar_one_or_none()
        recent_orders = int(
            await db.scalar(
                select(func.count(Order.id)).where(
                    Order.organization_id == ctx.organization_id,
                    Order.status.notin_(["cancelled", "draft"]),
                ),
            )
            or 0,
        )
        return {
            "action": "upsell_rule_create",
            "trigger_category": trigger,
            "suggest_category": suggest,
            "trigger_mode": ctx.payload.get("trigger_mode") or "missing_category",
            "duplicate_rule_exists": existing is not None,
            "estimated_scope": {
                "recent_orders_pool": recent_orders,
                "note": "Правило сработает при заказах без категории «{trigger}», предложит «{suggest}».".format(
                    trigger=trigger,
                    suggest=suggest,
                ),
            },
            "risk": "medium",
        }

    async def apply(self, db: AsyncSession, ctx: CommandContext) -> dict[str, Any]:
        trigger_category = str(ctx.payload.get("trigger_category") or "").strip()
        suggest_category = str(ctx.payload.get("suggest_category") or "").strip()
        phrase = str(ctx.payload.get("phrase_template") or "").strip() or (
            "К заказу отлично подойдёт {item_name} ({price} ₸). Добавить?"
        )
        row = UpsellRule(
            organization_id=ctx.organization_id,
            trigger_mode=str(ctx.payload.get("trigger_mode") or "missing_category").strip(),
            trigger_category=trigger_category,
            suggest_category=suggest_category,
            min_order_sum=float(ctx.payload.get("min_order_sum") or 0),
            max_order_sum=ctx.payload.get("max_order_sum"),
            phrase_template=phrase,
            sort_order=int(ctx.payload.get("sort_order") or 0),
            is_active=bool(ctx.payload.get("is_active", True)),
        )
        db.add(row)
        await db.flush()
        return {
            "upsell_rule_id": row.id,
            "trigger_category": trigger_category,
            "suggest_category": suggest_category,
        }

    def audit_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "trigger_category": payload.get("trigger_category"),
            "suggest_category": payload.get("suggest_category"),
            "trigger_mode": payload.get("trigger_mode"),
        }


upsell_rule_create_command = UpsellRuleCreateCommand()
