"""Money Layer: recovered metrics + sales_hourly_daily (iiko ETL)

Revision ID: 20260524_money_layer_recovered
Revises: 20260523_p3_waiter_kpi
Create Date: 2026-05-24
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260524_money_layer_recovered"
down_revision: Union[str, None] = "20260523_p3_waiter_kpi"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "daily_org_stats",
        sa.Column("recovered_kzt", sa.Numeric(14, 2), nullable=False, server_default="0"),
    )
    op.add_column(
        "daily_org_stats",
        sa.Column("focus_completed_count", sa.Integer(), nullable=False, server_default="0"),
    )

    op.create_table(
        "sales_hourly_daily",
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("hour", sa.SmallInteger(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="order"),
        sa.Column("location_id", sa.Integer(), nullable=True),
        sa.Column("orders_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("revenue_kzt", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["location_id"], ["locations.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("organization_id", "day", "hour", "source"),
    )
    op.create_index(
        "ix_sales_hourly_daily_org_day",
        "sales_hourly_daily",
        ["organization_id", "day"],
    )


def downgrade() -> None:
    op.drop_index("ix_sales_hourly_daily_org_day", table_name="sales_hourly_daily")
    op.drop_table("sales_hourly_daily")
    op.drop_column("daily_org_stats", "focus_completed_count")
    op.drop_column("daily_org_stats", "recovered_kzt")
