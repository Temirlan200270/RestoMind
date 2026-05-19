"""Add bookings_created, payments_completed/failed, revenue_kzt to daily_org_stats

Revision ID: 20260518_daily_org_stats_v2
Revises: 20260518_tenant_is_network
Create Date: 2026-05-18
"""
from __future__ import annotations
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "20260518_daily_org_stats_v2"
down_revision: Union[str, None] = "20260518_tenant_is_network"
branch_labels: Sequence[str] | None = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for col_name, col_type, default in [
        ("bookings_created", sa.Integer(), "0"),
        ("payments_completed", sa.Integer(), "0"),
        ("payments_failed", sa.Integer(), "0"),
        ("revenue_kzt", sa.Numeric(14, 2), "0"),
    ]:
        op.add_column(
            "daily_org_stats",
            sa.Column(col_name, col_type, nullable=False, server_default=sa.text(default)),
        )


def downgrade() -> None:
    for col_name in ("revenue_kzt", "payments_failed", "payments_completed", "bookings_created"):
        op.drop_column("daily_org_stats", col_name)
