"""DailyOrgStats table — event-driven daily aggregates (Phase 2.3)

Revision ID: 20260518_daily_org_stats
Revises: 20260518_org_max_discount
Create Date: 2026-05-18
"""
from __future__ import annotations
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "20260518_daily_org_stats"
down_revision: Union[str, None] = "20260518_org_max_discount"
branch_labels: Sequence[str] | None = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "daily_org_stats",
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("orders_created", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("orders_confirmed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("orders_cancelled", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("bookings_confirmed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("bookings_cancelled", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("escalations", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("operator_takeovers", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("organization_id", "day"),
    )
    op.create_index(
        "ix_daily_org_stats_org_day",
        "daily_org_stats",
        ["organization_id", "day"],
    )


def downgrade() -> None:
    op.drop_index("ix_daily_org_stats_org_day", table_name="daily_org_stats")
    op.drop_table("daily_org_stats")
