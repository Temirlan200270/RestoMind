"""Add meta_json to staff_users for Manager assigned_org_ids

Revision ID: 20260519_staff_meta_json
Revises: 20260518_daily_org_stats_v2
Create Date: 2026-05-19
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260519_staff_meta_json"
down_revision: Union[str, None] = "20260518_daily_org_stats_v2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "staff_users",
        sa.Column("meta_json", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("staff_users", "meta_json")
