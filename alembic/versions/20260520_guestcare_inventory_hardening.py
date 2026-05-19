"""Add GuestCare external reviews and inventory stock snapshots

Revision ID: 20260520_guestcare_inventory
Revises: 20260520_org_meta_json
Create Date: 2026-05-20 00:20:00.000000
"""

from __future__ import annotations

from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260520_guestcare_inventory"
down_revision: Union[str, None] = "20260520_org_meta_json"
branch_labels: Union[str, tuple[str, ...], None] = None
depends_on: Union[str, tuple[str, ...], None] = None


def upgrade() -> None:
    op.create_table(
        "external_reviews",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=40), server_default="external", nullable=False),
        sa.Column("external_id", sa.String(length=120), nullable=False),
        sa.Column("url", sa.String(length=500), nullable=False),
        sa.Column("author", sa.String(length=160), server_default="", nullable=False),
        sa.Column("rating", sa.Integer(), nullable=True),
        sa.Column("text", sa.Text(), server_default="", nullable=False),
        sa.Column("status", sa.String(length=24), server_default="new", nullable=False),
        sa.Column("reply_draft", sa.Text(), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column("imported_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "source", "external_id", name="uq_external_reviews_org_source_id"),
    )
    op.create_index("ix_external_reviews_org_imported", "external_reviews", ["organization_id", "imported_at"])
    op.create_index("ix_external_reviews_org_status", "external_reviews", ["organization_id", "status"])
    op.create_index(op.f("ix_external_reviews_organization_id"), "external_reviews", ["organization_id"])
    op.create_index(op.f("ix_external_reviews_status"), "external_reviews", ["status"])

    op.create_table(
        "inventory_stock_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("location_id", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(length=40), server_default="manual", nullable=False),
        sa.Column("sku", sa.String(length=120), nullable=False),
        sa.Column("ingredient", sa.String(length=240), nullable=False),
        sa.Column("unit", sa.String(length=32), server_default="", nullable=False),
        sa.Column("quantity", sa.Numeric(14, 3), server_default="0", nullable=False),
        sa.Column("min_quantity", sa.Numeric(14, 3), nullable=True),
        sa.Column("reorder_quantity", sa.Numeric(14, 3), nullable=True),
        sa.Column("daily_usage_estimate", sa.Numeric(14, 3), nullable=True),
        sa.Column("external_id", sa.String(length=160), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["location_id"], ["locations.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "location_id",
            "source",
            "sku",
            name="uq_inventory_stock_org_location_source_sku",
        ),
    )
    op.create_index("ix_inventory_stock_org_location", "inventory_stock_snapshots", ["organization_id", "location_id"])
    op.create_index("ix_inventory_stock_org_updated", "inventory_stock_snapshots", ["organization_id", "updated_at"])
    op.create_index(op.f("ix_inventory_stock_snapshots_location_id"), "inventory_stock_snapshots", ["location_id"])
    op.create_index(op.f("ix_inventory_stock_snapshots_organization_id"), "inventory_stock_snapshots", ["organization_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_inventory_stock_snapshots_organization_id"), table_name="inventory_stock_snapshots")
    op.drop_index(op.f("ix_inventory_stock_snapshots_location_id"), table_name="inventory_stock_snapshots")
    op.drop_index("ix_inventory_stock_org_updated", table_name="inventory_stock_snapshots")
    op.drop_index("ix_inventory_stock_org_location", table_name="inventory_stock_snapshots")
    op.drop_table("inventory_stock_snapshots")

    op.drop_index(op.f("ix_external_reviews_status"), table_name="external_reviews")
    op.drop_index(op.f("ix_external_reviews_organization_id"), table_name="external_reviews")
    op.drop_index("ix_external_reviews_org_status", table_name="external_reviews")
    op.drop_index("ix_external_reviews_org_imported", table_name="external_reviews")
    op.drop_table("external_reviews")
