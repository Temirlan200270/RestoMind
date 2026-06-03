"""Intelligence OS trust, memory and graph layers

Revision ID: 20260603_intelos_trust_layers
Revises: 20260603_intelos_sales_copilot
Create Date: 2026-06-03
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260603_intelos_trust_layers"
down_revision: Union[str, None] = "20260603_intelos_sales_copilot"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _add_column(table: str, column: sa.Column) -> None:
    op.add_column(table, column)


def upgrade() -> None:
    _add_column("operational_insights", sa.Column("confidence_score", sa.Numeric(5, 4), nullable=True))
    _add_column("operational_insights", sa.Column("evidence_json", sa.JSON(), nullable=True))
    _add_column("operational_insights", sa.Column("drilldown_json", sa.JSON(), nullable=True))

    _add_column("recommendation_outcomes", sa.Column("action_id", sa.String(length=120), nullable=True))
    _add_column("recommendation_outcomes", sa.Column("baseline_window_json", sa.JSON(), nullable=True))
    _add_column("recommendation_outcomes", sa.Column("measurement_window_json", sa.JSON(), nullable=True))
    _add_column("recommendation_outcomes", sa.Column("data_quality_confidence", sa.Numeric(5, 4), nullable=True))
    op.create_index("ix_recommendation_outcomes_action_id", "recommendation_outcomes", ["action_id"])

    op.create_table(
        "source_data_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source", sa.String(length=40), server_default="iiko_olap", nullable=False),
        sa.Column("entity_type", sa.String(length=80), server_default="sales", nullable=False),
        sa.Column("date_from", sa.Date(), nullable=True),
        sa.Column("date_to", sa.Date(), nullable=True),
        sa.Column("row_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("checksum", sa.String(length=128), server_default="", nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
    )
    op.create_index("ix_source_data_snapshots_org_created", "source_data_snapshots", ["organization_id", "created_at"])
    op.create_index("ix_source_data_snapshots_org_source_entity", "source_data_snapshots", ["organization_id", "source", "entity_type"])

    op.create_table(
        "canonical_products",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source", sa.String(length=40), server_default="iiko_olap", nullable=False),
        sa.Column("source_product_id", sa.String(length=160), nullable=False),
        sa.Column("name", sa.String(length=255), server_default="", nullable=False),
        sa.Column("category", sa.String(length=180), nullable=True),
        sa.Column("confidence_score", sa.Numeric(5, 4), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.UniqueConstraint("organization_id", "source", "source_product_id", name="uq_canonical_products_org_source_id"),
    )
    op.create_index("ix_canonical_products_org_name", "canonical_products", ["organization_id", "name"])

    op.create_table(
        "canonical_sales_orders",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("snapshot_id", sa.Integer(), sa.ForeignKey("source_data_snapshots.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source", sa.String(length=40), server_default="iiko_olap", nullable=False),
        sa.Column("source_order_id", sa.String(length=160), nullable=False),
        sa.Column("order_date", sa.Date(), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revenue", sa.Numeric(14, 2), server_default="0", nullable=False),
        sa.Column("guest_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("waiter_name", sa.String(length=240), nullable=True),
        sa.Column("order_type", sa.String(length=120), nullable=True),
        sa.Column("origin_name", sa.String(length=120), nullable=True),
        sa.Column("confidence_score", sa.Numeric(5, 4), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.UniqueConstraint("organization_id", "source", "source_order_id", name="uq_canonical_sales_orders_org_source_id"),
    )
    op.create_index("ix_canonical_sales_orders_org_date", "canonical_sales_orders", ["organization_id", "order_date"])
    op.create_index("ix_canonical_sales_orders_snapshot_id", "canonical_sales_orders", ["snapshot_id"])

    op.create_table(
        "canonical_sales_items",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("snapshot_id", sa.Integer(), sa.ForeignKey("source_data_snapshots.id", ondelete="SET NULL"), nullable=True),
        sa.Column("canonical_order_id", sa.Integer(), sa.ForeignKey("canonical_sales_orders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source", sa.String(length=40), server_default="iiko_olap", nullable=False),
        sa.Column("source_product_id", sa.String(length=160), nullable=True),
        sa.Column("product_name", sa.String(length=255), server_default="", nullable=False),
        sa.Column("category", sa.String(length=180), nullable=True),
        sa.Column("quantity", sa.Numeric(14, 3), server_default="0", nullable=False),
        sa.Column("revenue", sa.Numeric(14, 2), server_default="0", nullable=False),
        sa.Column("cost", sa.Numeric(14, 2), nullable=True),
        sa.Column("confidence_score", sa.Numeric(5, 4), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=True),
    )
    op.create_index("ix_canonical_sales_items_org_product", "canonical_sales_items", ["organization_id", "source_product_id"])
    op.create_index("ix_canonical_sales_items_order", "canonical_sales_items", ["canonical_order_id"])

    op.create_table(
        "data_quality_reports",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("snapshot_id", sa.Integer(), sa.ForeignKey("source_data_snapshots.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source", sa.String(length=40), server_default="iiko_olap", nullable=False),
        sa.Column("entity_type", sa.String(length=80), server_default="sales", nullable=False),
        sa.Column("status", sa.String(length=24), server_default="ok", nullable=False),
        sa.Column("confidence_score", sa.Numeric(5, 4), server_default="1", nullable=False),
        sa.Column("row_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("issue_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("required_missing", sa.Integer(), server_default="0", nullable=False),
        sa.Column("duplicate_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("invalid_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
    )
    op.create_index("ix_data_quality_reports_org_created", "data_quality_reports", ["organization_id", "created_at"])
    op.create_index("ix_data_quality_reports_org_source_entity", "data_quality_reports", ["organization_id", "source", "entity_type"])

    op.create_table(
        "insight_deliveries",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("insight_id", sa.Integer(), sa.ForeignKey("operational_insights.id", ondelete="CASCADE"), nullable=False),
        sa.Column("channel", sa.String(length=40), server_default="telegram_owner", nullable=False),
        sa.Column("status", sa.String(length=24), server_default="pending", nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=True),
        sa.Column("error_text", sa.String(length=500), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("action_taken_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.UniqueConstraint("organization_id", "insight_id", "channel", name="uq_insight_deliveries_org_insight_channel"),
    )
    op.create_index("ix_insight_deliveries_org_status", "insight_deliveries", ["organization_id", "status"])
    op.create_index("ix_insight_deliveries_org_sent", "insight_deliveries", ["organization_id", "sent_at"])

    op.create_table(
        "organization_memory_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_date", sa.Date(), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("entity_type", sa.String(length=80), nullable=True),
        sa.Column("entity_id", sa.String(length=120), nullable=True),
        sa.Column("summary", sa.Text(), server_default="", nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column("source", sa.String(length=80), server_default="system", nullable=False),
        sa.Column("confidence_score", sa.Numeric(5, 4), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
    )
    op.create_index("ix_org_memory_org_date", "organization_memory_events", ["organization_id", "event_date"])
    op.create_index("ix_org_memory_org_type", "organization_memory_events", ["organization_id", "event_type"])
    op.create_index("ix_org_memory_org_entity", "organization_memory_events", ["organization_id", "entity_type", "entity_id"])

    op.create_table(
        "dish_ingredients",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("dish_product_id", sa.String(length=160), nullable=False),
        sa.Column("dish_name", sa.String(length=255), server_default="", nullable=False),
        sa.Column("ingredient_sku", sa.String(length=160), nullable=False),
        sa.Column("ingredient_name", sa.String(length=255), server_default="", nullable=False),
        sa.Column("quantity", sa.Numeric(14, 4), server_default="0", nullable=False),
        sa.Column("unit", sa.String(length=32), server_default="", nullable=False),
        sa.Column("unit_cost", sa.Numeric(14, 4), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.UniqueConstraint("organization_id", "dish_product_id", "ingredient_sku", name="uq_dish_ingredients_org_dish_sku"),
    )
    op.create_index("ix_dish_ingredients_org_dish", "dish_ingredients", ["organization_id", "dish_product_id"])
    op.create_index("ix_dish_ingredients_org_sku", "dish_ingredients", ["organization_id", "ingredient_sku"])

    op.create_table(
        "ingredient_suppliers",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ingredient_sku", sa.String(length=160), nullable=False),
        sa.Column("ingredient_name", sa.String(length=255), server_default="", nullable=False),
        sa.Column("supplier_name", sa.String(length=255), server_default="", nullable=False),
        sa.Column("supplier_external_id", sa.String(length=160), nullable=True),
        sa.Column("lead_time_days", sa.Integer(), nullable=True),
        sa.Column("risk_score", sa.Numeric(5, 4), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.UniqueConstraint("organization_id", "ingredient_sku", "supplier_name", name="uq_ingredient_suppliers_org_sku_supplier"),
    )
    op.create_index("ix_ingredient_suppliers_org_supplier", "ingredient_suppliers", ["organization_id", "supplier_name"])

    op.create_table(
        "dish_margin_profile",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("dish_product_id", sa.String(length=160), nullable=False),
        sa.Column("dish_name", sa.String(length=255), server_default="", nullable=False),
        sa.Column("category", sa.String(length=180), nullable=True),
        sa.Column("avg_price", sa.Numeric(14, 2), nullable=True),
        sa.Column("estimated_cost", sa.Numeric(14, 2), nullable=True),
        sa.Column("margin_pct", sa.Numeric(10, 4), nullable=True),
        sa.Column("revenue_30d", sa.Numeric(14, 2), nullable=True),
        sa.Column("confidence_score", sa.Numeric(5, 4), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.UniqueConstraint("organization_id", "dish_product_id", name="uq_dish_margin_profile_org_dish"),
    )
    op.create_index("ix_dish_margin_profile_org_margin", "dish_margin_profile", ["organization_id", "margin_pct"])

    op.create_table(
        "dish_seasonality_profile",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("entity_type", sa.String(length=40), server_default="dish", nullable=False),
        sa.Column("entity_id", sa.String(length=160), nullable=False),
        sa.Column("entity_name", sa.String(length=255), server_default="", nullable=False),
        sa.Column("period_key", sa.String(length=40), nullable=False),
        sa.Column("expected_quantity", sa.Numeric(14, 3), nullable=True),
        sa.Column("expected_revenue", sa.Numeric(14, 2), nullable=True),
        sa.Column("confidence_score", sa.Numeric(5, 4), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.UniqueConstraint("organization_id", "entity_type", "entity_id", "period_key", name="uq_dish_seasonality_org_entity_period"),
    )
    op.create_index("ix_dish_seasonality_org_entity", "dish_seasonality_profile", ["organization_id", "entity_type", "entity_id"])

    op.create_table(
        "dish_substitution_links",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_product_id", sa.String(length=160), nullable=False),
        sa.Column("target_product_id", sa.String(length=160), nullable=False),
        sa.Column("relation_type", sa.String(length=40), server_default="substitute", nullable=False),
        sa.Column("strength", sa.Numeric(5, 4), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.UniqueConstraint("organization_id", "source_product_id", "target_product_id", name="uq_dish_substitution_org_pair"),
    )
    op.create_index("ix_dish_substitution_org_source", "dish_substitution_links", ["organization_id", "source_product_id"])


def downgrade() -> None:
    for table in (
        "dish_substitution_links",
        "dish_seasonality_profile",
        "dish_margin_profile",
        "ingredient_suppliers",
        "dish_ingredients",
        "organization_memory_events",
        "insight_deliveries",
        "data_quality_reports",
        "canonical_sales_items",
        "canonical_sales_orders",
        "canonical_products",
        "source_data_snapshots",
    ):
        op.drop_table(table)
    op.drop_index("ix_recommendation_outcomes_action_id", table_name="recommendation_outcomes")
    for column in ("data_quality_confidence", "measurement_window_json", "baseline_window_json", "action_id"):
        op.drop_column("recommendation_outcomes", column)
    for column in ("drilldown_json", "evidence_json", "confidence_score"):
        op.drop_column("operational_insights", column)
