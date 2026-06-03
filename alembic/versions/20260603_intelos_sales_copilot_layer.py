"""Intelligence OS sales fact and copilot foundation

Revision ID: 20260603_intelos_sales_copilot
Revises: 20260604_telegram_org_mapping
Create Date: 2026-06-03
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260603_intelos_sales_copilot"
down_revision: Union[str, None] = "20260604_telegram_org_mapping"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("organizations", sa.Column("iiko_data_source", sa.String(length=16), server_default="cloud", nullable=False))
    op.add_column("organizations", sa.Column("iiko_server_host", sa.String(length=255), server_default="", nullable=False))
    op.add_column("organizations", sa.Column("iiko_server_port", sa.Integer(), server_default="443", nullable=False))
    op.add_column("organizations", sa.Column("iiko_server_login", sa.String(length=255), server_default="", nullable=False))
    op.add_column("organizations", sa.Column("iiko_server_password_enc", sa.Text(), nullable=True))
    op.add_column("organizations", sa.Column("iiko_server_department_id", sa.String(length=255), server_default="", nullable=False))

    op.create_table(
        "sales_fact_orders",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("location_id", sa.Integer(), sa.ForeignKey("locations.id", ondelete="SET NULL"), nullable=True),
        sa.Column("iiko_order_id", sa.String(length=160), nullable=False),
        sa.Column("order_date", sa.Date(), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revenue", sa.Numeric(14, 2), server_default="0", nullable=False),
        sa.Column("guest_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("waiter_name", sa.String(length=240), nullable=True),
        sa.Column("order_type", sa.String(length=120), nullable=True),
        sa.Column("source", sa.String(length=120), nullable=True),
        sa.Column("data_source", sa.String(length=32), server_default="iiko_olap", nullable=False),
        sa.Column("raw_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.UniqueConstraint("organization_id", "iiko_order_id", name="uq_sales_fact_orders_org_iiko_order"),
    )
    op.create_index("ix_sales_fact_orders_org_date", "sales_fact_orders", ["organization_id", "order_date"])
    op.create_index("ix_sales_fact_orders_org_closed", "sales_fact_orders", ["organization_id", "closed_at"])
    op.create_index("ix_sales_fact_orders_organization_id", "sales_fact_orders", ["organization_id"])
    op.create_index("ix_sales_fact_orders_location_id", "sales_fact_orders", ["location_id"])

    op.create_table(
        "sales_fact_items",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("sales_fact_orders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_id", sa.String(length=160), nullable=True),
        sa.Column("product_name", sa.String(length=255), server_default="", nullable=False),
        sa.Column("category", sa.String(length=180), nullable=True),
        sa.Column("quantity", sa.Numeric(14, 3), server_default="0", nullable=False),
        sa.Column("revenue", sa.Numeric(14, 2), server_default="0", nullable=False),
        sa.Column("cost", sa.Numeric(14, 2), nullable=True),
        sa.Column("raw_json", sa.JSON(), nullable=True),
    )
    op.create_index("ix_sales_fact_items_org_product", "sales_fact_items", ["organization_id", "product_id"])
    op.create_index("ix_sales_fact_items_order", "sales_fact_items", ["order_id"])
    op.create_index("ix_sales_fact_items_organization_id", "sales_fact_items", ["organization_id"])

    op.create_table(
        "sales_daily_agg",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("location_id", sa.Integer(), sa.ForeignKey("locations.id", ondelete="SET NULL"), nullable=True),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("source", sa.String(length=32), server_default="iiko_olap", nullable=False),
        sa.Column("total_revenue", sa.Numeric(14, 2), server_default="0", nullable=False),
        sa.Column("order_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("guest_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("avg_check", sa.Numeric(14, 2), server_default="0", nullable=False),
        sa.Column("baseline_revenue", sa.Numeric(14, 2), nullable=True),
        sa.Column("delta_pct", sa.Numeric(10, 4), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.UniqueConstraint("organization_id", "date", "source", name="uq_sales_daily_agg_org_date_source"),
    )
    op.create_index("ix_sales_daily_agg_org_date", "sales_daily_agg", ["organization_id", "date"])
    op.create_index("ix_sales_daily_agg_organization_id", "sales_daily_agg", ["organization_id"])
    op.create_index("ix_sales_daily_agg_location_id", "sales_daily_agg", ["location_id"])

    op.create_table(
        "recommendation_outcomes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("insight_id", sa.Integer(), sa.ForeignKey("operational_insights.id", ondelete="SET NULL"), nullable=True),
        sa.Column("recommendation_type", sa.String(length=80), server_default="", nullable=False),
        sa.Column("status", sa.String(length=24), server_default="proposed", nullable=False),
        sa.Column("metric", sa.String(length=80), server_default="", nullable=False),
        sa.Column("baseline_value", sa.Numeric(14, 2), nullable=True),
        sa.Column("target_value", sa.Numeric(14, 2), nullable=True),
        sa.Column("realized_delta", sa.Numeric(14, 2), nullable=True),
        sa.Column("realized_money", sa.Numeric(14, 2), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("measure_after", sa.DateTime(timezone=True), nullable=True),
        sa.Column("measured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
    )
    op.create_index("ix_recommendation_outcomes_org_status", "recommendation_outcomes", ["organization_id", "status"])
    op.create_index("ix_recommendation_outcomes_org_due", "recommendation_outcomes", ["organization_id", "measure_after"])
    op.create_index("ix_recommendation_outcomes_organization_id", "recommendation_outcomes", ["organization_id"])
    op.create_index("ix_recommendation_outcomes_insight_id", "recommendation_outcomes", ["insight_id"])


def downgrade() -> None:
    op.drop_index("ix_recommendation_outcomes_insight_id", table_name="recommendation_outcomes")
    op.drop_index("ix_recommendation_outcomes_organization_id", table_name="recommendation_outcomes")
    op.drop_index("ix_recommendation_outcomes_org_due", table_name="recommendation_outcomes")
    op.drop_index("ix_recommendation_outcomes_org_status", table_name="recommendation_outcomes")
    op.drop_table("recommendation_outcomes")

    op.drop_index("ix_sales_daily_agg_location_id", table_name="sales_daily_agg")
    op.drop_index("ix_sales_daily_agg_organization_id", table_name="sales_daily_agg")
    op.drop_index("ix_sales_daily_agg_org_date", table_name="sales_daily_agg")
    op.drop_table("sales_daily_agg")

    op.drop_index("ix_sales_fact_items_organization_id", table_name="sales_fact_items")
    op.drop_index("ix_sales_fact_items_order", table_name="sales_fact_items")
    op.drop_index("ix_sales_fact_items_org_product", table_name="sales_fact_items")
    op.drop_table("sales_fact_items")

    op.drop_index("ix_sales_fact_orders_location_id", table_name="sales_fact_orders")
    op.drop_index("ix_sales_fact_orders_organization_id", table_name="sales_fact_orders")
    op.drop_index("ix_sales_fact_orders_org_closed", table_name="sales_fact_orders")
    op.drop_index("ix_sales_fact_orders_org_date", table_name="sales_fact_orders")
    op.drop_table("sales_fact_orders")

    op.drop_column("organizations", "iiko_server_department_id")
    op.drop_column("organizations", "iiko_server_password_enc")
    op.drop_column("organizations", "iiko_server_login")
    op.drop_column("organizations", "iiko_server_port")
    op.drop_column("organizations", "iiko_server_host")
    op.drop_column("organizations", "iiko_data_source")
