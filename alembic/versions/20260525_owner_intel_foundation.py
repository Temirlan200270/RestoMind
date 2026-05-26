"""Owner Intelligence foundation tables

Revision ID: 20260525_owner_intel_foundation
Revises: 20260525_daily_stats_ops_events
Create Date: 2026-05-25
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260525_owner_intel_foundation"
down_revision: Union[str, None] = "20260525_daily_stats_ops_events"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_order_audits",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("location_id", sa.Integer(), nullable=True),
        sa.Column("order_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("trace_id", sa.String(length=120), nullable=True),
        sa.Column("risk_score", sa.Integer(), server_default="0", nullable=False),
        sa.Column("risk_level", sa.String(length=16), server_default="low", nullable=False),
        sa.Column("tags_json", sa.JSON(), nullable=True),
        sa.Column("reasons_json", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="open", nullable=False),
        sa.Column("prevented_value", sa.Numeric(14, 2), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by_staff_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["location_id"], ["locations.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reviewed_by_staff_id"], ["staff_users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_order_audits_org_status", "ai_order_audits", ["organization_id", "status"])
    op.create_index("ix_ai_order_audits_org_created", "ai_order_audits", ["organization_id", "created_at"])

    op.create_table(
        "upsell_offer_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("location_id", sa.Integer(), nullable=True),
        sa.Column("order_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("chat_log_id", sa.Integer(), nullable=True),
        sa.Column("source_rule_id", sa.Integer(), nullable=True),
        sa.Column("base_item_id", sa.String(length=100), nullable=True),
        sa.Column("offered_item_id", sa.String(length=100), nullable=True),
        sa.Column("base_item_name", sa.String(length=255), server_default="", nullable=False),
        sa.Column("offered_item_name", sa.String(length=255), server_default="", nullable=False),
        sa.Column("variant", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="shown", nullable=False),
        sa.Column("offered_price", sa.Numeric(14, 2), server_default="0", nullable=False),
        sa.Column("added_revenue", sa.Numeric(14, 2), server_default="0", nullable=False),
        sa.Column("meta_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["chat_log_id"], ["chat_logs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["location_id"], ["locations.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_rule_id"], ["upsell_rules.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_upsell_offer_events_org_status", "upsell_offer_events", ["organization_id", "status"])
    op.create_index("ix_upsell_offer_events_org_created", "upsell_offer_events", ["organization_id", "created_at"])

    op.create_table(
        "operational_mode_states",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("location_id", sa.Integer(), nullable=True),
        sa.Column("kitchen_load", sa.String(length=16), server_default="normal", nullable=False),
        sa.Column("prep_time_extra_min", sa.Integer(), server_default="0", nullable=False),
        sa.Column("delivery_mode", sa.String(length=16), server_default="normal", nullable=False),
        sa.Column("force_pickup_only", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_by_staff_id", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["location_id"], ["locations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["updated_by_staff_id"], ["staff_users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "location_id", name="uq_operational_mode_org_location"),
    )

    op.add_column(
        "menu_items",
        sa.Column("cost_price", sa.Numeric(10, 2), nullable=True, comment="Себестоимость для Menu Profit Lab"),
    )


def downgrade() -> None:
    op.drop_column("menu_items", "cost_price")
    op.drop_table("operational_mode_states")
    op.drop_table("upsell_offer_events")
    op.drop_table("ai_order_audits")
