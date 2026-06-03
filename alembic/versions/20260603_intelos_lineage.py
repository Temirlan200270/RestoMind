"""Intelligence OS sales lineage fields

Revision ID: 20260603_intelos_lineage
Revises: 20260603_intelos_trust_layers
Create Date: 2026-06-03
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260603_intelos_lineage"
down_revision: Union[str, None] = "20260603_intelos_trust_layers"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("sales_fact_orders", sa.Column("snapshot_id", sa.Integer(), nullable=True))
    op.add_column("sales_fact_orders", sa.Column("canonical_order_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_sales_fact_orders_snapshot_id",
        "sales_fact_orders",
        "source_data_snapshots",
        ["snapshot_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_sales_fact_orders_canonical_order_id",
        "sales_fact_orders",
        "canonical_sales_orders",
        ["canonical_order_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_sales_fact_orders_snapshot_id", "sales_fact_orders", ["snapshot_id"])
    op.create_index("ix_sales_fact_orders_canonical_order_id", "sales_fact_orders", ["canonical_order_id"])

    op.add_column("sales_fact_items", sa.Column("snapshot_id", sa.Integer(), nullable=True))
    op.add_column("sales_fact_items", sa.Column("canonical_item_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_sales_fact_items_snapshot_id",
        "sales_fact_items",
        "source_data_snapshots",
        ["snapshot_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_sales_fact_items_canonical_item_id",
        "sales_fact_items",
        "canonical_sales_items",
        ["canonical_item_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_sales_fact_items_snapshot_id", "sales_fact_items", ["snapshot_id"])
    op.create_index("ix_sales_fact_items_canonical_item_id", "sales_fact_items", ["canonical_item_id"])


def downgrade() -> None:
    op.drop_index("ix_sales_fact_items_canonical_item_id", table_name="sales_fact_items")
    op.drop_index("ix_sales_fact_items_snapshot_id", table_name="sales_fact_items")
    op.drop_constraint("fk_sales_fact_items_canonical_item_id", "sales_fact_items", type_="foreignkey")
    op.drop_constraint("fk_sales_fact_items_snapshot_id", "sales_fact_items", type_="foreignkey")
    op.drop_column("sales_fact_items", "canonical_item_id")
    op.drop_column("sales_fact_items", "snapshot_id")
    op.drop_index("ix_sales_fact_orders_canonical_order_id", table_name="sales_fact_orders")
    op.drop_index("ix_sales_fact_orders_snapshot_id", table_name="sales_fact_orders")
    op.drop_constraint("fk_sales_fact_orders_canonical_order_id", "sales_fact_orders", type_="foreignkey")
    op.drop_constraint("fk_sales_fact_orders_snapshot_id", "sales_fact_orders", type_="foreignkey")
    op.drop_column("sales_fact_orders", "canonical_order_id")
    op.drop_column("sales_fact_orders", "snapshot_id")
