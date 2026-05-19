"""Add is_network flag to tenants (Franchise Phase 1 OS)

Revision ID: 20260518_tenant_is_network
Revises: 20260518_daily_org_stats
Create Date: 2026-05-18
"""
from __future__ import annotations
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "20260518_tenant_is_network"
down_revision: Union[str, None] = "20260518_daily_org_stats"
branch_labels: Sequence[str] | None = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column(
            "is_network",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            comment="Phase 1 OS: True = сеть/франшиза — Branch Switcher и сетевая аналитика",
        ),
    )


def downgrade() -> None:
    op.drop_column("tenants", "is_network")
